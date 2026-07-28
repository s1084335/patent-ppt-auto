"""公司中文名草稿確認 API（三態流程的「確認」環節）。

補的是整條線唯一的斷點（2026-07-26 盤點發現，2026-07-27 補實作）：
`ai:company_zh_name` 產得出草稿、`apply_confirmed_display_names` 也早就存在，
但兩者之間**沒有任何 API／UI**——草稿寫進 DB 後看不到也確認不了，
`wips_metadata_json->'zh_name_verdict'` 有寫入端沒有讀取端。
後果：報表 COALESCE 第 1／2 順位（confirmed 對照名）永遠命不中，
公司名收斂實際只做到第 3 層（庫內統計名），中文名永遠出不來。

規格＝`.agents/context/company-zh-name-confirm-spec.md`，使用者 2026-07-27 修正兩點：
- **手動觸發**（推翻規格書「匯入後自動觸發」）：自動觸發容易失敗且無補救入口——
  沿 `ai:patent_note` 同日改手動的教訓（AI 首次失敗後同檔案再匯入被去重擋掉，
  那批資料再也不會被自動觸發）。故保留 `POST .../generate`。
- **略過保留草稿**：未裁決的草稿不能消失，使用者當下沒空處理，下次還要看得到。

三支端點：
- `GET  /company-zh-drafts?limit&offset`  列草稿（含 verdict 與原文名，供對照）
- `POST /company-zh-drafts/confirm`       逐筆裁決（confirm／edit／reject）
- `POST /company-zh-drafts/generate`      手動觸發 AI 產草稿

⚠ 寫入一律委派既有 `apply_confirmed_display_names`，**不在本檔另寫一套 SQL**：
去重規則、(代碼, lookup key) 唯一索引、re-canonicalize 既有列等規則只有那一份，
複寫必然漂移。
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from backend.app.db.job_repository import create_job


router = APIRouter(tags=["company-zh-drafts"])

# 確認寫回時記在 source_file 的來源標記，供日後追溯「這批是誰確認的」。
# 沿 PENDING_SQL 的 `display_name_curation%` 前綴——該前綴同時是「此代碼已裁決」
# 的判斷依據，確認過的代碼因此不會再出現在待中文化清單。
CONFIRM_SOURCE_LABEL = "display_name_curation:zh_name_review"


class ZhNameDecision(BaseModel):
    """單筆裁決。

    三態（規格 C）：
    - `confirm`：以**草稿名**確認（草稿名由後端自查，前端不必回傳、也不得竄改）
    - `edit`   ：以使用者改過的 `name` 確認
    - `reject` ：**略過**——不寫入、**草稿保留**，下次仍列在待確認清單
    """

    code: str = Field(min_length=1, max_length=64)
    action: Literal["confirm", "reject", "edit"]
    name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check_edit_has_name(self) -> "ZhNameDecision":
        """edit＝以改過的名字確認，缺 name 就沒東西可寫。"""
        if self.action == "edit" and not (self.name or "").strip():
            raise ValueError("action='edit' 必須提供 name")
        return self


class ZhNameConfirmRequest(BaseModel):
    """批次裁決請求；items 為空時視為無事可做（回 0，不報錯）。"""

    items: list[ZhNameDecision] = Field(default_factory=list)


@router.get("/company-zh-drafts")
def list_drafts(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """列出待確認的 AI 中文名草稿。

    回 {items: [{code, draft_name, verdict, created_at, original_name}, ...], total}。
    verdict＝translated（找到市場慣用中文名）／keep_original（查無，保留英文原文）；
    original_name＝該代碼收斂前的原始字面，供判斷這個中文名對不對。
    """
    from backend.app.derived.company_alias_importer import list_zh_name_drafts

    result = list_zh_name_drafts(limit=limit, offset=offset)
    return {**result, "limit": limit, "offset": offset}


@router.post("/company-zh-drafts/confirm")
def confirm_drafts(body: ZhNameConfirmRequest) -> dict[str, Any]:
    """逐筆裁決中文名草稿。

    - `confirm`／`edit` → 寫成正式顯示名（review_status='confirmed'）並**刪掉該草稿列**。
      寫入委派既有 `apply_confirmed_display_names`（唯一寫入規則來源）：
      (代碼, 別稱 lookup key) 已存在就 re-canonicalize 該列、否則插一列 confirmed。
      canonical 自身也會被納入別稱，確保顯示名字面可精確命中。
    - `reject` → **略過**：不寫入、**不刪草稿**，下次仍列在待確認清單。
      ⚠ 這是使用者 2026-07-27 明示要求：未裁決的草稿不能消失。

    確認後自動 enqueue `refresh_derived`——收斂名存 `report_patent_base`（全量重建表），
    不重跑 refresh 的話使用者回專利表會看到公司名沒變、誤以為確認失敗。
    全為 reject 時不必 refresh（沒有任何顯示名改變），不浪費一輪重建。
    """
    from backend.app.derived.company_alias_importer import (
        apply_confirmed_display_names,
        get_draft_names,
    )

    applied = [item for item in body.items if item.action in ("confirm", "edit")]
    skipped = [item for item in body.items if item.action == "reject"]

    if not applied:
        # 全部略過（或空請求）：草稿原樣保留，顯示名未變，不需要 refresh。
        return {
            "confirmed": 0,
            "skipped": len(skipped),
            "inserted": 0,
            "updated": 0,
            "drafts_cleared": 0,
            "refreshed_job_id": None,
        }

    mapping: dict[str, dict[str, Any]] = {}
    for item in applied:
        # 草稿的中文名與英文正式名皆由後端自查（前端不必回傳、也不得竄改）；
        # edit＝以使用者改過的**中文名**取代草稿中文名，英文正式名沿用草稿值。
        # ⚠ 2026-07-28 拆四欄：keep_original 草稿的中文欄本來就是空的，
        # 此時仍可確認（英文正式名照寫、顯示自然退英文），不再一律 422。
        zh_name, en_name = get_draft_names(item.code)
        if item.action == "edit":
            zh_name = (item.name or "").strip() or None
        if not (zh_name or en_name):
            raise HTTPException(
                status_code=422,
                detail=f"代碼 {item.code} 查無草稿名，無法確認",
            )
        mapping[item.code] = {"zh_name": zh_name, "normalized_name": en_name, "aliases": []}

    try:
        summary = apply_confirmed_display_names(mapping, CONFIRM_SOURCE_LABEL)
    except Exception as exc:  # noqa: BLE001 - 寫入失敗要讓前端看到原因，不吞錯
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    # 只刪已確認者的草稿列；略過的原樣留著。
    cleared = _clear_drafts([item.code for item in applied])

    # ⚠ 關鍵接線：confirmed 只寫進 company_aliases（對照表），**專利表顯示的收斂名存在
    # derived_layer.report_patent_base**——不重跑 refresh，使用者確認完會看到「表格沒變」。
    # 2026-07-28 起不傳 scope：refresh_derived 已整合為一律刷全部（含家族兩張表）。
    # 留著 scope='aliases' 不影響行為（handler 已忽略），但會誤導讀 code 的人以為還有分岔。
    refresh_job_id = create_job("refresh_derived", {})

    return {
        "confirmed": len(applied),
        "skipped": len(skipped),
        "inserted": summary.get("inserted", 0),
        "updated": summary.get("updated", 0),
        "drafts_cleared": cleared,
        "refreshed_job_id": refresh_job_id,
    }


def _clear_drafts(codes: list[str]) -> int:
    """刪掉這批代碼的 ai_suggested 草稿列（已確認，草稿無用）。

    ⚠ 只在 confirm／edit 後呼叫；reject（略過）不得走這裡，草稿要留著。
    一次 `= ANY(%s)` 刪整批，不逐筆往返。
    """
    if not codes:
        return 0
    import psycopg

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**get_connection_kwargs()) as conn:
        cur = conn.execute(
            "DELETE FROM derived_layer.company_aliases "
            "WHERE review_status = 'ai_suggested' AND \"申請人代碼\" = ANY(%s)",
            (codes,),
        )
        deleted = cur.rowcount
        conn.commit()
    return int(deleted or 0)


@router.post("/company-zh-drafts/generate")
def generate_drafts() -> dict[str, Any]:
    """手動觸發 AI 產中文名草稿（job `ai:company_zh_name`，走 Companion 領取）。

    ⚠ **手動而非匯入後自動**（2026-07-27 使用者定案，推翻規格書設計 A）：
    自動觸發一旦失敗就沒有補救入口——同批資料重匯會被去重擋掉，那些公司再也不會
    被自動問一次（`ai:patent_note` 同日踩過同一個坑才改手動）。

    runner 的 PENDING_SQL 只挑「無中文名、無裁決列、無既有草稿」的代碼，
    故重複按不會重問同一批、不重複燒 token。
    """
    job_id = create_job("ai:company_zh_name", {})
    return {"job_id": job_id}


# ══════════════ 專利權人代碼補齊（2026-07-28 使用者需求）══════════════
#
# 背景：company_aliases 0 筆 → applicant_display_name 四層 COALESCE 全落空 →
# 報表上 CHI HUA 三種寫法各佔一列、中文名全空。代碼覆蓋率實測 60 筆只有 3 筆，
# 「自動化能解決的」幾乎不存在，等於全部要人工補。
#
# ⚠ **與既有中文名確認線整合，不另造第三套**（使用者：「和現有代碼機制以及中文
# 重新整合，這樣公司這個才不會亂」）：
#   - 寫入一律委派 apply_confirmed_display_names（去重、re-canonicalize、
#     review_status 轉換的規則只有那一份）
#   - 「已處理過」沿用同一個 CONFIRM_SOURCE_LABEL 前綴判定，不另立標記
#   - AI 中文名沿用既有 ai:company_zh_name job，本區塊不自呼 CLI
#
# ⚠ 使用者定案的兩條紅線：
#   ① **代碼只能是使用者去 WIPS 查來的**——不自動產生、不 AI 建議
#   ② **系統不預先分組**——誰跟誰共用代碼由使用者填相同代碼決定；
#      待補清單只作參考與省打字，不暗示分組關係
#
# 成果顯現處（使用者指明）：瀏覽專利的專利權人相關欄位、以及所有用
# applicant_display_name／current_assignee_display_name 的報表。


# ══════════════ 無代碼備用方案：臨時代碼 TEMP:<slug>（2026-07-28）══════════════
#
# 為什麼需要：WIPS 要一定數量專利才給代碼，待補的 33 間多是 1–3 件的小廠，
# **可能永遠沒有代碼**。實測「別稱路徑完全不看代碼」——無代碼也能收斂——但擋在三處：
#   ① 前端 confirmCompanyCodes() 過濾 `g.code &&`
#   ② 後端 CodeGroup.code 有 min_length=1
#   ③ apply_confirmed_display_names 的 mapping key 是代碼，多組 NULL 會撞在一起
#
# ⚠ **臨時代碼不冒充 WIPS 代碼**：使用者定「代碼只能查 WIPS 給」，`TEMP:` 前綴是
# **系統標記**、不是假代碼——肉眼與程式都能一眼分辨，UI 亦標示「尚無 WIPS 代碼」。
# 選它而不是「放行 NULL 代碼」的理由：
#   - 不動唯一寫入路徑的 mapping 結構（key 仍是字串代碼），既有規則零漂移
#   - 多組無代碼彼此可區分（NULL 在 mapping dict 只會有一個 key）
#   - 日後補真代碼＝一句 UPDATE 把 TEMP:xxx 換成真代碼，該組所有變體一起換
TEMP_CODE_PREFIX = "TEMP:"


def make_temp_code(name: str) -> str:
    """由公司正式名產穩定的臨時代碼 `TEMP:<slug>`。

    穩定（同名恆同碼）：大小寫、前後空白、連續空白都先正規化——否則使用者重複送出
    同一家公司會每次長出一組新的。slug 只保留英數與連字號，長度截到 48
    （欄位上限 64，留給前綴與極端字元）。
    純中文等 slug 化後為空的名稱，退用名稱的 sha1 前 12 碼，仍保證同名同碼。
    """
    import hashlib
    import re as _re

    normalized = " ".join((name or "").strip().casefold().split())
    slug = _re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:48]
    if not slug:
        slug = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{TEMP_CODE_PREFIX}{slug}"


def is_temp_code(code: str | None) -> bool:
    """是否為系統產的臨時代碼（供 UI 標示與「補真代碼」入口判斷）。"""
    return bool(code) and str(code).startswith(TEMP_CODE_PREFIX)


class CodeGroup(BaseModel):
    """一組 = 代碼 + 公司中文名稱 + 正規化名稱 + N 個變體（2026-07-28 拆四欄）。

    - `code` **可空**：尚無 WIPS 代碼的小廠照樣要能建組（見 TEMP_CODE_PREFIX 說明）。
    - `zh_name`／`normalized_name` **兩欄都可空**（使用者第②點：不加 CHECK，
      可能先建組之後才補名）；兩欄皆空時該組寫不出東西，由端點側擋下。
    """

    code: str = Field(default="", max_length=64)
    zh_name: str = Field(default="", max_length=200)
    normalized_name: str = Field(default="", max_length=200)
    # 拆欄前的單欄輸入；保留供既有呼叫端／舊前端不中斷，語意由
    # groups_to_alias_mapping 的相容路徑（含 CJK 視為中文名）決定。
    company_name: str = Field(default="", max_length=200)
    variants: list[str] = Field(default_factory=list)


class ConfirmCodesRequest(BaseModel):
    groups: list[CodeGroup] = Field(min_length=1)


class RenameGroupRequest(BaseModel):
    """改公司名（中文／英文）；兩欄都可空，但不得同時為空（那等於刪名）。"""

    zh_name: str = Field(default="", max_length=200)
    normalized_name: str = Field(default="", max_length=200)


class PromoteCodeRequest(BaseModel):
    """把臨時代碼換成 WIPS 查來的真代碼。"""

    new_code: str = Field(min_length=1, max_length=64)


def group_aliases_by_code(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 company_aliases 列聚成「代碼 → 變體清單」，供既有代碼區兩層展開。

    ⚠ 2026-07-28 起變體帶 `id`：變體維護（移除單一變體）要能精確指到哪一列，
    只有字面的話同代碼下大小寫變體會指錯。同時帶 `is_canonical` 供前端把
    canonical 那列標成不可刪——刪了整組顯示名會壞（apply_confirmed_display_names
    會把正式名自身也寫成一列別稱）。
    """
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("申請人代碼") or "").strip()
        if not code:
            continue
        zh = str(row.get("公司中文名稱") or "").strip()
        en = str(row.get("正規化名稱") or "").strip()
        legacy = str(row.get("公司名稱") or "").strip()
        entry = grouped.setdefault(
            code,
            {
                "code": code,
                "is_temp": is_temp_code(code),
                "zh_name": zh,
                "normalized_name": en,
                # 顯示名：與報表同一順位（中文 → 英文正式名 → 舊欄）。
                "company_name": zh or en or legacy,
                "variants": [],
            },
        )
        # 同組各列的正式名理應一致；先出現的有值就留著，避免被後續空值蓋掉。
        entry["zh_name"] = entry["zh_name"] or zh
        entry["normalized_name"] = entry["normalized_name"] or en
        entry["company_name"] = entry["company_name"] or zh or en or legacy
        alias = str(row.get("別稱") or "").strip()
        if not alias:
            continue
        official = {n.casefold() for n in (zh, en, legacy) if n}
        entry["variants"].append({
            "id": row.get("id"),
            "alias": alias,
            # canonical 列＝別稱字面等於該組某個正式名；前端不給刪。
            "is_canonical": alias.casefold() in official,
        })
    return sorted(grouped.values(), key=lambda g: g["code"])


