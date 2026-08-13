"""候選方案 AI 輔助說明 headless CLI runner（ai:candidate_explanation 任務的核心）。

用途：分群 calibrate 完成、候選主題數方案產出後，把三組候選的指標
（coherence／diversity／balance／score／k／document_count）交給 headless CLI，
產出「各方案取捨怎麼看」的一段中文說明，寫回 topic_state_json->'candidates' 的
`llm_explanation`。AI 只解釋指標意義輔助使用者判斷，不替使用者選、不評價專利內容。

使用者裁決（讀法一，2026-07-24）：**現在做 B**——直呼與現役三支 AI job
（ai:topic_label／ai:patent_note／ai:narrative）一致的「runner 直呼 domain 函式 ＋
CLI 只吃內嵌 prompt」路徑，能立刻運作；但**寫成為 MCP 預留**：取指標與寫回封成
兩個明確、可注入的呼叫點（`fetch_payload`／`write_explanations`，預設即既有兩支
domain 函式）。未來安裝包階段把 headless CLI 掛 MCP 機制建好時，切換是「換入口」
（把 fetch/write 換成經 MCP 呼叫 tools_clustering.xxx）——底層同一份 domain 函式、
業務邏輯不變，不是重寫。

🔴 紅線（維持不變，不在此重造）：
- 取指標一律走既有 `candidate_review_payload(run_id)`：它已 pop CANDIDATE_REFERENCE_PARAMETER_KEY，
  instruction 明寫「不要要求或引用代表文檔」，AI 只看指標。**不得**傳專利內容／keywords／
  c-TF-IDF refs 給 CLI。
- prompt **沿用** payload 內既有 `instruction`（來自 workspace_service），不另寫一份口徑。
- 寫回一律走既有 `apply_candidate_explanations(run_id=, explanations=)`：它守住空白／超長／
  未知 candidate_id 的驗證，只存說明不代選案。

設計沿用 ai_topic_label_runner／ai_patent_note_runner：CLI 呼叫抽成可注入的 `cli_runner`
（測試餵 fake，不跑二進位、不燒 token），指令組裝共用 cli_gateway.build_cli_command
（本線宣告 NO_TOOLS：prompt 資料內嵌，不需要任何工具）。
輸出契約：{"explanations":[{"candidate_id":...,"explanation":...}]}；繁體中文。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from backend.app.clustering import workspace_service

import functools

from .cli_gateway import (
    DEFAULT_CLI_TIMEOUT_SECONDS,
    CliRunner,
    NO_TOOLS,
    parse_cli_result,
    run_cli,
)
from .ai_payload_file import extract_json_payload
from .cli_gateway import build_cli_command as _gw_build_cli_command

# 🔴 最小權限（2026-08-13 修正擴權）：本任務 prompt 由 build_prompt 把候選指標
# 全部內嵌，不讀檔、不寫檔、不上網、不查 DB——白名單為空。
# ⚠ 原本從 `ai_narrative_runner` 借 build_cli_command，那是
# `partial(tools=RESEARCH_TOOLS)`，於是本線靜默拿到 Read/Glob/Grep/Write ＋
# 八支 MCP 取證工具＋`--mcp-config`。借共用符號時要連權限一起看，不是只看名字對。
build_cli_command = functools.partial(_gw_build_cli_command, tools=NO_TOOLS)


# 候選說明流程版本；隨 prompt 契約升版而變，寫進結果供追溯。
# v2（2026-07-27）：instruction 口徑改為對齊 decisions.md 2026-07-17——
# 禁止把小數分數當主內容、要求翻成語意原因、明說切分程度/穩定性/風險三面向，
# 並移除寫死的「三組候選」（組數依資料量而定）。
PROMPT_VERSION = "candidate_explanation_v2"

# ⚠ MCP 預留的兩個抽換點（讀法一核心）。
# 現在：直接指向既有 domain 函式（與現役三支 AI job 一致，能立刻運作）。
# 未來（安裝包階段全線一起切）：把 run_candidate_explanation 呼叫端傳入的
# fetch_payload／write_explanations 換成「經 headless CLI + MCP 呼叫 tools_clustering
# 的 get_candidate_review_payload／apply_candidate_explanations」——那兩支 MCP 工具與
# 這裡用的是**同一份 domain 函式**（mcp_server/tools_clustering.py 為薄包），故只換入口、
# 底層業務邏輯不變。這裡刻意不建 MCP 驅動（那不是本階段的事），只把呼叫點封成可注入。
default_fetch_payload: Callable[[int], dict[str, Any]] = workspace_service.candidate_review_payload
default_write_explanations = workspace_service.apply_candidate_explanations


class CandidateExplanationRunnerError(RuntimeError):
    """候選說明流程失敗（CLI 產出不合契約、回吐未知 candidate_id 等）。"""


def build_prompt(payload: dict[str, Any]) -> str:
    """把候選指標 payload 組成 headless CLI 提示。

    🔴 只帶指標與既有 instruction，絕不夾帶專利內容／keywords／c-TF-IDF refs——
    payload 由 candidate_review_payload 產生，已在來源守住（pop refs、instruction 明寫
    不引用代表文檔）。此處 prompt **沿用** payload['instruction']（不另寫口徑），
    另附每組候選的六項指標供 AI 依據。
    """
    instruction = str(payload.get("instruction") or "").strip()
    # 只挑指標欄位進 prompt，逐一列出；不把整份 candidate dict 塞進去，避免未來若 payload
    # 夾帶其他鍵（如 parameters 內殘留物）連帶外洩。
    lines: list[str] = []
    for candidate in payload.get("candidates") or []:
        lines.append(
            "candidate_id={cid}｜方案類型={ctype}｜主題數 k={k}｜"
            "coherence={coh}｜diversity={div}｜balance={bal}｜score={score}".format(
                cid=candidate.get("candidate_id"),
                ctype=candidate.get("candidate_type"),
                k=candidate.get("k"),
                coh=candidate.get("coherence"),
                div=candidate.get("diversity"),
                bal=candidate.get("balance"),
                score=candidate.get("score"),
            )
        )
    metrics_block = "\n".join(lines)
    document_count = payload.get("document_count")

    return (
        "任務：為分群候選主題數方案產生取捨說明（系統派工、非互動、一次性）。\n"
        "AI 角色：只解釋各方案指標的取捨意義，輔助使用者判斷；不替使用者選定方案、"
        "不評價任何專利內容。\n\n"
        f"資料量（document_count）：{document_count}\n\n"
        "候選方案指標：\n"
        f"{metrics_block}\n\n"
        f"{instruction}\n\n"
        "輸出契約：只輸出一個 JSON 物件，形狀為\n"
        '{"explanations": [{"candidate_id": 1, "explanation": "..."}, ...]}\n'
        "candidate_id 必須原樣取自上方清單，不得新增、改寫或遺漏；一律繁體中文；"
        "不要輸出多餘說明文字。"
    )


def _extract_explanations(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """從 headless CLI 的 JSON 輸出取出 explanations 陣列。

    `claude -p --output-format json` 會把模型回覆包在 `result` 字串內，故先解外層再解內層；
    CLI 直接回契約形狀者（如 opencode 或未來變更）也一併支援，不寫死單一形狀。
    """
    candidate: Any = parsed
    if "explanations" not in candidate and isinstance(candidate.get("result"), str):
        text = candidate["result"].strip()
        # 取 JSON 收口在 ai_payload_file.extract_json_payload（2026-07-27 實機 9g）：
        # 原本只認「開頭就是 ```」，CLI 多一句開場白（「依契約輸出：」「以下為契約
        # 指定的 JSON 物件：」）就整段丟 json.loads 而炸——job 102 跑了 183 秒、
        # 第一批已落庫，仍因此整趟報 failed。共用函式容忍前後贅字，七支 runner 同一份。
        try:
            candidate = extract_json_payload(text)
        except ValueError as exc:
            raise CandidateExplanationRunnerError(str(exc)) from exc
    explanations = candidate.get("explanations") if isinstance(candidate, dict) else candidate
    if not isinstance(explanations, list):
        raise CandidateExplanationRunnerError(
            f"CLI 輸出缺少 explanations 陣列：{str(parsed)[:300]}")
    return [item for item in explanations if isinstance(item, dict)]


def run_candidate_explanation(
    *,
    run_id: int,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: CliRunner | None = None,
    # ⚠ MCP 預留的兩個抽換點：預設即既有 domain 函式，未來換 MCP 只換這兩個入口。
    fetch_payload: Callable[[int], dict[str, Any]] | None = None,
    write_explanations: Callable[..., dict[str, int]] | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """整條候選說明流程：取指標 → 內嵌 prompt 呼 CLI → 解析 → 寫回。

    ⚠ 取指標與寫回是兩個**明確、可抽換的呼叫點**（fetch_payload／write_explanations）：
    - 現在（B 做法）：預設直呼既有 domain 函式 candidate_review_payload /
      apply_candidate_explanations（與現役三支 AI job 一致，立刻運作）。
    - 未來（安裝包階段全線切 MCP）：只把這兩個入口換成經 MCP 呼叫 tools_clustering 的
      同名工具（底層同一份 domain 函式），業務邏輯不變。

    cli_runner 可注入供測試以 fake 取代，不真跑 CLI、不燒 token。
    progress(stage, percent) 供 AI 執行期間 0→100 緩進（AI 任務無內部百分比）。
    """
    fetch = fetch_payload if fetch_payload is not None else default_fetch_payload
    write = write_explanations if write_explanations is not None else default_write_explanations
    runner = cli_runner if cli_runner is not None else None

    if progress is not None:
        progress("讀取候選方案指標", 10)
    # ── 抽換點 1：取指標（現＝直呼 candidate_review_payload；未來＝MCP get_candidate_review_payload）
    payload = fetch(run_id)
    candidates = payload.get("candidates") or []
    if not candidates:
        # 沒有候選＝沒東西可解釋，不呼叫 CLI、不寫回（不空燒 token）。
        if progress is not None:
            progress("無候選方案可說明", 100)
        return {
            "run_id": run_id,
            "candidates": 0,
            "explanations_written": 0,
            "prompt_version": PROMPT_VERSION,
            "cli_kind": cli_kind,
        }

    if progress is not None:
        progress("AI 產生候選方案說明中", 40)
    prompt = build_prompt(payload)
    argv = build_cli_command(cli_kind, prompt, model=model)
    # runner 為 None 時走共用 gateway 的 subprocess 執行器；測試可注入 fake。
    if runner is None:
        runner = run_cli
    parsed = parse_cli_result(runner(argv, timeout_seconds))

    known_ids = {int(c["candidate_id"]) for c in candidates}
    explanations: list[dict[str, Any]] = []
    for item in _extract_explanations(parsed):
        try:
            candidate_id = int(item.get("candidate_id"))
        except (TypeError, ValueError) as exc:
            raise CandidateExplanationRunnerError(
                f"CLI 產出 candidate_id 非整數：{item.get('candidate_id')!r}"
            ) from exc
        if candidate_id not in known_ids:
            # 幻覺 candidate_id 直接失敗，不把不屬於此 run 的說明寫進資料。
            raise CandidateExplanationRunnerError(
                f"CLI 產出未知 candidate_id：{candidate_id}（本 run：{sorted(known_ids)}）"
            )
        explanation = str(item.get("explanation") or "").strip()
        if not explanation:
            continue
        explanations.append({"candidate_id": candidate_id, "explanation": explanation})

    if progress is not None:
        progress("寫回候選方案說明", 90)
    # ── 抽換點 2：寫回（現＝直呼 apply_candidate_explanations；未來＝MCP apply_candidate_explanations）
    written = 0
    if explanations:
        result = write(run_id=run_id, explanations=explanations)
        written = int(result.get("updated_count", 0))

    if progress is not None:
        progress(f"候選方案說明完成（共 {written} 組）", 100)
    return {
        "run_id": run_id,
        "candidates": len(candidates),
        "explanations_written": written,
        "prompt_version": PROMPT_VERSION,
        "cli_kind": cli_kind,
    }
