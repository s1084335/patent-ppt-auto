"""主題標籤／摘要 headless CLI runner（ai:topic_label 任務的核心）。

用途：把正式 topic version 的主題名，從 c-TF-IDF 關鍵詞拼接（現況如
"unit / said / second"）換成人看得懂的中文主題名與短摘要。

🔴 核心規則（使用者定案，見 decisions.md「AI 標籤／摘要——關鍵字不得傳給 CLI」）：

1. 「文檔」＝c-TF-IDF 衡量出的每主題前 N 筆代表性專利。挑選由分群引擎完成
   （`clustering/model.py` 的 `rank_ctfidf_representative_documents`，用 c-TF-IDF 向量
   cosine similarity，**不是** topic probability），結果已存在 topic_state_json 的
   `representative_patent_ids`。本模組**直接讀既有欄位、不重算排序**，避免與引擎不一致。

2. **c-TF-IDF keywords 絕對不得傳給 CLI**。要區分兩件事：c-TF-IDF 是「挑哪幾筆」的方法
   （引擎內部用，照用其結果）；c-TF-IDF keywords 是「關鍵詞內容」，一律不得進 payload、
   prompt 或任何 CLI 看得到的地方——包含 keywords 欄位、關鍵詞拼接的舊 label，以及任何由
   keywords 衍生的提示文字。理由：給了關鍵字，LLM 會覆述關鍵詞而非閱讀專利內容命名，
   產出等同現況那些看不懂的主題名。`_strip_cli_visible_fields` 是這條紅線的執行點。

3. CLI 產出是**草稿**：一律以 `source='llm'` 回填，CLI 自稱 manual 也會被改回 llm。
   人工命名（`label_source='manual'`）由引擎的 `apply_topic_labels` guard 保護，不被覆蓋。

設計沿用 `ai_narrative_runner`：CLI 呼叫抽成可注入的 `cli_runner`（測試餵 fake，不跑二進位）、
`cli_kind`／`model` 由任務 payload 帶、指令組裝集中在 `build_cli_command`。

落點：不新增 table、不新增欄位——標籤寫回 topic_state_json 的 topics 元素既有
`label`／`summary`／`label_source` 欄，經 clustering 既有 `apply_topic_labels`（本輪不改該檔）。
"""

from __future__ import annotations

import json
from typing import Any, Callable

import functools

from .cli_gateway import (
    DEFAULT_CLI_TIMEOUT_SECONDS,
    CliResult,
    CliRunner,
    READ_ONLY_TOOLS,
    parse_cli_result,
    run_cli,
)
from .cli_gateway import build_cli_command as _gw_build_cli_command
from .ai_payload_file import extract_json_payload


# 標籤流程版本；隨 prompt 契約升版而變，寫進結果供追溯。
# v2（2026-07-27）：summary 字數由「20 到 40／上限 80」放寬到「40 到 50／上限 100」；
# 同版起資料改走檔案（ai_payload_file）而非命令列，並支援分批與批間帶已用名稱。
PROMPT_VERSION = "topic_label_v2"

# 每個 topic 給 LLM 的代表性專利上限。與 clustering 引擎的 TOPIC_LABELING_DOC_LIMIT 同值；
# 此處另存一份常數，是為了讓 worker 端不必在匯入期就拉進整個 clustering 相依（延遲載入）。
TOPIC_LABELING_DOC_LIMIT = 5

# AI 產出一律為草稿來源；manual 只能由前端 rename endpoint 寫入，AI 通道不得自我升級。
AI_LABEL_SOURCE = "llm"

# 🔴 紅線黑名單：這些鍵一旦出現在送往 CLI 的資料中即為違規，組 payload 時直接剔除。
# c-TF-IDF keywords 內容、關鍵詞拼接的舊 label 都不得讓 CLI 看到。
_CLI_FORBIDDEN_KEYS = frozenset(
    {"keywords", "keywords_json", "topic_keywords", "terms", "label", "summary"}
)


