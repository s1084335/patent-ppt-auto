"""不相干專利篩選 AI 判讀 headless CLI runner（ai:irrelevant_filter 任務的核心）。

用途：分群完成後，每主題取 c-TF-IDF **最低** N 筆（最不像該主題的候選，見
clustering/model.py:rank_ctfidf_least_representative_documents），讀其**文獻備註**交 headless
CLI 逐筆判讀（相干／可疑／不相干＋理由），輔助使用者決定是否剔除。AI 只標記與說明理由，
剔除與否一律由使用者逐筆決定（規格第 123-126 行）。

規格唯一來源：irrelevant-patent-filter-spec.md 第 25-121 行（c-TF-IDF 最低 N 筆方案）。

🔴 紅線（本檔嚴格執行）：
- **相似度分數與 keywords 絕不外流**：候選由 c-TF-IDF 分數挑出（挑哪 N 筆），但給 CLI 的
  prompt **只含專利文獻備註（＋可選主題 label）**，不含分數、不含 keywords。分數只在
  clustering 層挑候選時用，到本 runner 已只剩 (patent_id, note[, topic_label])。
- **各筆獨立判讀、不混批**（規格第 48-56 行）：批次呼叫可為效率把多筆放同一次 CLI 請求，
  但 prompt 明令「逐筆絕對判斷、不得以同批其他專利為基準、不得回傳排序或最差 N 筆」，
  輸出逐筆結果。此點與文獻備註線不同——那條每筆互不影響，本線須主動防止相對化。
- **主題 label 給、summary 不給**（2026-07-24 定案，第 1 題）：label＝主題人可讀顯示名，給 AI
  當「這件屬不屬於這個主題」的對照；label ≠ c-TF-IDF keywords，故可傳。summary 不給（AI 產物
  餵回 AI 會互相污染）。keywords／相似度分數仍為紅線，一律不傳。

⚠ **安全來自任務設計**（沿文獻備註／市場摘要線定案）：備註文字直接內嵌 prompt，CLI 不需
Read 檔案、不需連網即可完成，CLI 白名單為空（不開 WebSearch/WebFetch/Read/Glob/Grep/Write）。

設計沿 ai_market_summary_runner／ai_candidate_explanation_runner：cli_kind/model/cli_runner
可注入（測試餵 fake，不跑二進位、不燒 token）；fetch_notes／select_candidates 亦可注入，
供測試以 fake 取代，不真碰 DB／embeddings。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

import psycopg

# 排除表寫入收口在 clustering.exclusions（本 runner 不自己寫 SQL）。
# 模組層 import 而非函式內 import：讓 _persist_verdicts 的落庫行為可被測試替換。
from backend.app.clustering.exclusions import store_ai_verdicts
from backend.app.db.connection import get_connection_kwargs

from .ai_narrative_runner import (
    DEFAULT_CLI_TIMEOUT_SECONDS,
    CliRunner,
    parse_cli_result,
    _CLI_SPECS,
    _subprocess_cli_runner,
)
from .ai_payload_file import extract_json_payload


# 篩選流程版本；隨 prompt 契約升版而變，寫進結果供追溯。
PROMPT_VERSION = "irrelevant_filter_v1"

# 🔴 最小權限：備註已內嵌 prompt，CLI 不需讀檔/寫檔/上網——白名單為空。
_FILTER_TAIL_ARGS = ["--output-format", "json", "--allowedTools", ""]

# 批次筆數（規格第 118 行）：50 筆 × 約 100 字 ≈ 5,000 字/批，context 安全。
DEFAULT_BATCH_SIZE = 50

# 三分結果的合法值（規格第 113-116 行）；另有程式判定的「無法判斷」（備註為空）。
VALID_VERDICTS = ("相干", "可疑", "不相干")
UNDECIDABLE = "無法判斷"


class IrrelevantFilterRunnerError(RuntimeError):
    """不相干篩選流程失敗（CLI 產出不合契約等）。"""


def build_cli_command(cli_kind: str, prompt: str, *, model: str | None = None) -> list[str]:
    """組 headless argv；沿 ai_narrative_runner 的 CLI 對照表，但覆寫 tail_args 為空白名單。

    覆寫理由：文獻備註已內嵌 prompt，CLI **不需要任何工具**（不讀檔、不連網），白名單為空。
    opencode 等未提供工具白名單旗標的 CLI 沿用其原 tail_args。
    """
    spec = _CLI_SPECS.get(cli_kind)
    if spec is None:
        raise IrrelevantFilterRunnerError(
            f"未知 cli_kind：{cli_kind!r}（可用：{sorted(_CLI_SPECS)}）")
    model_args: list[str] = []
    if model:
        model_flag = spec.get("model_flag")
        if not model_flag:
            raise IrrelevantFilterRunnerError(f"{cli_kind!r} 不支援指定 model")
        model_args = [model_flag, model]
    tail = _FILTER_TAIL_ARGS if cli_kind == "claude" else list(spec["tail_args"])
    return [spec["binary"], spec["prompt_flag"], prompt, *model_args, *tail]


def build_prompt(candidates: Sequence[tuple[int, str]], topic_label: str | None = None) -> str:
    """把一批 (patent_id, 文獻備註) 組成 headless CLI 提示。

    🔴 只帶 patent_id、文獻備註與（可選）主題 label——絕不夾帶 c-TF-IDF 分數或 keywords。
    ⚠ topic_label（2026-07-24 定案，第 1 題）＝該批專利所屬主題的「人可讀顯示名」（如「鋸切結構」），
      給 AI 當「這件屬不屬於這個主題」的對照。label ≠ keywords：label 是主題名稱、非 c-TF-IDF 詞彙，
      故可傳；keywords／相似度分數仍為紅線、一律不傳。不給 topic_label 時不硬湊對照句（向後相容）。
    ⚠ 不給 summary（summary 為 AI 產物，餵回 AI 會互相污染）。
    ⚠ 逐筆獨立判斷的要求在此明寫，主動防止 AI 以同批其他專利為基準做相對排名。
    輸入的 candidates 應已濾除空備註（空備註由 runner 直接標『無法判斷』，不進 prompt）。
    """
    lines = [f"- patent_id={pid}｜文獻備註：{note}" for pid, note in candidates]
    docs_block = "\n".join(lines)

    # 主題對照句：僅在有 label 時加入；label 是主題顯示名，非 keywords（紅線不受影響）。
    label_line = (
        f"── 主題對照 ──\n這批專利被歸到主題「{topic_label}」。"
        "請判斷每一件依其文獻備註，是否真的屬於這個主題所代表的產品範疇。\n\n"
        if topic_label else ""
    )

    return (
        "任務：判讀以下每一件專利是否與本次分析的產品主題相干（系統派工、非互動、一次性）。\n"
        "AI 角色：只標記與說明理由，輔助使用者判斷；**剔除與否由使用者決定，你不做剔除決策**。\n\n"
        f"{label_line}"
        "── 判讀方式（最重要，務必遵守）──\n"
        "1. **逐筆各自獨立判斷**：對每一件專利，只依它自己的文獻備註做**絕對判斷**——這一件本身"
        "相不相干。\n"
        "2. ⚠ **不得以同批其他專利為基準**：不要比較同批哪幾件比較不像、不要回傳排序、"
        "不要挑出「這批中最差的 N 筆」。每件的結論不受同批其他件影響。\n"
        "3. 只有文獻備註可作依據；備註未提到的內容不臆測。\n\n"
        "── 判準：嚴格度『中』──\n"
        "以本次分析的產品主題為基準，**排除不同產品類別**者：\n"
        "- 明顯無關（如與產品領域完全不同的技術）→『不相干』。\n"
        "- 同一大領域但屬**不同產品類別**（例：主題為割草機，備註為吹葉機）→『不相干』。\n"
        "- 屬於同一產品類別、只是規格或型式差異 →『相干』。\n"
        "- 依備註無法明確歸類、把握不足 →『可疑』（交使用者重點檢視）。\n\n"
        "── 待判讀專利（每件獨立）──\n"
        f"{docs_block}\n\n"
        "── 輸出契約 ──\n"
        "只輸出一個 JSON 物件，形狀為\n"
        '{"results": [{"patent_id": 1, "verdict": "相干", "reason": "..."}, ...]}\n'
        "- patent_id 原樣取自上方清單，每件都要有一筆結果，不得新增、改寫或遺漏。\n"
        "- verdict 只能是「相干」「可疑」「不相干」三者之一。\n"
        "- reason＝該筆的判讀理由（繁體中文，簡短具體）。\n"
        "一律繁體中文；不要輸出多餘說明文字。"
    )


def _extract_results(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """從 headless CLI 的 JSON 輸出取出 results 陣列。

    `claude -p --output-format json` 把模型回覆包在 `result` 字串內，故先解外層再解內層；
    CLI 直接回契約形狀者（opencode 或未來變更）也一併支援，不寫死單一形狀。
    """
    candidate: Any = parsed
    if isinstance(candidate, dict) and "results" not in candidate \
            and isinstance(candidate.get("result"), str):
        text = candidate["result"].strip()
        # 取 JSON 收口在 ai_payload_file.extract_json_payload（2026-07-27 實機 9g）：
        # 原本只認「開頭就是 ```」，CLI 多一句開場白（「依契約輸出：」「以下為契約
        # 指定的 JSON 物件：」）就整段丟 json.loads 而炸——job 102 跑了 183 秒、
        # 第一批已落庫，仍因此整趟報 failed。共用函式容忍前後贅字，七支 runner 同一份。
        try:
            candidate = extract_json_payload(text)
        except ValueError as exc:
            raise IrrelevantFilterRunnerError(str(exc)) from exc
    results = candidate.get("results") if isinstance(candidate, dict) else candidate
    if not isinstance(results, list):
        raise IrrelevantFilterRunnerError(
            f"CLI 輸出缺少 results 陣列：{str(parsed)[:300]}")
    return [item for item in results if isinstance(item, dict)]


def _normalize_verdict(raw: Any) -> str:
    """把 AI 回傳的 verdict 限縮到三分之一；未知值保守歸『可疑』。

    保守設計：AI 若回出格值（幻覺/亂寫），不擅自當成相干或不相干，一律標可疑讓使用者
    重點檢視，符合「AI 只輔助、不決定正式分類」原則。
    """
    value = str(raw or "").strip()
    if value in VALID_VERDICTS:
        return value
    return "可疑"


def fetch_notes(patent_ids: Sequence[int], *, conn: Any | None = None) -> dict[int, str | None]:
    """讀這批 patent 的文獻備註（0032 已搬到 core_layer.patents."文獻備註"）。

    conn 可注入（測試餵拋棄式 DB）；未注入時借連線池。回 {patent_id: note}；備註為 NULL
    時值為 None，由 runner 標『無法判斷』（不預設相干/不相干）。
    """
    ids = [int(pid) for pid in patent_ids]
    if not ids:
        return {}
    from contextlib import nullcontext

    if conn is not None:
        ctx = nullcontext(conn)
    else:
        from backend.app.db.connection import get_pool
        ctx = get_pool().connection()
    with ctx as active:
        with active.cursor() as cur:
            cur.execute(
                'SELECT id, "文獻備註" FROM core_layer.patents WHERE id = ANY(%s)',
                (ids,),
            )
            rows = cur.fetchall()
    result: dict[int, str | None] = {}
    for r in rows:
        pid = int(r[0] if not isinstance(r, dict) else r["id"])
        note = r[1] if not isinstance(r, dict) else r["文獻備註"]
        result[pid] = note
    return result


def _persist_verdicts(workspace_id: int, results: Sequence[dict[str, Any]]) -> int:
    """把逐筆判讀落為待複核草稿（status='pending'），回實際寫入筆數。

    寫入收口在 clustering.exclusions.store_ai_verdicts（本 runner 不自己寫 SQL）；
    只有「不相干」「可疑」會落庫，「相干」與「無法判斷」不進待複核清單。

    ⚠ 失敗隔離（沿 handlers 的 enqueue 失敗隔離模式）：落庫失敗只記 log 不 raise——
    AI 判讀已經跑完（token 已花），不能讓寫庫問題把整趟結果吃掉；results 仍回得去，
    job 結果看得到判讀內容，使用者可重跑落庫。
    """
    import logging

    try:
        with psycopg.connect(**get_connection_kwargs()) as conn:
            stored = store_ai_verdicts(workspace_id, results, conn=conn)
            conn.commit()
        return stored
    except Exception:  # noqa: BLE001 - 落庫失敗不得吃掉已完成的 AI 判讀結果
        logging.getLogger(__name__).exception(
            "irrelevant filter verdicts persist failed (workspace_id=%s)", workspace_id)
        return 0


def run_irrelevant_filter(
    *,
    workspace_id: int,
    candidates: Sequence[tuple[int, str | None]] | None = None,
    topic_label: str | None = None,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: CliRunner | None = None,
    fetch_notes: Callable[[Sequence[int]], dict[int, str | None]] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
    payload_root: Any = None,
) -> dict[str, Any]:
    """整條篩選判讀流程：整理候選備註 → 分批內嵌 prompt 呼 CLI → 逐筆解析。

    candidates＝[(patent_id, note)]（分數已在上游 c-TF-IDF 挑選階段用完、不傳入）。
    - note 已給者直接用；note 為 None 且提供 fetch_notes 時，補讀文獻備註。
    - **備註為空（None/空白）者直接標『無法判斷』**，不進 prompt、不呼 AI 判空的
      （規格第 101 行：不得預設相干/不相干）。
    - 有備註者依 batch_size 分批（規格第 118 行：50 筆一批），逐批呼 CLI；每批 prompt
      要求逐筆獨立判斷、不相對化。

    cli_runner／fetch_notes 可注入供測試以 fake 取代，不真跑 CLI/DB、不燒 token。
    progress(stage, percent) 供執行期 0→100 緩進。回傳含逐筆 results 與統計。
    """
    from . import ai_payload_file as pf

    runner = cli_runner if cli_runner is not None else _subprocess_cli_runner
    pf.cleanup_old_payloads(root=payload_root)

    if progress is not None:
        progress("整理候選專利文獻備註", 10)

    cand_list = list(candidates or [])
    # note 缺（None）且有 fetch_notes 時補讀；已給 note 者不動。
    missing_note_ids = [pid for pid, note in cand_list if note is None]
    fetched: dict[int, str | None] = {}
    if missing_note_ids and fetch_notes is not None:
        fetched = fetch_notes(missing_note_ids)

    # 分兩堆：有備註（進 AI）、空備註（直接無法判斷）。保留輸入順序。
    to_judge: list[tuple[int, str]] = []
    undecidable: list[dict[str, Any]] = []
    for pid, note in cand_list:
        text = note if note is not None else fetched.get(pid)
        if text is None or not str(text).strip():
            # 備註為空：無法判讀，明確標無法判斷（不預設相干/不相干）。
            undecidable.append({"patent_id": pid, "verdict": UNDECIDABLE,
                                "reason": "文獻備註為空，無法判讀"})
        else:
            to_judge.append((pid, str(text).strip()))

    results: list[dict[str, Any]] = []
    if not to_judge:
        # 全部空備註：不呼 CLI（不空燒 token），全標無法判斷。
        if progress is not None:
            progress("無可判讀的文獻備註", 100)
        # 全為「無法判斷」，store_ai_verdicts 不收此值（無判讀依據不進待複核清單），
        # 故 stored 恆為 0；仍回該欄位保持結果形狀一致。
        return {
            "workspace_id": workspace_id,
            "candidates": len(cand_list),
            "judged": 0,
            "undecidable": len(undecidable),
            "stored": 0,
            "results": undecidable,
            "prompt_version": PROMPT_VERSION,
            "cli_kind": cli_kind,
        }

    # 分批（規格第 118 行：批次 50）。
    batches = [to_judge[i : i + batch_size] for i in range(0, len(to_judge), batch_size)]
    total_batches = len(batches)
    for batch_index, batch in enumerate(batches):
        if progress is not None:
            # 55→90 之間按批緩進。
            pct = 55 + int(35 * (batch_index / total_batches))
            progress(f"AI 判讀第 {batch_index + 1}/{total_batches} 批", pct)
        # 資料走檔案不走命令列（2026-07-27）：備註全文塞 argv 在 Windows
        # （CreateProcess 上限 32,767）有超標風險（50 筆備註實測 6,589，備註變長即漲）。
        # 與 topic_label／patent_note 收斂到同一套 ai_payload_file，不再各自散落。
        path = pf.write_payload_file(
            "irrelevant_filter",
            {
                "instruction": (
                    "逐筆判斷每則文獻備註與主題是否相干，"
                    f"verdict 僅能為 {list(VALID_VERDICTS)} 之一；"
                    "每筆獨立判斷，不得與其他筆相對比較。"
                ),
                "topic_label": topic_label,
                "output_contract": {"results": [{"patent_id": 0, "verdict": ""}]},
                "items": [{"patent_id": pid, "note": note} for pid, note in batch],
            },
            root=payload_root,
            label=f"ws{workspace_id}_b{batch_index + 1:02d}",
        )
        argv = pf.build_cli_command_with_payload(
            cli_kind,
            instruction="任務：判斷專利與主題是否相干（系統派工、非互動、一次性）。",
            payload_path=path,
            model=model,
        )
        parsed = parse_cli_result(runner(argv, timeout_seconds))
        batch_ids = {pid for pid, _ in batch}
        for item in _extract_results(parsed):
            try:
                pid = int(item.get("patent_id"))
            except (TypeError, ValueError) as exc:
                raise IrrelevantFilterRunnerError(
                    f"CLI 產出 patent_id 非整數：{item.get('patent_id')!r}"
                ) from exc
            if pid not in batch_ids:
                # 幻覺 patent_id 直接失敗，不把不屬於本批的判讀寫進結果。
                raise IrrelevantFilterRunnerError(
                    f"CLI 產出未知 patent_id：{pid}（本批：{sorted(batch_ids)}）")
            results.append({
                "patent_id": pid,
                "verdict": _normalize_verdict(item.get("verdict")),
                "reason": str(item.get("reason") or "").strip(),
            })

    # 空備註的無法判斷結果併回總結果。
    results.extend(undecidable)

    # 落庫為待複核草稿（2026-07-27）：判讀結果原本只回傳、不寫 DB，前端無從逐筆裁決，
    # AI 跑完等於白跑。此處落 status='pending'，等使用者按「保留／確定」。
    stored = _persist_verdicts(workspace_id, results)

    if progress is not None:
        progress("篩選判讀完成", 100)
    return {
        "workspace_id": workspace_id,
        "candidates": len(cand_list),
        "judged": len(to_judge),
        "undecidable": len(undecidable),
        "stored": stored,
        "results": results,
        "prompt_version": PROMPT_VERSION,
        "cli_kind": cli_kind,
    }
