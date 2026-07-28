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