class TopicLabelRunnerError(RuntimeError):
    """主題標籤流程失敗（CLI 產出不合契約、topic_code 不存在或無任何標籤）。"""


# ⚠ 等級由「沿用敘述線 tail_args」收斂為唯讀檔（2026-08-09）：主路徑早已走
# 資料檔（build_cli_command_with_payload，READ_ONLY），這支 legacy 包裝繼承
# 敘述線等級只是歷史殘留——敘述線加上 MCP 取證後再沿用即為擴權。
build_cli_command = functools.partial(_gw_build_cli_command, tools=READ_ONLY_TOOLS)


def _strip_cli_visible_fields(node: Any) -> Any:
    """遞迴剔除所有 CLI 不該看到的欄位（紅線執行點）。

    刻意採「黑名單剔除」而非「白名單挑選」的補強層：引擎 payload 目前已不含 keywords，
    但萬一上游改動把 keywords 或舊 label 加回來，這層仍會擋掉，不讓關鍵詞流進 prompt。
    """
    if isinstance(node, dict):
        return {
            key: _strip_cli_visible_fields(value)
            for key, value in node.items()
            if str(key).lower() not in _CLI_FORBIDDEN_KEYS
        }
    if isinstance(node, list):
        return [_strip_cli_visible_fields(item) for item in node]
    return node