def _group_field(group: Any, name: str) -> Any:
    """CodeGroup（pydantic）與 dict（測試）兩種輸入都能取值。"""
    if isinstance(group, dict):
        return group.get(name)
    return getattr(group, name, None)


def _group_display_name(group: Any) -> str:
    """一組的顯示名（衝突判斷用）：中文 → 英文正式名 → 舊 company_name 鍵。

    保留 `company_name` 是為了相容既有呼叫端與測試的舊形狀。
    """
    for key in ("zh_name", "normalized_name", "company_name"):
        value = str(_group_field(group, key) or "").strip()
        if value:
            return value
    return ""


def groups_to_alias_mapping(groups: list[Any]) -> dict[str, dict[str, Any]]:
    """轉成唯一寫入路徑 apply_confirmed_display_names 的 mapping 形狀。

    {代碼: {"zh_name": 中文正式名, "normalized_name": 英文正式名, "aliases": [變體]}}

    - 無代碼組自動掛臨時代碼（見 make_temp_code）：mapping 的 key 必須能區分各組，
      否則多組無代碼會全部撞成同一個 key、合併成一家公司。
    - 空白變體格剔除——UI 的「＋」會留下未填的格子，寫進去會變成垃圾別稱。
    - 兩個正式名皆空的組略過（沒有名字就沒有東西可寫；不報錯，符合使用者第②點）。
    """
    mapping: dict[str, dict[str, Any]] = {}
    for group in groups:
        code = str(_group_field(group, "code") or "").strip()
        zh = str(_group_field(group, "zh_name") or "").strip()
        en = str(_group_field(group, "normalized_name") or "").strip()
        if not (zh or en):
            # 相容舊單欄輸入（company_name）：交給 writer 的 canonical 相容路徑判斷。
            legacy = str(_group_field(group, "company_name") or "").strip()
            if not legacy:
                continue
            zh_or_en_spec: dict[str, Any] = {"canonical": legacy}
        else:
            zh_or_en_spec = {"zh_name": zh or None, "normalized_name": en or None}
        if not code:
            code = make_temp_code(en or zh or zh_or_en_spec.get("canonical") or "")
        entry = mapping.setdefault(code, {**zh_or_en_spec, "aliases": []})
        for raw in _group_field(group, "variants") or []:
            alias = str(raw).strip()
            if alias and alias not in entry["aliases"]:
                entry["aliases"].append(alias)
    return mapping


