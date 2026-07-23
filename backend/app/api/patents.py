"""既有專利庫查詢 API（供案件比對選取被比對專利、專利總覽跨 workspace 顯示）。

GET /api/v1/patents/search：以專利號機制（六欄 COALESCE）ILIKE 搜尋既有庫專利，
回 {items:[{patent_id, patent_number, title, country_code, applicant_display_name}]}。

GET /api/v1/patents：分頁列出全庫專利（不分 workspace），每筆附 workspaces 歸屬陣列，
回 {items, total, limit, offset}。供專利總覽跨 workspace 顯示。

唯讀，SQL 集中於 app_layer.patent_queries。本層只做請求驗證、呼叫 service 與回傳；
不把 SQL 寫進 router、不綁死 workspace（供任何案件比對「從庫選被比對專利」重用）。
"""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query, Response

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


@router.get("/patents")
def list_patents(
    keyword: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """分頁列出全庫專利（不分 workspace），每筆標示所屬 workspace。

    keyword 選填（不帶＝不過濾，列全庫）；limit 上限 200（le=200，防一次撈全庫），
    offset ge=0，非法值由 FastAPI 擋成 422。回 {items, total, limit, offset}，
    每筆的 workspaces 為 [{workspace_id, workspace_name}]（不屬任何 workspace 者為空陣列）。
    """
    return patent_queries.list_patents(limit=limit, offset=offset, keyword=keyword)


# 內嵌圖 magic number → MIME；WIPS 匯出實測為 JPEG，但不寫死單一格式（PNG/GIF 亦可能出現）。
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF8", "image/gif"),
)


def _figure_media_type(blob: bytes) -> str:
    """依 magic number 判斷圖片 MIME；無法辨識時回泛用 application/octet-stream。"""
    for signature, media_type in _IMAGE_SIGNATURES:
        if blob.startswith(signature):
            return media_type
    return "application/octet-stream"


@router.get("/patents/{patent_id}/figure")
def get_patent_figure(patent_id: int = Path(..., ge=1)) -> Response:
    """取單筆專利的代表圖（WIPS Excel 內嵌圖，0026 起存於 core_layer.patents."主附圖"）。

    查無專利或該筆無圖一律回 404（不回 500）；前端據此顯示 placeholder。
    回應帶 ETag（內容 sha256）與 Cache-Control（不可變內容，快取一天），
    讓清單捲動時瀏覽器不重複下載同一張圖——一批 1900 張約 30MB，這是必要的。
    帶 If-None-Match 且相符時回 304，不重送內容。
    """
    blob = patent_queries.get_patent_figure(patent_id)
    if blob is None:
        raise HTTPException(status_code=404, detail="patent figure not found")
    etag = f'"{hashlib.sha256(blob).hexdigest()[:32]}"'
    return Response(
        content=blob,
        media_type=_figure_media_type(blob),
        headers={"ETag": etag, "Cache-Control": "public, max-age=86400"},
    )
