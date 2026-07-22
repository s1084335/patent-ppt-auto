"""既有專利庫查詢 API（供案件比對選取被比對專利）。

GET /api/v1/patents/search：以專利號機制（六欄 COALESCE）ILIKE 搜尋既有庫專利，
回 {items:[{patent_id, patent_number, title, country_code, applicant_display_name}]}。

唯讀，SQL 集中於 app_layer.patent_queries。本層只做請求驗證、呼叫 service 與回傳；
不把 SQL 寫進 router、不綁死 workspace（供任何案件比對「從庫選被比對專利」重用）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from backend.app.app_layer import patent_queries


router = APIRouter(tags=["patents"])


@router.get("/patents/search")
def search_patents(
    q: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """以專利號搜尋既有庫專利（精確號或片段皆可）。

    q 必填（min_length=1，防空字串命中全庫）；limit 上限 200（le=200，防全表掃），
    非法值由 FastAPI 擋成 422。回 {items}，查無回空清單。單一批次 SQL、不逐筆查。
    """
    return patent_queries.search_patents(q=q, limit=limit)