def find_code_conflicts(groups: list[Any], existing: dict[str, str]) -> list[dict[str, str]]:
    """同一代碼配到不同正規化名稱＝真衝突（使用者定的唯一驗證）。

    代碼本身**不驗格式**——WIPS 編碼規則未知，擋錯會讓合法代碼輸不進去。
    比對批內彼此、以及與 DB 既有代碼。

    ⚠ 空代碼一律跳過（2026-07-28）：無代碼組各自會拿到不同的臨時代碼，
    拿空字串當 key 比對會把兩組不相干的公司誤判成代碼衝突。
    """
    conflicts: list[dict[str, str]] = []
    seen: dict[str, str] = {}
    for group in groups:
        code = str(_group_field(group, "code") or "").strip()
        if not code:
            continue
        name = _group_display_name(group)
        prior = seen.get(code) or existing.get(code)
        if prior and prior != name:
            conflicts.append({"code": code, "existing_name": prior, "new_name": name})
        seen.setdefault(code, name)
    return conflicts


_PENDING_CODES_SQL = """
    WITH names AS (
        SELECT lower(regexp_replace(BTRIM(x.raw_name), '\\s+', ' ', 'g')) AS lookup_key,
               x.raw_name,
               x.source_field,
               pp.patent_id
        FROM core_layer.patent_people pp
        CROSS JOIN LATERAL (VALUES
            (NULLIF(BTRIM(pp."申請人"), ''), '申請人'),
            (NULLIF(BTRIM(pp."標準化申請人"), ''), '標準化申請人'),
            (NULLIF(BTRIM(pp."最近專利權人[US,JP,KR,CN,CA,AU]"), ''), '最近專利權人'),
            (NULLIF(BTRIM(pp."標準當前專利權人[US,JP,KR,CN,CA,AU]"), ''), '標準當前專利權人'),
            (NULLIF(BTRIM(pp."最近受讓人[US,KR,CN]"), ''), '最近受讓人')
        ) AS x(raw_name, source_field)
        WHERE x.raw_name IS NOT NULL
    )
    SELECT n.lookup_key,
           min(n.raw_name) AS name,
           array_agg(DISTINCT n.source_field ORDER BY n.source_field) AS source_fields,
           count(DISTINCT n.patent_id) AS patent_count
    FROM names n
    WHERE NOT EXISTS (
        SELECT 1 FROM derived_layer.company_aliases ca
        WHERE ca.review_status = 'confirmed'
          AND lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g')) = n.lookup_key
    )
    GROUP BY n.lookup_key
    ORDER BY count(DISTINCT n.patent_id) DESC, min(n.raw_name)
    LIMIT %(limit)s
"""


