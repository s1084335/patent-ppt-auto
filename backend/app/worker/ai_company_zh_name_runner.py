"""公司中文名 AI 草稿 headless CLI runner（ai:company_zh_name 任務的核心）。

用途：把「缺市場慣用中文名」的公司（申請人／專利權人代碼＋英文公司名）交給 headless
CLI，產出中文名**草稿**，寫回 `derived_layer.company_aliases` 的 `ai_suggested` 態列，
等使用者逐筆確認（走既有 apply_confirmed_display_names）才成為正式顯示名。

規格唯一來源：decisions.md 2026-07-24「公司中文名由 AI 產草稿」＋「落點修正：沿用既有
company_aliases 機制，不另建表」。定案重點：

1. **沿用 company_aliases 三態**（不新增欄）：
   - 未判斷：canonical 無 CJK、無 curation 裁決列、無 AI 草稿列
     （＝govern_company_names 的 needs_zh_name 條件，本 runner 全庫掃）。
   - AI 草稿待確認：一列 review_status='ai_suggested'、source_type='ai_suggested'，
     公司名稱＝中文名草稿（verdict='translated'）或原文（verdict='keep_original'），
     verdict 存 wips_metadata_json->'zh_name_verdict'。
   - 已確認：apply_confirmed_display_names 寫 confirmed（含保留原文裁決）。
2. **AI 草稿不進正式顯示欄**：refresh 的 code_alias_names 只採 confirmed 列，草稿列
   （ai_suggested）天然被排除——這是三態最重要的護欄，AI 只產草稿、不決定正式資料。
3. **市場慣用中文名、不硬翻**（使用者紅線）：prompt 明令要市場慣用叫法（泉峰、牧田、
   史丹利百得、創科、美沃奇），不直翻、不音譯；**明確允許並鼓勵回報「查無慣用中文名
   （保留原文）」**——這是本案最大風險（AI 對冷門公司易硬造），故 verdict 兩態分明。
4. **同代碼草稿唯一**：重跑時同代碼既有草稿以一次 UPDATE 收斂，不堆疊多列。

安全（沿 2026-07-23「安全來自任務設計」）：待中文化清單（代碼＋英文名）內嵌 prompt，
CLI 不需讀檔、不需連網；CLI 白名單維持最小權限（見 _ZH_TAIL_ARGS）。

設計沿用 ai_patent_note_runner／ai_candidate_explanation_runner：CLI 呼叫抽成可注入的
cli_runner（測試餵 fake，不跑二進位、不燒 token），指令組裝共用 _CLI_SPECS。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from backend.app.transforms.text import clean_text

from .ai_narrative_runner import (
    DEFAULT_CLI_TIMEOUT_SECONDS,
    CliResult,
    CliRunner,
    _CLI_SPECS,
    _subprocess_cli_runner,
    parse_cli_result,
)
from . import ai_payload_file as pf
from .ai_payload_file import extract_json_payload


# 中文名草稿流程版本；隨 prompt 契約升版而變，寫進結果供追溯。
PROMPT_VERSION = "company_zh_name_v1"

# 草稿列的標記（沿 company_aliases 既有欄，不新增欄）。
DRAFT_REVIEW_STATUS = "ai_suggested"
DRAFT_SOURCE_TYPE = "ai_suggested"
DRAFT_SOURCE_FILE = "ai:company_zh_name"

# AI 判定兩態：有市場慣用中文名 vs 查無（保留原文）。
VERDICT_TRANSLATED = "translated"
VERDICT_KEEP_ORIGINAL = "keep_original"
_VALID_VERDICTS = frozenset({VERDICT_TRANSLATED, VERDICT_KEEP_ORIGINAL})

# 🔴 最小權限：本任務只需模型讀 prompt 內文並回 JSON，不讀檔、不寫檔、不上網。
_ZH_TAIL_ARGS = ["--output-format", "json", "--allowedTools", ""]


class CompanyZhNameRunnerError(RuntimeError):
    """中文名草稿流程失敗（CLI 產出不合契約、回吐未知代碼／verdict 等）。"""


def build_cli_command(cli_kind: str, prompt: str, *, model: str | None = None) -> list[str]:
    """組 headless argv；沿用 ai_narrative_runner 對照表，但覆寫最小權限 tail_args。

    本任務不需任何工具：prompt 自帶全部輸入、輸出走 stdout，故 claude 走空白名單。
    opencode 等未提供工具白名單旗標的 CLI 沿用其原 tail_args。
    """
    spec = _CLI_SPECS.get(cli_kind)
    if spec is None:
        raise CompanyZhNameRunnerError(
            f"未知 cli_kind：{cli_kind!r}（可用：{sorted(_CLI_SPECS)}）")
    model_args: list[str] = []
    if model:
        model_flag = spec.get("model_flag")
        if not model_flag:
            raise CompanyZhNameRunnerError(f"{cli_kind!r} 不支援指定 model")
        model_args = [model_flag, model]
    tail = _ZH_TAIL_ARGS if cli_kind == "claude" else list(spec["tail_args"])
    return [spec["binary"], spec["prompt_flag"], prompt, *model_args, *tail]


# 判定規則與輸出契約（2026-07-28 由命令列 prompt 搬進資料檔）。
# 內容與原 build_prompt 逐條等價，只是改成結構化欄位由 CLI 讀檔取得。
_ZH_NAME_RULES = [
    "若該公司在市場上有**廣為人知的慣用中文名**，回報該中文名。"
    "例：Chervon→泉峰、Makita→牧田、Stanley Black & Decker→史丹利百得、"
    "Techtronic→創科、Milwaukee→美沃奇。這些是市場慣用叫法，**不是直翻**。",
    "**不直翻、不音譯**：不得把英文公司名逐字翻成中文，也不得音譯造一個中文名。"
    "慣用中文名是市場既成叫法，查無就查無，不要自行拼造。",
    "**查無廣為人知中文名時，回報保留原文**（verdict='keep_original'）——"
    "這是完全正常且被鼓勵的結果，寧可保留原文也不要硬造。冷門或小公司多屬此類。",
    "判定用繁體中文思考；zh_name 若有值必為繁體中文，技術詞／型號可保留英文。",
]

_ZH_NAME_OUTPUT_CONTRACT = {
    "shape": '{"names": [{"company_code": "X", "verdict": "translated", "zh_name": "泉峰"}, ...]}',
    "verdict": {
        "translated": "有市場慣用中文名，zh_name 給該中文名",
        "keep_original": "查無慣用中文名，保留原文，可省略 zh_name",
    },
    "rules": [
        "company_code 必須原樣取自 companies 清單，不得新增、改寫或遺漏",
        "只輸出一個 JSON 物件，不要多餘說明文字",
    ],
}


def build_zh_name_payload(companies: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """組資料檔內容（取代把整批公司名串進命令列）。

    ⚠ 為什麼改（2026-07-28，使用者原則「AI 分類不要走參數傳遞那種」）：
    原本 build_prompt 把整批公司名串成一大段字再塞進 argv，長度隨公司數線性成長——
    實測 20 家 2,384 字元、200 家 17,784、**500 家 43,584 已超過 Windows
    CreateProcess 的 32,767 上限**（臨界約 370 家）。撞上時是 WinError 206，
    表象像 CLI 壞掉、極難查；ai:topic_label 2026-07-27 踩過同一個坑（128,101 字元）。

    兩條紅線原封搬進 payload，語意不變：不硬翻、查無回 keep_original。
    """
    return {
        "task": "為每家公司判定「市場慣用中文名」（系統派工、非互動、一次性）",
        "rules": list(_ZH_NAME_RULES),
        "companies": [{"code": code, "name_en": name} for code, name in companies],
        "output_contract": _ZH_NAME_OUTPUT_CONTRACT,
    }


def build_prompt(companies: Sequence[tuple[str, str]]) -> str:
    """把一批「代碼＋英文公司名」組成 headless CLI 提示。

    ⚠ 2026-07-28 起**不再是主路徑**：資料改走 `build_zh_name_payload` 落檔
    （命令列長度不隨公司數成長）。本函式保留供既有測試與離線除錯使用。

    ⚠ 兩條紅線在此執行：
    - **不硬翻**：明令要市場慣用中文名、不直翻、不音譯冷門公司。
    - **查無可區分**：verdict 兩態（translated／keep_original），查無時回 keep_original，
      不編造中文名——這是防 AI 硬造的關鍵。

    ⚠ 安全來自任務設計：代碼與英文名直接內嵌，CLI 不需讀檔／連網即可完成。
    """
    blocks = [f"### company_code: {code}\n公司英文名：{name}" for code, name in companies]
    companies_block = "\n\n".join(blocks)

    return (
        "任務：為每家公司判定「市場慣用中文名」（系統派工、非互動、一次性）。\n\n"
        "判定規則：\n"
        "1. 若該公司在市場上有**廣為人知的慣用中文名**，回報該中文名。\n"
        "   例：Chervon→泉峰、Makita→牧田、Stanley Black & Decker→史丹利百得、\n"
        "   Techtronic→創科、Milwaukee→美沃奇。這些是市場慣用叫法，**不是直翻**。\n"
        "2. **不直翻、不音譯**：不得把英文公司名逐字翻成中文，也不得音譯造一個中文名。\n"
        "   慣用中文名是市場既成叫法，查無就查無，不要自行拼造。\n"
        "3. **查無廣為人知中文名時，回報保留原文**（verdict='keep_original'）——\n"
        "   這是完全正常且被鼓勵的結果，寧可保留原文也不要硬造。冷門或小公司多屬此類。\n"
        "4. 判定用繁體中文思考；zh_name 若有值必為繁體中文，技術詞／型號可保留英文。\n\n"
        f"{companies_block}\n\n"
        "輸出契約：只輸出一個 JSON 物件，形狀為\n"
        '{"names": [{"company_code": "X", "verdict": "translated", "zh_name": "泉峰"},\n'
        '           {"company_code": "Y", "verdict": "keep_original"}, ...]}\n'
        "verdict 只能是 \"translated\"（有慣用中文名，zh_name 給該中文名）或\n"
        "\"keep_original\"（查無，保留原文，可省略 zh_name）。\n"
        "company_code 必須原樣取自上方清單，不得新增、改寫或遺漏；不要輸出多餘說明文字。"
    )


def _extract_names(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """從 headless CLI 的 JSON 輸出取出 names 陣列（沿 patent_note 的雙層解析）。"""
    candidate: Any = parsed
    if "names" not in candidate and isinstance(candidate.get("result"), str):
        text = candidate["result"].strip()
        # 取 JSON 收口在 ai_payload_file.extract_json_payload（2026-07-27 實機 9g）：
        # 原本只認「開頭就是 ```」，CLI 多一句開場白（「依契約輸出：」「以下為契約
        # 指定的 JSON 物件：」）就整段丟 json.loads 而炸——job 102 跑了 183 秒、
        # 第一批已落庫，仍因此整趟報 failed。共用函式容忍前後贅字，七支 runner 同一份。
        try:
            candidate = extract_json_payload(text)
        except ValueError as exc:
            raise CompanyZhNameRunnerError(str(exc)) from exc
    names = candidate.get("names") if isinstance(candidate, dict) else candidate
    if not isinstance(names, list):
        raise CompanyZhNameRunnerError(f"CLI 輸出缺少 names 陣列：{str(parsed)[:300]}")
    return [item for item in names if isinstance(item, dict)]


# 「已裁決中文名」的來源標記前綴（2026-07-28 使用者實機發現後分流）。
#
# `display_name_curation` 前綴下有兩種來源，語意不同：
#   - `:zh_name_review` ＝真的裁決過中文名（含 keep_original「查無，保留原文」）
#   - `:code_registry`  ＝只是在代碼補齊區塊建了組，**中文名還空著**
#
# PENDING_SQL 只能排除前者。原本用寬鬆的 `display_name_curation%` 一律排除，
# 把剛建的組也當成已裁決，使用者按「產生中文名草稿」時 job succeeded 卻
# 什麼都沒做（只跑 3.4 秒、畫面顯示「目前沒有待確認的中文名草稿」）。
ZH_REVIEW_SOURCE_PREFIX = "display_name_curation:zh_name_review"

# 代碼補齊建組的來源後綴，供測試判斷用（實際完整字面見
# `backend/app/api/company_aliases.py` 的 CODE_REGISTRY_SOURCE_LABEL）。
CODE_REGISTRY_SOURCE_SUFFIX = ":code_registry"


class CompanyZhNameStore:
    """中文名草稿的 DB 落點（讀待中文化清單＋草稿收斂寫入）。

    沿用 derived_layer.company_aliases，不另建表。
    """

    # 讀待中文化清單（三態的「未判斷」）：以代碼為單位，取一個代表英文名。
    #
    # ⚠ 2026-07-28 四欄拆分（使用者第④點）改了兩件事：
    # - **待處理判斷**：由「公司名稱不含 CJK」改為「**公司中文名稱為空**」。
    #   語意更直接，不必靠字元類別推測——混合字串（XIAMEN ... | Zeng Qing）
    #   本來就會被字元判斷誤判。
    # - **AI 輸入**：改讀 `正規化名稱`（英文正式名）；該欄空時退用**別稱原文**。
    #   使用者定「AI 本來就是要將正規化或是原值英文轉中文」。
    #
    # 其餘條件不變（全庫掃）：
    # - 該代碼無 curation 裁決列（source_file LIKE 'display_name_curation%'，含保留原文）。
    # - 該代碼無 AI 草稿列（review_status='ai_suggested'）＝不重複問同批、不燒 token。
    # 一次 GROUP BY 掃描（代碼數量級，26 筆等級），非 N+1。
    PENDING_SQL = """
        SELECT ca."申請人代碼",
               mode() WITHIN GROUP (ORDER BY COALESCE(
                   NULLIF(BTRIM(ca."正規化名稱"), ''),
                   NULLIF(BTRIM(ca."別稱"), '')
               )) AS company_name
        FROM derived_layer.company_aliases ca
        WHERE ca.review_status = 'confirmed'
          AND NULLIF(BTRIM(ca."申請人代碼"), '') IS NOT NULL
          AND COALESCE(
                NULLIF(BTRIM(ca."正規化名稱"), ''),
                NULLIF(BTRIM(ca."別稱"), '')
          ) IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM derived_layer.company_aliases z
              WHERE z."申請人代碼" = ca."申請人代碼"
                AND NULLIF(BTRIM(z."公司中文名稱"), '') IS NOT NULL
          )
          -- 已裁決過中文名者不重複問。
          -- ⚠ 只認**裁決**來源（`:zh_name_review`），不認代碼補齊建組
          -- （`:code_registry`）——兩者都掛 display_name_curation 前綴，但後者
          -- 只是建了組、中文名還空著，正是需要 AI 產草稿的對象。
          -- 原本用寬鬆的 `LIKE 'display_name_curation%%'` 一律排除，導致使用者
          -- 建完組按「產生中文名草稿」時 job succeeded 卻只跑 3.4 秒、
          -- 畫面顯示「沒有待確認的草稿」——看起來成功、實際什麼都沒做。
          --
          -- 為何不能改看「中文欄是否為空」就好：keep_original 裁決（查過查無，
          -- 保留英文原文）後中文欄仍是空的，只有來源標記能區分「查過」與「還沒查」。
          AND NOT EXISTS (
              SELECT 1 FROM derived_layer.company_aliases d
              WHERE d."申請人代碼" = ca."申請人代碼"
                AND d.source_file LIKE %(zh_review_prefix)s
          )
          AND NOT EXISTS (
              SELECT 1 FROM derived_layer.company_aliases s
              WHERE s."申請人代碼" = ca."申請人代碼"
                AND s.review_status = 'ai_suggested'
          )
        GROUP BY ca."申請人代碼"
        ORDER BY ca."申請人代碼"
    """

    # 草稿收斂：同代碼既有草稿列先刪再插，保證一代碼一草稿列（不堆疊）。
    _DELETE_DRAFT_SQL = (
        "DELETE FROM derived_layer.company_aliases "
        "WHERE \"申請人代碼\" = %s AND review_status = 'ai_suggested'"
    )
    # ⚠ 草稿的中文名寫進 `公司中文名稱`（2026-07-28 拆四欄）；`keep_original`
    # 時該值為 None → **中文欄留空**，不把英文塞進中文欄（使用者第④點）。
    # `別稱` 是 NOT NULL 欄，草稿列以英文原文（source_name）填之，供列表顯示對照。
    _INSERT_DRAFT_SQL = (
        'INSERT INTO derived_layer.company_aliases '
        '("申請人代碼", "公司中文名稱", "正規化名稱", "別稱", source_file, source_type, '
        " review_status, wips_metadata_json) "
        "VALUES (%(code)s, %(zh_name)s, %(source_name)s, %(source_name)s, %(source_file)s, "
        "        %(source_type)s, %(review_status)s, %(metadata)s)"
    )

    def __init__(self, connect_kwargs: dict[str, Any] | None = None) -> None:
        self._connect_kwargs = connect_kwargs

    def _connect(self):
        """延遲載入 psycopg 與連線設定，避免匯入期就拉進 DB 相依。"""
        import psycopg

        from backend.app.db.connection import get_connection_kwargs

        return psycopg.connect(**(self._connect_kwargs or get_connection_kwargs()))

    def fetch_pending(self, *, limit: int | None = None) -> list[tuple[str, str]]:
        """單次查詢取回待中文化 (company_code, 英文公司名)，非 N+1。"""
        sql = self.PENDING_SQL + ("\n        LIMIT %(limit)s" if limit else "")
        # 具名參數：SQL 內已有 %(zh_review_prefix)s，混用位置參數會 TypeError。
        params: dict[str, Any] = {"zh_review_prefix": f"{ZH_REVIEW_SOURCE_PREFIX}%"}
        if limit:
            params["limit"] = int(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [(str(code), str(name or "")) for code, name in rows]

    def write_drafts(self, drafts: Sequence[dict[str, Any]]) -> int:
        """把草稿收斂寫回：每代碼先刪舊草稿再插新草稿，一趟交易內完成。

        草稿列 review_status='ai_suggested'，故不落 confirmed 唯一索引、不進顯示欄收斂。
        verdict 存 wips_metadata_json->'zh_name_verdict'，供前端／確認流程區分
        translated（有中文名）與 keep_original（查無保留原文）。

        ⚠ 2026-07-28 拆四欄：`zh_name` 只在 translated 時有值，寫進 `公司中文名稱`；
        keep_original 時為 None（中文欄留空，不塞英文）。`source_name`＝AI 的輸入
        英文名，寫進 `正規化名稱`／`別稱` 供列表對照。
        """
        from psycopg.types.json import Jsonb

        if not drafts:
            return 0
        written = 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                for draft in drafts:
                    code = draft["company_code"]
                    # 同代碼草稿收斂：先刪既有草稿列，再插一列（不堆疊多列草稿）。
                    cur.execute(self._DELETE_DRAFT_SQL, (code,))
                    cur.execute(
                        self._INSERT_DRAFT_SQL,
                        {
                            "code": code,
                            "zh_name": draft.get("zh_name") or None,
                            "source_name": draft.get("source_name") or draft.get("zh_name"),
                            "source_file": DRAFT_SOURCE_FILE,
                            "source_type": DRAFT_SOURCE_TYPE,
                            "review_status": draft.get("review_status", DRAFT_REVIEW_STATUS),
                            "metadata": Jsonb({"zh_name_verdict": draft["verdict"]}),
                        },
                    )
                    written += 1
            conn.commit()
        return written


def run_company_zh_name(
    *,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: CliRunner | None = None,
    store: Any | None = None,
    limit: int | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """整條中文名草稿流程：讀待中文化清單 → 內嵌 prompt 呼 CLI → 解析 → 草稿收斂寫回。

    cli_runner／store 皆可注入，供測試以 fake 取代，不真跑 CLI、不碰 DB。
    AI 只產草稿（ai_suggested 態），不進正式顯示欄；使用者確認才走 apply_confirmed_display_names。
    回傳：候選數、草稿寫入數、cli_kind／prompt_version 供追溯。
    """
    runner = cli_runner if cli_runner is not None else _subprocess_cli_runner
    zh_store = store if store is not None else CompanyZhNameStore()

    if progress is not None:
        progress("讀取待中文化的公司", 10)
    candidates = zh_store.fetch_pending(limit=limit)
    if not candidates:
        if progress is not None:
            progress("沒有待中文化的公司", 100)
        return {
            "candidates": 0,
            "drafts_written": 0,
            "cli_kind": cli_kind,
            "prompt_version": PROMPT_VERSION,
        }

    known_codes = {code for code, _ in candidates}
    if progress is not None:
        progress(f"AI 產生公司中文名草稿（{len(candidates)} 家）", 40)
    # 資料走檔案、命令列只留 instruction 與路徑（見 build_zh_name_payload 的說明）。
    payload_path = pf.write_payload_file(
        "company_zh_name",
        build_zh_name_payload(candidates),
        label=f"n{len(candidates)}",
    )
    argv = pf.build_cli_command_with_payload(
        cli_kind,
        instruction="任務：為公司判定市場慣用中文名（系統派工、非互動、一次性）。",
        payload_path=payload_path,
        model=model,
    )
    parsed = parse_cli_result(runner(argv, timeout_seconds))

    # 代碼→英文名，供 keep_original 時落回原文（顯示不硬翻）。
    name_by_code = {code: name for code, name in candidates}
    drafts: list[dict[str, Any]] = []
    for item in _extract_names(parsed):
        code = clean_text(item.get("company_code"))
        if code is None:
            raise CompanyZhNameRunnerError(f"CLI 產出缺 company_code：{item}")
        if code not in known_codes:
            # 幻覺代碼直接失敗，不把不存在的草稿寫進資料。
            raise CompanyZhNameRunnerError(
                f"CLI 產出未知 company_code：{code}（本批：{sorted(known_codes)}）")
        verdict = str(item.get("verdict") or "").strip()
        if verdict not in _VALID_VERDICTS:
            raise CompanyZhNameRunnerError(
                f"CLI 產出未知 verdict：{verdict!r}（限 {sorted(_VALID_VERDICTS)}）")
        if verdict == VERDICT_TRANSLATED:
            zh_name = clean_text(item.get("zh_name"))
            if not zh_name:
                # 判 translated 卻沒給中文名＝不合契約，不寫空草稿。
                raise CompanyZhNameRunnerError(
                    f"translated 判定缺 zh_name：{item}")
        else:
            # keep_original：查無市場慣用中文名 → **中文欄留空**（2026-07-28 使用者
            # 第④點）。不再把英文原文塞進中文欄；顯示自然退到「正規化名稱」，
            # 符合「一律中文，沒中文才退英文正式名」的第①點。
            zh_name = None
        drafts.append({
            "company_code": code,
            "zh_name": zh_name,
            # AI 的輸入英文名（正規化名稱，空時為別稱原文）——寫回草稿列供對照。
            "source_name": name_by_code[code],
            "verdict": verdict,
            "review_status": DRAFT_REVIEW_STATUS,
        })

    if progress is not None:
        progress("寫回中文名草稿", 90)
    written = zh_store.write_drafts(drafts)

    if progress is not None:
        progress(f"公司中文名草稿完成（共 {written} 家）", 100)
    return {
        "candidates": len(candidates),
        "drafts_written": written,
        "cli_kind": cli_kind,
        "prompt_version": PROMPT_VERSION,
    }
