"""分群模組的 LLM adapter。

這個檔案只負責把「候選方案說明」與「主題標籤/摘要」包成穩定介面。
分群、topic assignment、合併與資料庫寫入仍由 runner/workspace_service 控制，
避免 LLM 直接決定分類結果。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from typing import Any


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMConfig:
    """集中管理分群 LLM provider、模型與生成參數。"""

    provider: str = "huggingface"
    model: str = "Qwen/Qwen3-8B:nscale"
    max_tokens: int = 1600
    temperature: float = 0.1

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """從環境變數讀取設定；沒有 token 時仍可走 fallback。"""
        return cls(
            provider=os.getenv("CLUSTERING_LLM_PROVIDER", "huggingface").strip(),
            model=os.getenv("CLUSTERING_LLM_MODEL", "Qwen/Qwen3-8B:nscale").strip(),
            max_tokens=int(os.getenv("CLUSTERING_LLM_MAX_TOKENS", "1600")),
            temperature=float(os.getenv("CLUSTERING_LLM_TEMPERATURE", "0.1")),
        )


class ClusteringLLM:
    """供分群流程呼叫的 LLM 閘口，失敗時回到可預期的 fallback 文案。"""

    def __init__(self, config: LLMConfig | None = None) -> None:
        """初始化 provider 設定；實際 client 採延遲載入。"""
        self.config = config or LLMConfig.from_env()
        self.token = os.getenv("HF_TOKEN", "").strip()

    @property
    def available(self) -> bool:
        """確認目前是否能呼叫 Hugging Face Inference API。"""
        return self.config.provider == "huggingface" and bool(self.token)

    def explain_candidates(
        self,
        *,
        source_label: str,
        document_count: int,
        candidates: list[dict[str, Any]],
    ) -> dict[int, str]:
        """替三組候選主題數產生短說明，供使用者選定方案。"""
        if not self.available:
            return {
                int(item["candidate_id"]): self._fallback_candidate_explanation(item)
                for item in candidates
            }

        prompt = f"""
/no_think
你是專利分類產品的分析助理。請用繁體中文解釋下列分群候選方案的差異。

資料來源：{source_label}
文件數：{document_count}

要求：
1. 每個 candidate_id 回傳一段 35 到 70 字的說明。
2. 說明要協助使用者理解保守、平衡、細分的差異。
3. 不要只重複 score，也不要替使用者直接定案。
4. 只回傳 JSON array，格式：
[{{"candidate_id": 1, "explanation": "..."}}]

候選方案：
{json.dumps(candidates, ensure_ascii=False)}
""".strip()
        try:
            payload = self._chat_json(prompt)
        except Exception as exc:
            LOGGER.warning("Clustering LLM candidate explanation failed: %s", exc)
            return {
                int(item["candidate_id"]): self._fallback_candidate_explanation(item)
                for item in candidates
            }

        explanations = {
            int(item["candidate_id"]): str(item["explanation"]).strip()
            for item in payload
            if "candidate_id" in item and "explanation" in item
        }
        for item in candidates:
            candidate_id = int(item["candidate_id"])
            explanations.setdefault(candidate_id, self._fallback_candidate_explanation(item))
        return explanations

    def label_topics(self, topics: list[dict[str, Any]]) -> dict[int, dict[str, str]]:
        """依主題 keywords 與前五筆代表性專利產生中文標籤與摘要。"""
        if not topics:
            return {}
        if not self.available:
            return {
                int(item["topic_id"]): self._fallback_topic_result(item)
                for item in topics
            }

        results: dict[int, dict[str, str]] = {}
        for start in range(0, len(topics), 5):
            batch = topics[start : start + 5]
            prompt = f"""
/no_think
你是專利技術分類助理。請根據每個 topic 的 keywords 與代表性專利節錄，
為每個 topic 產生繁體中文標籤與摘要。

要求：
1. label：8 到 14 個中文字，描述技術或功效主軸。
2. summary：50 到 100 個中文字，說明這群專利共同的技術重點或功效。
3. 不要改變 topic_id，不要新增不存在的 topic。
4. 只回傳 JSON array，格式：
[{{"topic_id": 1, "label": "...", "summary": "..."}}]

topics：
{json.dumps(batch, ensure_ascii=False)}
""".strip()
            try:
                payload = self._chat_json(prompt)
            except Exception as exc:
                LOGGER.warning("Clustering LLM topic labeling failed: %s", exc)
                payload = []

            for item in payload:
                if not {"topic_id", "label", "summary"}.issubset(item):
                    continue
                results[int(item["topic_id"])] = {
                    "label": str(item["label"]).strip(),
                    "summary": str(item["summary"]).strip(),
                    "source": "llm",
                }

            # 每批都補 fallback，避免部分 topic 因 LLM 格式問題沒有結果。
            for item in batch:
                topic_id = int(item["topic_id"])
                results.setdefault(topic_id, self._fallback_topic_result(item))
        return results

    def _chat_json(self, prompt: str) -> list[dict[str, Any]]:
        """呼叫 Hugging Face chat completion 並解析 JSON array。"""
        from huggingface_hub import InferenceClient

        client = InferenceClient(api_key=self.token)
        response = client.chat_completion(
            model=self.config.model,
            messages=[
                {
                    "role": "system",
                    "content": "你只輸出有效 JSON，不要使用 Markdown code fence。",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )
        content = str(response.choices[0].message.content or "").strip()
        start = content.find("[")
        end = content.rfind("]")
        if start < 0 or end < start:
            raise ValueError("LLM response does not contain a JSON array")
        parsed = json.loads(content[start : end + 1])
        if not isinstance(parsed, list):
            raise ValueError("LLM JSON response must be an array")
        return [item for item in parsed if isinstance(item, dict)]

    @staticmethod
    def _fallback_candidate_explanation(candidate: dict[str, Any]) -> str:
        """沒有 LLM token 或呼叫失敗時，用指標產生可讀的候選說明。"""
        label = {
            "conservative": "保守方案",
            "balanced": "平衡方案",
            "detailed": "細分方案",
        }.get(str(candidate.get("candidate_type")), "候選方案")
        return (
            f"{label}使用 {candidate.get('k')} 個主題，"
            f"coherence {float(candidate.get('coherence', 0)):.3f}、"
            f"diversity {float(candidate.get('diversity', 0)):.3f}、"
            f"balance {float(candidate.get('balance', 0)):.3f}；"
            "可作為排序輔助，仍需人工檢視主題內容。"
        )

    @staticmethod
    def _fallback_topic_label(topic: dict[str, Any]) -> str:
        """用前三個 keyword 組成 fallback topic label。"""
        keywords = topic.get("keywords") or []
        terms = [str(item.get("term", "")) for item in keywords if isinstance(item, dict)]
        return " / ".join(term for term in terms[:3] if term) or "未命名主題"

    @classmethod
    def _fallback_topic_result(cls, topic: dict[str, Any]) -> dict[str, str]:
        """沒有 LLM 結果時，保留 keyword label 並標明摘要未生成。"""
        return {
            "label": cls._fallback_topic_label(topic),
            "summary": "LLM 尚未產生摘要；目前先依主題關鍵字作為標籤，等待人工檢視或重新執行標籤流程。",
            "source": "fallback",
        }