@router.get("/company-codes/pending")
def list_pending_company_codes(limit: int = Query(default=200, ge=1, le=1000)) -> dict[str, Any]:
    """待補代碼的專利權人名稱（去重後的原始名稱＋專利數＋出現在哪些欄位）。

    排除（使用者定「已處理過的不再出現」）：已在 company_aliases 且
    review_status='confirmed' 的名稱。此判定與既有中文名確認線（display_name_curation
    前綴，見 CONFIRM_SOURCE_LABEL）指向同一批資料，不另立一套標記。

    ⚠ 只列清單、**不做任何分組推斷**——誰跟誰同一個代碼由使用者查 WIPS 後決定。
    """
    import psycopg
    from psycopg.rows import dict_row

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        rows = conn.execute(_PENDING_CODES_SQL, {"limit": limit}).fetchall()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get("/company-codes/existing")
def list_existing_company_codes() -> dict[str, Any]:
    """DB 既有代碼（供收合區塊兩層展開：代碼 → 該代碼下的公司變體）。"""
    import psycopg
    from psycopg.rows import dict_row

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        rows = conn.execute(
            # id 一併帶出：變體維護（移除單一變體）要能精確指到哪一列。
            '''
            SELECT id, "申請人代碼", "公司中文名稱", "正規化名稱", "公司名稱", "別稱"
            FROM derived_layer.company_aliases
            WHERE review_status = 'confirmed'
              AND NULLIF(BTRIM("申請人代碼"), '') IS NOT NULL
            ORDER BY "申請人代碼", "別稱"
            '''
        ).fetchall()
    groups = group_aliases_by_code([dict(r) for r in rows])
    return {"items": groups, "count": len(groups)}


