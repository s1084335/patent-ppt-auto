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
        get_draft_name,
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
        # confirm＝用草稿名（後端自查，前端不必回傳也不得竄改）；edit＝用使用者改過的名字。
        name = (item.name or "").strip() if item.action == "edit" else get_draft_name(item.code)
        if not name:
            raise HTTPException(
                status_code=422,
                detail=f"代碼 {item.code} 查無草稿名，無法以 action='confirm' 確認",
            )
        mapping[item.code] = {"canonical": name, "aliases": []}

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


class CodeGroup(BaseModel):
    """一組 = 代碼 + 正規化名稱 + N 個變體（對應 UI 一列可展開多格）。"""

    code: str = Field(min_length=1, max_length=64)
    company_name: str = Field(min_length=1, max_length=200)
    variants: list[str] = Field(default_factory=list)


class ConfirmCodesRequest(BaseModel):
    groups: list[CodeGroup] = Field(min_length=1)


def group_aliases_by_code(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 company_aliases 列聚成「代碼 → 變體清單」，供既有代碼區兩層展開。"""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("申請人代碼") or "").strip()
        if not code:
            continue
        entry = grouped.setdefault(
            code,
            {"code": code, "company_name": row.get("公司名稱") or "", "variants": []},
        )
        alias = str(row.get("別稱") or "").strip()
        if alias and alias not in entry["variants"]:
            entry["variants"].append(alias)
    return sorted(grouped.values(), key=lambda g: g["code"])


def _group_field(group: Any, name: str) -> Any:
    """CodeGroup（pydantic）與 dict（測試）兩種輸入都能取值。"""
    if isinstance(group, dict):
        return group.get(name)
    return getattr(group, name, None)


def groups_to_alias_mapping(groups: list[Any]) -> dict[str, dict[str, Any]]:
    """轉成既有 apply_confirmed_display_names 的 mapping 形狀。

    {代碼: {"canonical": 正規化名, "aliases": [變體, ...]}}
    空白變體格剔除——UI 的「＋」會留下未填的格子，寫進去會變成垃圾別稱。
    """
    mapping: dict[str, dict[str, Any]] = {}
    for group in groups:
        code = str(_group_field(group, "code") or "").strip()
        name = str(_group_field(group, "company_name") or "").strip()
        raw_variants = _group_field(group, "variants") or []
        entry = mapping.setdefault(code, {"canonical": name, "aliases": []})
        for raw in raw_variants:
            alias = str(raw).strip()
            if alias and alias not in entry["aliases"]:
                entry["aliases"].append(alias)
    return mapping


def find_code_conflicts(groups: list[Any], existing: dict[str, str]) -> list[dict[str, str]]:
    """同一代碼配到不同正規化名稱＝真衝突（使用者定的唯一驗證）。

    代碼本身**不驗格式**——WIPS 編碼規則未知，擋錯會讓合法代碼輸不進去。
    比對批內彼此、以及與 DB 既有代碼。
    """
    conflicts: list[dict[str, str]] = []
    seen: dict[str, str] = {}
    for group in groups:
        code = str(_group_field(group, "code") or "").strip()
        name = str(_group_field(group, "company_name") or "").strip()
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
            '''
            SELECT "申請人代碼", "公司名稱", "別稱"
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
            '''
            SELECT DISTINCT "申請人代碼" AS code, "公司名稱" AS name
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
