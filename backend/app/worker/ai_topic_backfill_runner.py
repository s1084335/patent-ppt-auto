"""技術通道 AI 補分 runner（ai:topic_backfill 任務核心，CLU-014）。

規格唯一來源：openspec change `add-technical-channel-ai-backfill`。

三段式的第二段：對補分候選（該通道無 embeddings、非設計案、未指派）產
「建議主題＋一句理由」。建議是**敘述型輔助**——隨 job result 落 `app_layer.workflow_outputs`
（output_type='job_result:ai:topic_backfill'，job 框架 complete_job 自動存；
⚠ 2026-08-07 現實回寫：analysis_outputs 是 legacy_0021 空表，非現行落點），
不碰 `topic_assignments`；
正式指派由第三段（使用者批次核准）的確定性程式寫入。

守則：
- 建議主題**只能從該通道現有主題清單選**；清單外＝標 invalid 現形，
  不靜默丟棄、不自創主題。
- CLI 少回或多回：少回的候選以 valid=False 佔位現形；回了不存在的
  patent_id 直接 fail loud（寧可整批重跑，不吞錯位建議）。
- 無候選＝不呼叫 CLI、不落 output（誠實回 0）。

cli_runner／candidate_fetcher／topics_fetcher／persister 皆可注入，
供測試以 fake 取代；正式路徑由 ai_bridge 帶預設實作（真 SQL＋真 CLI）。
"""
from __future__ import annotations

import json
from typing import Any, Callable

# 版本隨 prompt 契約升版而變，寫進 analysis_outputs 供追溯。
PROMPT_VERSION = "topic_backfill_v1"

DEFAULT_CLI_TIMEOUT_SECONDS = 600.0


class TopicBackfillError(RuntimeError):
    """補分流程失敗（CLI 產出不合契約、回吐未知 patent_id 等）。"""


def build_prompt(candidates: list[dict[str, Any]], topics: list[dict[str, Any]]) -> str:
    """組 headless CLI 提示：候選文本＋主題選單＋輸出契約。"""
    topic_lines = "\n".join(
        f"- {t['topic_key']}：{t.get('label') or ''}｜{t.get('summary') or ''}"
        for t in topics
    )
    cand_lines = "\n".join(
        f"### {c['patent_id']}｜{c.get('patent_number') or ''}｜{c.get('title') or ''}\n"
        f"{c.get('input_text') or ''}"
        for c in candidates
    )
    return (
        "任務：技術主題補分建議（系統派工、非互動、一次性）。\n\n"
        "下列專利因無獨立項而未進技術分群。請依每件的文獻內容，從「現有主題清單」\n"
        "為每件建議一個主題，並各附一句理由（為什麼歸這個主題）。\n\n"
        f"## 現有主題清單（建議**只能**從這裡選 topic_key，不得自創）\n{topic_lines}\n\n"
        f"## 待補分專利\n{cand_lines}\n\n"
        "## 輸出（只輸出這個 JSON，不加其他文字）\n"
        '{"suggestions": [{"patent_id": <int>, "suggested_topic_key": "<key>", '
        '"reason": "<一句理由>"}]}\n'
        "每件都要有一筆；判斷不了也要選最接近的主題並在理由說明不確定性。"
    )


def _parse_reply(raw: str) -> list[dict[str, Any]]:
    """解析 CLI 回覆的 JSON；容忍前後雜訊（取第一個 { 到最後一個 }）。"""
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise TopicBackfillError(f"CLI 回覆非 JSON：{text[:200]!r}")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise TopicBackfillError(f"CLI JSON 解析失敗：{exc}") from exc
    rows = data.get("suggestions")
    if not isinstance(rows, list):
        raise TopicBackfillError("CLI 回覆缺 suggestions 陣列")
    return rows


def run_topic_backfill(
    *,
    workspace_id: int,
    source_field: str,
    candidate_fetcher: Callable[[], list[dict[str, Any]]],
    topics_fetcher: Callable[[], list[dict[str, Any]]],
    cli_runner: Callable[..., str],
    persister: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ai_model: str | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """候選 → prompt → CLI → 驗收 → 落 analysis_outputs。回傳統計摘要。"""
    def _tick(stage: str, percent: int) -> None:
        if progress:
            progress(stage, percent)

    _tick("取補分候選", 20)
    candidates = candidate_fetcher()
    if not candidates:
        return {"candidates": 0, "suggested": 0, "invalid": 0, "workspace_id": workspace_id,
                "source_field": source_field, "suggestions": []}

    topics = topics_fetcher()
    if not topics:
        raise TopicBackfillError("該通道尚無主題清單——先完成分群才有選單可補分")
    known_keys = {t["topic_key"] for t in topics}
    by_pid = {int(c["patent_id"]): c for c in candidates}

    _tick("CLI 建議產生中", 40)
    raw = cli_runner(build_prompt(candidates, topics), timeout_seconds=timeout_seconds)
    rows = _parse_reply(raw)

    _tick("驗收建議", 80)
    suggestions: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        pid = int(row.get("patent_id", -1))
        if pid not in by_pid:
            raise TopicBackfillError(f"CLI 回吐未知 patent_id={pid}——建議可能錯位，整批作廢")
        seen.add(pid)
        key = str(row.get("suggested_topic_key") or "")
        valid = key in known_keys
        suggestions.append({
            "patent_id": pid,
            "patent_number": by_pid[pid].get("patent_number"),
            "title": by_pid[pid].get("title"),
            "suggested_topic_key": key,
            "reason": str(row.get("reason") or ""),
            "valid": valid,
            **({} if valid else {"invalid_reason": f"建議主題 {key!r} 不在現有主題清單"}),
        })
    # CLI 少回的候選：佔位現形，不得整批當成功。
    for pid, cand in by_pid.items():
        if pid not in seen:
            suggestions.append({
                "patent_id": pid,
                "patent_number": cand.get("patent_number"),
                "title": cand.get("title"),
                "suggested_topic_key": "",
                "reason": "",
                "valid": False,
                "invalid_reason": "CLI 未回覆此件建議",
            })
    suggestions.sort(key=lambda s: s["patent_id"])

    invalid = sum(1 for s in suggestions if not s["valid"])
    _tick("回存建議", 90)
    result = {
        "candidates": len(candidates),
        "suggested": len(suggestions) - invalid,
        "invalid": invalid,
        "workspace_id": workspace_id,
        "source_field": source_field,
        "prompt_version": PROMPT_VERSION,
        "ai_model": ai_model,
        # 建議本體隨 job result 進 workflow_outputs（complete_job 自動存）。
        "suggestions": suggestions,
    }
    if persister is not None:
        extra = persister(result)
        if extra:
            result.update(extra)
    return result