@router.post("/company-codes/confirm")
def confirm_company_codes(body: ConfirmCodesRequest) -> dict[str, Any]:
    """使用者按「確定」後才寫入；代碼衝突則 409 不寫。

    ⚠ 寫入委派既有 apply_confirmed_display_names——去重（(代碼, lookup key) 同一把
    key）、既有列 re-canonicalize、review_status／source_type 轉換的規則只有那一份，
    本端點自寫 SQL 必然漂移（company_alias_importer docstring 亦明載此戒律）。

    寫完 enqueue refresh_derived：收斂名存 report_patent_base，不刷新使用者會看到
    「表格沒變」（既有中文名確認線踩過同一個坑）。成果顯現在瀏覽專利的專利權人
    欄位與相關報表。
    """
    import psycopg
    from psycopg.rows import dict_row

    from backend.app.db.connection import get_connection_kwargs
    from backend.app.derived.company_alias_importer import apply_confirmed_display_names

    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        existing_rows = conn.execute(
            # 顯示名順位同報表（中文 → 英文正式名 → 舊欄），衝突判斷才與畫面一致。
            '''
            SELECT DISTINCT "申請人代碼" AS code,
                   COALESCE(NULLIF(BTRIM("公司中文名稱"), ''),
                            NULLIF(BTRIM("正規化名稱"), ''),
                            NULLIF(BTRIM("公司名稱"), '')) AS name
            FROM derived_layer.company_aliases
            WHERE review_status = 'confirmed'
              AND NULLIF(BTRIM("申請人代碼"), '') IS NOT NULL
            '''
        ).fetchall()
    existing = {r["code"]: r["name"] for r in existing_rows}

    conflicts = find_code_conflicts(body.groups, existing)
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail={"message": "同一代碼對應到不同正規化名稱", "conflicts": conflicts},
        )

    mapping = groups_to_alias_mapping(body.groups)
    written = apply_confirmed_display_names(mapping, CONFIRM_SOURCE_LABEL)
    refresh_job_id = create_job("refresh_derived", {})
    return {"groups": len(mapping), "written": written, "refresh_job_id": refresh_job_id}


