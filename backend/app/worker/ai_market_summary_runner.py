"""市場資料 AI 摘要 headless CLI runner（ai:market_summary 任務的核心）。

用途：讀某 workspace 上傳的市場 PDF（batch1 已落 MARKET_DOC_ROOT，metadata 在
`derived_layer.market_documents`），以 pymupdf 抽文字後**內嵌 prompt**，交 headless CLI
產出「結構化 payload_json ＋ 敘述 narrative」摘要草稿，經 batch1 的 MarketDocSummaryStore
版本化寫入（accepted_at=NULL，待使用者逐筆確認）。

規格唯一來源：`.agents/context/market-doc-summary-spec.md`。批2 落實的定案重點：

1. ⚠ **prompt 不寫死抽取欄位清單**（2026-07-24 使用者修正，本批最高優先）：每份市場 PDF
   的架構都不同（有的有 CAGR、有的分區域、有的只有質性描述）。prompt 引導 AI「依該份 PDF
   實際有的內容抽對報表有用的市場資訊，有什麼抽什麼，沒有的不硬湊、不留空欄硬填」。
2. ⚠ **範例 PPT 第10頁的 few-shot 定位為「品質／口吻參考」**，不是「必填欄位規格」——
   讓 AI 知道「好的市場摘要長這樣、這種密度、數字用區間、註明來源」，不是要求它也產出
   市場規模＋CAGR＋三區域（那是割草機的架構，別份 PDF 不會一樣）。
3. **payload_json 為彈性結構**（`items` 陣列，每項可含 label／value_min／value_max／unit／
   period／source／note，異質指標都放得進；或整份 payload_json 直接為 None）——善用 batch1
   把 payload_json 設計成整欄可空 JSONB 的彈性，不在批2 又當固定 schema 用。
4. **專利主角、市場輔助鐵律**：數字薄弱時寫質性 narrative、不硬造數字；payload_json 可空、
   由 narrative 承接；**專利數據不得推算市場規模／市占**。
5. **草稿 → 確認 → 報表只讀 accepted**：runner 只 create_summary（accepted_at=NULL），
   不代為 accept；報表經 get_report_market_summary 只讀 get_accepted_current。
6. **全庫 workspace 拒產**（2026-07-24 定案）：全庫是所有專利總和、無單一產品定義，市場範圍
   隨產品線走，故全庫不提供市場資料，runner 與報表取用一律擋下。

⚠ **安全來自任務設計**（沿文獻備註線 2026-07-23 定案）：市場 PDF 文字**直接內嵌 prompt**，
CLI 不需 Read 檔案、不需連網即可完成——「沒必要做」比「不准做」可靠。CLI 白名單為空
（`_MARKET_TAIL_ARGS`），明確不開 WebSearch／WebFetch／Read／Glob／Grep／Write。

設計沿用 ai_patent_note_runner／ai_candidate_explanation_runner：CLI 呼叫抽成可注入的
`cli_runner`（測試餵 fake，不跑二進位、不燒 token）；document_store／summary_store／
extract_text／is_global 皆可注入，供測試以 fake 取代，不真碰 DB／PDF。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from backend.app.settings import get_market_doc_root

from .ai_narrative_runner import (
    DEFAULT_CLI_TIMEOUT_SECONDS,
    CliRunner,
    parse_cli_result,
    _CLI_SPECS,
    _subprocess_cli_runner,
)
from .ai_payload_file import extract_json_payload


# 市場摘要流程版本；隨 prompt 契約升版而變，寫進結果供追溯。
PROMPT_VERSION = "market_summary_v1"

# 🔴 最小權限：本任務所有 PDF 文字已內嵌 prompt，CLI 不需讀檔、不寫檔、不上網。
# 明確不加 WebSearch／WebFetch／Read／Glob／Grep／Write，避免 CLI 白名單擴權
# （舊「AI 上網 deep-research」設計已作廢，見 spec「方向調整」節）。
_MARKET_TAIL_ARGS = ["--output-format", "json", "--allowedTools", ""]

# 單份 PDF 抽出文字的字數上限：避免超長市調報告撐爆 context；截斷只影響送給模型的輸入，
# 不改動落檔的原始 PDF。市場摘要重點在輪廓，前段通常已含主要數字與定義。
DEFAULT_PER_DOC_CHAR_LIMIT = 20_000


class MarketSummaryRunnerError(RuntimeError):
    """市場摘要流程失敗（全庫拒產、CLI 產出不合契約等）。"""


def build_cli_command(cli_kind: str, prompt: str, *, model: str | None = None) -> list[str]:
    """組 headless argv；沿用 ai_narrative_runner 的 CLI 對照表，但覆寫 tail_args 為空白名單。

    覆寫理由：市場 PDF 文字自帶在 prompt 內，CLI **不需要任何工具**（不讀檔、不連網），
    故白名單為空。opencode 等未提供工具白名單旗標的 CLI 沿用其原 tail_args。
    """
    spec = _CLI_SPECS.get(cli_kind)
    if spec is None:
        raise MarketSummaryRunnerError(
            f"未知 cli_kind：{cli_kind!r}（可用：{sorted(_CLI_SPECS)}）")
    model_args: list[str] = []
    if model:
        model_flag = spec.get("model_flag")
        if not model_flag:
            raise MarketSummaryRunnerError(f"{cli_kind!r} 不支援指定 model")
        model_args = [model_flag, model]
    tail = _MARKET_TAIL_ARGS if cli_kind == "claude" else list(spec["tail_args"])
    return [spec["binary"], spec["prompt_flag"], prompt, *model_args, *tail]


def extract_market_texts(
    docs: list[dict[str, Any]],
    *,
    root: Path | None = None,
    per_doc_char_limit: int = DEFAULT_PER_DOC_CHAR_LIMIT,
) -> dict[str, str]:
    """用 pymupdf 把每份市場 PDF 抽成純文字（key＝stored_filename）。

    選 pymupdf 抽文字（非讓 CLI Read 整份 PDF）的理由：
    - 文字內嵌 prompt → CLI 白名單可為空（不授予 Read／網路），安全來自任務設計。
    - pymupdf 已是專案依賴（comparison 線在用），不新增相依。
    - 市場 PDF 為散文／表格為主（非公式圖表密集的專利），純文字抽取足夠承載市場輪廓；
      獨立公式／圖內文字的損失對市場摘要影響小（不像專利需精確公式）。
    延遲 import pymupdf：測試注入 fake extract_text 時不需裝 pymupdf 也能載入本模組。
    """
    import pymupdf  # 延遲 import：fake extract_text 測試不觸發

    base = root if root is not None else get_market_doc_root()
    texts: dict[str, str] = {}
    for doc in docs:
        stored = doc.get("stored_filename")
        if not stored:
            continue
        pdf_path = base / stored
        try:
            with pymupdf.open(pdf_path) as pdf:
                parts = [page.get_text() for page in pdf]
        except Exception as exc:  # noqa: BLE001
            # 單份抽取失敗不整批中斷：記為錯誤說明，讓 AI 知道該份讀不到，其餘照抽。
            texts[stored] = f"[讀取失敗：{exc}]"
            continue
        text = "\n".join(parts).strip()
        texts[stored] = text[:per_doc_char_limit]
    return texts


# 範例 PPT 第10頁的實際句子，作為 few-shot **品質／口吻參考**（不是欄位規格）。
# ⚠ 這些句子只示範「好的市場摘要長怎樣」：數字用區間、標來源機構、質性判斷具體。
# 絕不是要求 AI 也產出市場規模＋CAGR＋三區域——那是割草機 PDF 的架構，別份不會一樣。
_FEWSHOT_EXAMPLES = (
    "「電動割草機(含電池／有線，不含燃油)市場輪廓；已對照多家市調機構交叉驗證，"
    "各機構定義不同，以區間呈現」\n"
    "「北美｜最大市場 35–41%｜住宅DIY主戰場｜CARB 2024 禁售燃油」\n"
    "「對公司意涵：自走式＝北美住宅產品；無自有電池平台生態者難以 B2C 切入」"
)


def build_prompt(texts: dict[str, str]) -> str:
    """把各份市場 PDF 的文字組成 headless CLI 提示。

    ⚠ 兩條批2 紅線都在這裡執行：
    - **不寫死抽取結構**：只引導「依該份 PDF 實際有的內容抽、沒有不硬湊」，不列必填欄位清單。
    - **few-shot＝品質參考**：範例第10頁的句子明確界定為「呈現品質與口吻的參考，不是要求
      你產出相同欄位」。

    ⚠ 專利主角、市場輔助鐵律亦在此明寫：數字薄弱寫質性、不硬造、不用專利推算市場。
    ⚠ 安全來自任務設計：PDF 文字直接內嵌下方，CLI 不需讀檔／連網（白名單為空）。
    """
    blocks: list[str] = []
    for filename, text in texts.items():
        blocks.append(f"### 檔名：{filename}\n{text}")
    docs_block = "\n\n".join(blocks) if blocks else "（無市場資料）"

    return (
        "任務：閱讀以下使用者上傳的市場資料 PDF 文字，為這個分析產出一份市場摘要"
        "（系統派工、非互動、一次性）。此摘要供報表／簡報呈現市場輪廓，輔助讀者理解"
        "專利數據的產業背景。\n\n"
        "── 撰寫原則 ──\n"
        "1. **依該份 PDF 實際有的內容抽取**：有什麼市場資訊就抽什麼，對報表有用的都可以收；"
        "PDF 沒有的欄位**不要硬湊、不要留空欄硬填**。不同 PDF 架構差異很大——有的分區域、"
        "有的分產品線、有的只有總體數字、有的只有質性描述——依實際內容自適應，不套固定模板。\n"
        "2. **專利數據才是主角、市場資料只是輔助**：市場數字薄弱或查無時，改寫「質性描述」"
        "（如通路、法規、主要市場、客群特徵）一樣有價值；**絕不硬造數字**、不得把單一來源的"
        "粗略說法包裝成精確區間、**不得用專利數據推算市場規模／市占**。找不到依據就寫缺漏，"
        "不臆測。\n"
        "3. 有數字時，數值以**區間**呈現並註明**來源機構與年份**；多份 PDF 對同一指標給不同"
        "數字時取 min–max，不平均。\n\n"
        "── 品質參考（範例，僅供對標口吻與密度，**不是欄位規格、不代表你要產出相同項目**）──\n"
        f"{_FEWSHOT_EXAMPLES}\n"
        "上面範例是「電動割草機」報告的樣子，示範好的市場摘要應有的密度、具體度與口吻"
        "（數字帶區間與來源、質性判斷具體）。**你手上的 PDF 主題不同、架構也不同，"
        "請照你實際讀到的內容產出，不要照抄範例的欄位。**\n\n"
        "── 市場資料 PDF 文字 ──\n"
        f"{docs_block}\n\n"
        "── 輸出契約 ──\n"
        "只輸出一個 JSON 物件，形狀為\n"
        '{"payload_json": {"items": [{"label": "...", "value_min": 55, "value_max": 110, '
        '"unit": "億美元", "period": "2024-2025", "source": "...", "note": "..."}, ...]}, '
        '"narrative": "..."}\n'
        "- `payload_json`＝結構化數據（有數字才填；每個數值指標一個 item，欄位有幾個填幾個，"
        "沒有的省略；**整份都沒有可比的結構化數字時，payload_json 直接給 null**）。\n"
        "- `narrative`＝敘述判斷（對公司意涵那類戰略敘述、或數字薄弱時的質性描述）。\n"
        "一律繁體中文；不要輸出多餘說明文字。"
    )


def _extract_summary(parsed: dict[str, Any]) -> dict[str, Any]:
    """從 headless CLI 的 JSON 輸出取出 {payload_json, narrative}。

    `claude -p --output-format json` 會把模型回覆包在 `result` 字串內，故先解外層再解內層；
    CLI 直接回契約形狀者（如 opencode 或未來變更）也一併支援，不寫死單一形狀。
    """
    candidate: Any = parsed
    has_contract = isinstance(candidate, dict) and (
        "payload_json" in candidate or "narrative" in candidate)
    if not has_contract and isinstance(candidate.get("result"), str):
        text = candidate["result"].strip()
        # 取 JSON 收口在 ai_payload_file.extract_json_payload（2026-07-27 實機 9g）：
        # 原本只認「開頭就是 ```」，CLI 多一句開場白（「依契約輸出：」「以下為契約
        # 指定的 JSON 物件：」）就整段丟 json.loads 而炸——job 102 跑了 183 秒、
        # 第一批已落庫，仍因此整趟報 failed。共用函式容忍前後贅字，七支 runner 同一份。
        try:
            candidate = extract_json_payload(text)
        except ValueError as exc:
            raise MarketSummaryRunnerError(str(exc)) from exc
    if not isinstance(candidate, dict):
        raise MarketSummaryRunnerError(f"CLI 輸出非 JSON 物件：{str(parsed)[:300]}")
    payload_json = candidate.get("payload_json")
    narrative = candidate.get("narrative")
    # payload_json 必須是 dict 或 None（彈性結構，數值薄弱時整份可空）。
    if payload_json is not None and not isinstance(payload_json, dict):
        raise MarketSummaryRunnerError(
            f"CLI 產出 payload_json 型別非物件/空：{type(payload_json).__name__}")
    return {
        "payload_json": payload_json,
        "narrative": (str(narrative).strip() or None) if narrative is not None else None,
    }


def get_report_market_summary(
    workspace_id: int,
    *,
    summary_store: Any | None = None,
    is_global: Callable[[int], bool] | None = None,
) -> dict[str, Any] | None:
    """報表／PPT 取用市場摘要的唯一入口：只回「現行版且已確認」的摘要。

    護欄：
    - **全庫 workspace 一律 None**（全庫不提供市場資料）——即使庫內誤有摘要也不給。
    - **只讀 accepted**：未確認草稿實體上進不了報表（沿實體隔離精神）。
    無市場資料或現行版未確認時回 None，交由報表側「無市場資料整區隱藏」的降級處理。
    """
    if is_global is None:
        from backend.app.app_layer.global_workspace import is_global_workspace as is_global
    if is_global(workspace_id):
        return None
    store = summary_store
    if store is None:
        from backend.app.market.market_doc_store import MarketDocSummaryStore
        store = MarketDocSummaryStore()
    return store.get_accepted_current(workspace_id)


def run_market_summary(
    *,
    workspace_id: int,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: CliRunner | None = None,
    document_store: Any | None = None,
    summary_store: Any | None = None,
    extract_text: Callable[[list[dict[str, Any]]], dict[str, str]] | None = None,
    is_global: Callable[[int], bool] | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """整條市場摘要流程：拒全庫 → 列 PDF → 抽文字內嵌 prompt → 呼 CLI → 存草稿。

    ⚠ 全庫 workspace 直接 raise，不呼 CLI、不寫摘要（全庫不提供市場資料）。
    ⚠ 產出為**草稿**（create_summary，accepted_at=NULL）——runner 不代為 accept，
    確認是使用者的事（批3 前端做確認 UI）。

    document_store／summary_store／extract_text／is_global／cli_runner 皆可注入，供測試以
    fake 取代，不真碰 DB／PDF／CLI、不燒 token、不產生真 job 進佇列。
    每階段回報進度（0→100），不留無限 spinner。回傳含 summary_id（無 PDF 時為 None）。
    """
    # ── 全庫護欄：最先擋，避免任何後續動作 ──
    if is_global is None:
        from backend.app.app_layer.global_workspace import is_global_workspace as is_global
    if is_global(workspace_id):
        raise MarketSummaryRunnerError(
            f"全庫 workspace（{workspace_id}）不提供市場資料，拒為其產摘要")

    doc_store = document_store
    if doc_store is None:
        from backend.app.market.market_doc_store import MarketDocumentStore
        doc_store = MarketDocumentStore()
    summ_store = summary_store
    if summ_store is None:
        from backend.app.market.market_doc_store import MarketDocSummaryStore
        summ_store = MarketDocSummaryStore()
    extractor = extract_text if extract_text is not None else extract_market_texts
    runner = cli_runner if cli_runner is not None else _subprocess_cli_runner

    if progress is not None:
        progress("讀取市場資料 PDF 清單", 10)
    docs = doc_store.list_documents(workspace_id)
    if not docs:
        # 沒有上傳市場 PDF＝沒東西可摘要，不呼 CLI、不寫摘要（不空燒 token）。
        if progress is not None:
            progress("此 workspace 尚無市場資料", 100)
        return {
            "workspace_id": workspace_id,
            "documents": 0,
            "summary_id": None,
            "prompt_version": PROMPT_VERSION,
            "cli_kind": cli_kind,
        }

    if progress is not None:
        progress("抽取市場 PDF 文字", 30)
    texts = extractor(docs)

    if progress is not None:
        progress("AI 產生市場摘要中", 55)
    prompt = build_prompt(texts)
    argv = build_cli_command(cli_kind, prompt, model=model)
    parsed = parse_cli_result(runner(argv, timeout_seconds))
    summary = _extract_summary(parsed)

    if progress is not None:
        progress("寫入市場摘要草稿（待確認）", 85)
    # 來源檔名：多份時併列，供追溯（哪些 PDF 產出這版摘要）。
    source_document = "；".join(
        str(d.get("original_filename") or d.get("stored_filename") or "") for d in docs
    ) or None
    # 草稿：accepted_at 由 store 於 create_summary 設 NULL，runner 不代為 accept。
    summary_id = summ_store.create_summary(
        workspace_id,
        payload_json=summary["payload_json"],
        narrative=summary["narrative"],
        source_document=source_document,
    )

    if progress is not None:
        progress("市場摘要草稿已建立，待逐筆確認", 100)
    return {
        "workspace_id": workspace_id,
        "documents": len(docs),
        "summary_id": summary_id,
        "prompt_version": PROMPT_VERSION,
        "cli_kind": cli_kind,
    }