def build_topic_label_payload(
    *,
    workspace_id: int,
    source_field: str,
    topic_keys: list[str] | None = None,
    payload_builder: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """組出要給 CLI 的主題標籤 payload（每個 topic 帶其代表性專利文檔）。

    效率：payload_builder **一次批次**取回所有 topic 的代表專利文檔（單次 DB 連線、
    每個通道一次查詢），不是每個 topic 查一次。

    payload_builder 未注入時延遲載入 clustering 的 topic_labeling_payload——延遲載入
    避免 AI bridge 啟動就拉進整個分群引擎相依（bridge 跑在只有 CLI 的主機上）。

    回傳資料已過 `_strip_cli_visible_fields`，保證不含 keywords 與舊 label。
    """
    if payload_builder is None:
        from backend.app.clustering.workspace_service import topic_labeling_payload

        payload_builder = topic_labeling_payload

    raw = payload_builder(
        workspace_id=int(workspace_id),
        source_field=source_field,
        topic_keys=list(topic_keys) if topic_keys else None,
    )
    payload = _strip_cli_visible_fields(raw)
    # 代表專利上限再收斂一次：引擎已切到 TOPIC_LABELING_DOC_LIMIT，這裡不放大、只保底。
    for topic in payload.get("topics", []):
        excerpts = topic.get("representative_patents") or []
        topic["representative_patents"] = list(excerpts)[:TOPIC_LABELING_DOC_LIMIT]
    return payload


def _sanitize_instruction(instruction: str) -> str:
    """移除引擎 instruction 中殘留的「keywords」字樣。

    引擎原句含「不要依賴 keywords」——語意上是禁令而非關鍵詞內容，但仍會把「keywords」
    這個概念送進 prompt，等同提示模型往關鍵詞方向想。紅線要求 CLI 完全看不到這個詞，
    故改寫成同義但不提關鍵詞的說法。
    """
    return instruction.replace("；不要依賴 keywords", "").replace("不要依賴 keywords", "").strip()


def build_prompt(payload: dict[str, Any]) -> str:
    """把 payload 組成 headless CLI 提示。

    提示只含：代表性專利的文檔內容、必要 metadata（workspace／source_field／run_id／
    topic_code）與輸出契約。**不含任何 keywords**——payload 已由
    `build_topic_label_payload` 剔除，引擎 instruction 內殘留的字樣也由
    `_sanitize_instruction` 移除；此處不另外拼接任何關鍵詞提示。
    """
    lines: list[str] = []
    for topic in payload.get("topics", []):
        lines.append(f"### topic_code: {topic['topic_code']}")
        excerpts = topic.get("representative_patents") or []
        for index, text in enumerate(excerpts, start=1):
            lines.append(f"  [代表專利 {index}] {text}")
        lines.append("")
    topics_block = "\n".join(lines).strip()

    return (
        "任務：為專利主題產生中文標籤與短摘要（系統派工、非互動、一次性）。\n\n"
        f"workspace_id={payload.get('workspace_id')} "
        f"source_field={payload.get('source_field')} "
        f"（{payload.get('source_label', '')}） run_id={payload.get('run_id')}\n\n"
        f"{_sanitize_instruction(str(payload.get('instruction', '')))}\n\n"
        "重要：下方每個 topic 只提供其代表性專利的文檔內容。請**逐篇閱讀專利內容**後歸納出\n"
        "該主題共同的技術/功效重點再命名；不要拼接高頻詞、不要覆述用字。\n\n"
        f"{topics_block}\n\n"
        "輸出契約：只輸出一個 JSON 物件，形狀為\n"
        '{"topics": [{"topic_code": "...", "label": "...", "summary": "..."}, ...]}\n'
        "topic_code 必須原樣取自上方清單，不得新增或改寫；不要輸出多餘說明文字。"
    )


def _extract_labels(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """從 headless CLI 的 JSON 輸出取出 topics 陣列。

    claude -p --output-format json 會把模型回覆包在 `result` 字串內，故先解外層再解內層；
    若 CLI 直接回契約形狀（如 opencode 或未來變更）也一併支援，不寫死單一形狀。
    """
    candidate: Any = parsed
    if "topics" not in candidate and isinstance(candidate.get("result"), str):
        text = candidate["result"].strip()
        # 取 JSON 收口在 ai_payload_file.extract_json_payload（2026-07-27 實機 9g）：
        # 原本只認「開頭就是 ```」，CLI 多一句開場白（「依契約輸出：」「以下為契約
        # 指定的 JSON 物件：」）就整段丟 json.loads 而炸——job 102 跑了 183 秒、
        # 第一批已落庫，仍因此整趟報 failed。共用函式容忍前後贅字，七支 runner 同一份。
        try:
            candidate = extract_json_payload(text)
        except ValueError as exc:
            raise TopicLabelRunnerError(str(exc)) from exc
    if isinstance(candidate, dict):
        topics = candidate.get("topics")
    else:
        topics = candidate
    if not isinstance(topics, list):
        raise TopicLabelRunnerError(f"CLI 輸出缺少 topics 陣列：{str(parsed)[:300]}")
    return [item for item in topics if isinstance(item, dict)]


def run_topic_label(
    *,
    workspace_id: int,
    source_field: str,
    topic_keys: list[str] | None = None,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: CliRunner | None = None,
    payload_builder: Callable[..., dict[str, Any]] | None = None,
    apply_labels: Callable[..., dict[str, int]] | None = None,
    updated_by: str = "ai-bridge",
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
    payload_root: Any = None,
) -> dict[str, Any]:
    """把主題標籤整條系統化：批次取代表文檔 → 組提示 → 呼叫 headless CLI → 驗收 → 回填。

    cli_runner／payload_builder／apply_labels 皆可注入，供測試以 fake 取代，不真跑 CLI、不碰 DB。
    回填走 clustering 既有 `apply_topic_labels`（本輪不改該檔）：AI 產出一律 `source='llm'`，
    引擎 guard 會跳過 `label_source='manual'` 的人工命名，因此人工定案不會被 AI 覆蓋。

    回傳：requested／updated 主題數、run_id 與 cli_kind／prompt_version 供追溯。
    """
    runner = cli_runner if cli_runner is not None else run_cli
    if apply_labels is None:
        from backend.app.clustering.workspace_service import apply_topic_labels

        apply_labels = apply_topic_labels

    payload = build_topic_label_payload(
        workspace_id=workspace_id,
        source_field=source_field,
        topic_keys=topic_keys,
        payload_builder=payload_builder,
    )
    known_codes = {str(topic["topic_code"]) for topic in payload.get("topics", [])}
    if not known_codes:
        raise TopicLabelRunnerError(
            f"workspace {workspace_id} / {source_field} 沒有可標籤的 active 主題"
        )
    if progress is not None:
        progress("cli_running", 30)

    # ── 資料檔＋分批（2026-07-27）────────────────────────────────────
    # 舊版把整段 prompt（含 10 主題×5 篇獨立項全文，實測 128,101 字元）塞進命令列，
    # 在本機 Companion（Windows，CreateProcess 上限 32,767）必然 WinError 206。
    # 改為資料落檔、CLI 以 Read 讀取；並依字元預算分批，避免單次超出 AI context。
    from . import ai_payload_file as pf

    pf.cleanup_old_payloads(root=payload_root)
    topics = list(payload.get("topics") or [])
    batches = pf.split_into_batches(topics, max_chars=pf.MAX_PAYLOAD_CHARS)

    labels: list[dict[str, Any]] = []
    used_labels: list[str] = []   # 解法 A：把已命名的名稱帶進後續批次，避免撞名
    for index, batch in enumerate(batches, start=1):
        batch_payload = {
            "workspace_id": payload.get("workspace_id"),
            "source_field": payload.get("source_field"),
            "run_id": payload.get("run_id"),
            "instruction": _sanitize_instruction(str(payload.get("instruction", ""))),
            "guidance": (
                "逐篇閱讀每個 topic 的代表專利內容後，歸納該主題共同的技術/功效重點再命名；"
                "不要拼接高頻詞、不要覆述用字。"
            ),
            "output_contract": {
                "topics": [{"topic_code": "原樣取自本檔 topics", "label": "", "summary": ""}]
            },
            "topics": batch,
        }
        # 分批後 AI 看不到其他批的主題，可能取出重複名稱；把已用過的名字（僅名稱、
        # 不含文檔，長度極短）帶進後續批次，讓它主動區隔。
        if used_labels:
            batch_payload["already_used_labels"] = list(used_labels)
            batch_payload["avoid_duplicate_hint"] = (
                "already_used_labels 是先前批次已採用的主題名稱，"
                "本批命名請避免與之重複或高度相似。"
            )
        path = pf.write_payload_file(
            "topic_label", batch_payload, root=payload_root,
            run_id=payload.get("run_id"),
            label=f"ws{workspace_id}_{source_field}_b{index:02d}",
        )
        argv = pf.build_cli_command_with_payload(
            cli_kind,
            instruction="任務：為專利主題產生中文標籤與短摘要（系統派工、非互動、一次性）。",
            payload_path=path,
            model=model,
        )
        parsed = parse_cli_result(runner(argv, timeout_seconds))
        for item in _extract_labels(parsed):
            topic_code = str(item.get("topic_code") or "").strip()
            if topic_code not in known_codes:
                # 幻覺 topic_code 直接失敗，不把不存在的主題標籤寫進正式 state。
                raise TopicLabelRunnerError(
                    f"CLI 產出未知 topic_code：{topic_code!r}（可用：{sorted(known_codes)}）"
                )
            label_text = str(item.get("label") or "").strip()
            labels.append({
                "topic_code": topic_code,
                "label": label_text,
                "summary": str(item.get("summary") or "").strip(),
                # 🔴 label guard：無論 CLI 自稱什麼，AI 通道一律 llm（草稿），不得自升 manual。
                "source": AI_LABEL_SOURCE,
            })
            if label_text:
                used_labels.append(label_text)

    if progress is not None:
        progress("cli_running", 85)
    if not labels:
        raise TopicLabelRunnerError("CLI 正常結束但未產出任何主題標籤")

    applied = apply_labels(
        workspace_id=int(workspace_id),
        source_field=source_field,
        labels=labels,
        updated_by=updated_by,
    )
    return {
        "workspace_id": int(workspace_id),
        "source_field": source_field,
        "run_id": payload.get("run_id"),
        "topics_requested": len(labels),
        "topics_updated": int(applied.get("updated_count", 0)),
        "cli_kind": cli_kind,
        "prompt_version": PROMPT_VERSION,
    }