# ══════════════ 變體維護操作（2026-07-28 使用者需求）══════════════
#
# 使用者問「按完新增了，想把特定變體解除，怎麼做」→ 原本**做不到**：
# 既有 DELETE 只有兩處，都是清 AI 草稿（ai_suggested），不含 confirmed。
# 寫進去拔不掉，等於對照表只能加不能改。
#
# 三個操作與各自的護欄：
#   - DELETE 單一變體 → ⚠ **不得刪到 canonical 那列**（apply_confirmed_display_names
#     會把正式名自身也寫成一列別稱；刪了整組顯示名會壞）
#   - PATCH 改公司名   → **委派 apply_confirmed_display_names 的 re-canonicalize**，
#     不自寫 UPDATE（規則只有那一份）
#   - DELETE 整組       → 該組專利退回原始字面
# ⚠ 三者都**必須 enqueue refresh_derived**：顯示名存在 report_patent_base（全量重建
# 表），不刷新使用者會看到「表格沒變」——本專案既有教訓。


def _connect_dict_rows():
    """開一條 dict_row 連線（三個維護端點共用，避免各自重寫 import）。"""
    import psycopg
    from psycopg.rows import dict_row

    from backend.app.db.connection import get_connection_kwargs

    return psycopg.connect(**get_connection_kwargs(), row_factory=dict_row)


@router.delete("/company-codes/variants/{alias_id}")
def remove_company_variant(alias_id: int) -> dict[str, Any]:
    """移除單一變體（該寫法退回待補清單、其專利回原始字面）。

    ⚠ **canonical 列不得刪**（回 409）：`apply_confirmed_display_names` 會把該組的
    正式名自身也寫成一列別稱，那一列被刪掉，這組的顯示名就再也命不中自己的字面，
    整組顯示會壞。要拿掉整組請走 DELETE /company-codes/{code}。

    刪完 enqueue refresh_derived——不刷新畫面不會變。
    """
    with _connect_dict_rows() as conn:
        row = conn.execute(
            'SELECT "申請人代碼", "別稱", "公司中文名稱", "正規化名稱", "公司名稱" '
            "FROM derived_layer.company_aliases WHERE id = %s",
            (alias_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"找不到變體 id={alias_id}")
        alias = str(row.get("別稱") or "").strip().casefold()
        official = {
            str(row.get(k) or "").strip().casefold()
            for k in ("公司中文名稱", "正規化名稱", "公司名稱")
        }
        official.discard("")
        if alias in official:
            raise HTTPException(
                status_code=409,
                detail="這一列是該組的正式名本身，刪掉整組顯示名會壞；"
                       "要移除整組請刪除該代碼。",
            )
        conn.execute("DELETE FROM derived_layer.company_aliases WHERE id = %s", (alias_id,))
        conn.commit()
    return {"deleted": 1, "refresh_job_id": create_job("refresh_derived", {})}


@router.patch("/company-codes/{code}")
def rename_company_group(code: str, body: RenameGroupRequest) -> dict[str, Any]:
    """改一組的公司名（中文／英文正式名）。

    ⚠ **委派 apply_confirmed_display_names 的 re-canonicalize，不自寫 UPDATE**：
    去重、既有列改掛、review_status 轉換的規則只有那一份，自寫必然漂移
    （company_alias_importer docstring 明載此戒律）。

    做法：把該組**既有變體全部讀出來**當 aliases 一起送進 writer，writer 會把每一列
    都 re-canonicalize 到新名字。少了這步只有正式名那列會改名、其他變體仍掛舊名，
    顯示就會分裂。新的正式名字面本身也會被 writer 補成一列別稱。

    兩欄可空但不得同時為空（那等於把這組的名字清掉，語意上應該是刪組）。
    """
    from backend.app.derived.company_alias_importer import apply_confirmed_display_names

    zh = (body.zh_name or "").strip()
    en = (body.normalized_name or "").strip()
    if not (zh or en):
        raise HTTPException(status_code=422, detail="中文名與英文正式名不得同時為空；要移除整組請刪除該代碼。")

    with _connect_dict_rows() as conn:
        rows = conn.execute(
            'SELECT "別稱" FROM derived_layer.company_aliases '
            "WHERE \"申請人代碼\" = %s AND review_status = 'confirmed'",
            (code,),
        ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail=f"找不到代碼 {code}")
    aliases = [str(r.get("別稱") or "").strip() for r in rows]

    written = apply_confirmed_display_names(
        {code: {"zh_name": zh or None, "normalized_name": en or None,
                "aliases": [a for a in aliases if a]}},
        CONFIRM_SOURCE_LABEL,
    )
    return {"code": code, "written": written,
            "refresh_job_id": create_job("refresh_derived", {})}


@router.delete("/company-codes/{code}")
def delete_company_group(code: str) -> dict[str, Any]:
    """刪整組（該代碼的所有列）——該組專利退回原始字面。

    DELETE **必須帶代碼條件**，否則會清掉整張對照表（company_aliases 是長期資產）。
    刪完 enqueue refresh_derived，專利表的收斂名才會退回原字面。
    """
    with _connect_dict_rows() as conn:
        cur = conn.execute(
            'DELETE FROM derived_layer.company_aliases WHERE "申請人代碼" = %s',
            (code,),
        )
        deleted = int(cur.rowcount or 0)
        conn.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail=f"找不到代碼 {code}")
    return {"code": code, "deleted": deleted,
            "refresh_job_id": create_job("refresh_derived", {})}


@router.post("/company-codes/{code}/promote")
def promote_company_code(code: str, body: PromoteCodeRequest) -> dict[str, Any]:
    """把臨時代碼換成 WIPS 查來的真代碼（該組所有變體一起換）。

    ⚠ 使用者紅線「代碼只能是去 WIPS 查來的」在此仍成立：本端點不產生代碼，
    只是把使用者查到的真代碼填上去、取代系統的 `TEMP:` 標記。

    一句 UPDATE 換整組（不逐列往返）。目標代碼已存在時擋下（409）——那是把兩組
    合併，語意不同，應由使用者在代碼區用同一代碼重建，才會走 writer 的去重規則。
    """
    new_code = body.new_code.strip()
    if not new_code:
        raise HTTPException(status_code=422, detail="new_code 不得為空")
    if is_temp_code(new_code):
        raise HTTPException(status_code=422, detail="新代碼不得是 TEMP: 臨時代碼")

    with _connect_dict_rows() as conn:
        clash = conn.execute(
            'SELECT 1 FROM derived_layer.company_aliases WHERE "申請人代碼" = %s LIMIT 1',
            (new_code,),
        ).fetchone()
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"代碼 {new_code} 已存在；合併兩組請在代碼區以同一代碼重建。")
        cur = conn.execute(
            'UPDATE derived_layer.company_aliases SET "申請人代碼" = %s, updated_at = now() '
            'WHERE "申請人代碼" = %s',
            (new_code, code),
        )
        updated = int(cur.rowcount or 0)
        conn.commit()
    if not updated:
        raise HTTPException(status_code=404, detail=f"找不到代碼 {code}")
    return {"old_code": code, "new_code": new_code, "updated": updated,
            "refresh_job_id": create_job("refresh_derived", {})}
