"""案件比對 · 圖片管線基礎（頁圖與 contact sheet 產製，render 依賴隔離）。

定案背景：比對報告寫作邏輯＝敘述搭配圖片（標準組成，非選配）；本輪只建管線基礎，
選圖與 PDF 組版後續輪做。

儲存規約（定案）：
- 專利資產目錄 `data/patent_assets/<patent_number>/<pdf_sha256>/`，跨 workspace 共用。
- 頁圖 `page_{n:03d}.png`、contact sheet `contact_sheet.png`。
- DB 只存最終選用圖片的相對路徑陣列，不存 metadata 或 binary（相對路徑由本模組回傳）。

render 依賴隔離：實際 PDF render 收攏成 PdfRenderer 介面注入；本輪不新增 pymupdf 依賴，
測試以 fake renderer 完成。重跑不覆蓋：已存在的頁圖／contact sheet 不重 render。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

CONTACT_SHEET_NAME = "contact_sheet.png"

# patent_number 目錄安全化：只保留英數與 . _ -，其餘（空白、逗號、斜線等）轉底線
_UNSAFE = re.compile(r"[^0-9A-Za-z._-]+")


def page_image_name(page_number: int) -> str:
    """頁圖檔名，三位零填充。"""
    return f"page_{page_number:03d}.png"


def safe_patent_component(patent_number: str) -> str:
    """把 patent_number 安全化為單層目錄名，防路徑穿越與 path-unsafe 字元。"""
    name = _UNSAFE.sub("_", str(patent_number).strip())
    name = name.strip("._")  # 去頭尾點與底線，避免 ".."／隱藏檔
    return name or "_"


class PdfRenderer(Protocol):
    """PDF render 單一介面（實作可注入 pymupdf 或 fake）。"""

    def render_page(self, pdf_path: str, page_index: int, out_path: str) -> None:
        """把 pdf_path 的指定頁 render 成 PNG 寫到 out_path。"""
        ...

    def render_contact_sheet(self, pdf_path: str, page_indices: list[int], out_path: str) -> None:
        """把指定頁組成 contact sheet 寫到 out_path。"""


class PymupdfRenderer:
    """PdfRenderer 真實作（pymupdf）。

    頁碼採 1-based（與 PatentImagePipeline 的 page_indices／page_{n:03d} 命名一致）。
    contact sheet 為真拼圖：各頁縮成等寬 tile，√n 欄網格、白底、Pixmap.copy 拼合。
    """

    def __init__(self, page_zoom: float = 2.0, tile_width: int = 320):
        import pymupdf  # 延遲 import：fake renderer 測試不需 pymupdf 也能載入本模組

        self._pymupdf = pymupdf
        self._zoom = page_zoom      # 頁圖解析度倍率（2.0 ≈ 144 dpi，報告可讀）
        self._tile_w = tile_width   # contact sheet 單格寬（px）

    def render_page(self, pdf_path: str, page_index: int, out_path: str) -> None:
        """把第 page_index 頁（1-based）render 成 PNG。"""
        with self._pymupdf.open(pdf_path) as doc:
            matrix = self._pymupdf.Matrix(self._zoom, self._zoom)
            doc[page_index - 1].get_pixmap(matrix=matrix).save(out_path)

    def render_contact_sheet(self, pdf_path: str, page_indices: list[int],
                             out_path: str) -> None:
        """把指定頁（1-based）縮圖拼成網格 contact sheet PNG。"""
        import math

        with self._pymupdf.open(pdf_path) as doc:
            tiles = []
            for n in page_indices:
                page = doc[n - 1]
                zoom = self._tile_w / max(page.rect.width, 1)  # 等寬縮放，高度依頁面比例
                tiles.append(page.get_pixmap(matrix=self._pymupdf.Matrix(zoom, zoom)))
            cols = max(1, math.ceil(math.sqrt(len(tiles))))
            rows = math.ceil(len(tiles) / cols)
            cell_h = max(t.height for t in tiles)
            sheet = self._pymupdf.Pixmap(
                self._pymupdf.csRGB,
                self._pymupdf.IRect(0, 0, cols * self._tile_w, rows * cell_h), False)
            sheet.clear_with(255)  # 白底
            for i, tile in enumerate(tiles):
                # set_origin 把 tile 座標平移到網格位置，copy 依 tile.irect 貼入
                tile.set_origin((i % cols) * self._tile_w, (i // cols) * cell_h)
                sheet.copy(tile, tile.irect)
            sheet.save(out_path)


class PatentImagePipeline:
    """頁圖與 contact sheet 產製管線；相對路徑基準為 base_dir。"""

    def __init__(self, base_dir: str, renderer: PdfRenderer):
        self._base = Path(base_dir)
        self._renderer = renderer

    def _asset_dir(self, patent_number: str, pdf_sha256: str) -> Path:
        return self._base / safe_patent_component(patent_number) / safe_patent_component(pdf_sha256)

    def render(self, patent_number: str, pdf_sha256: str, pdf_path: str,
               page_indices: list[int]) -> dict:
        """產指定頁的頁圖與 contact sheet；回傳相對於 base_dir 的路徑。重跑不覆蓋既有檔。"""
        target = self._asset_dir(patent_number, pdf_sha256)
        target.mkdir(parents=True, exist_ok=True)

        page_paths: list[str] = []
        for n in page_indices:
            out = target / page_image_name(n)
            if not out.exists():  # 重跑不覆蓋
                self._renderer.render_page(pdf_path, n, str(out))
            page_paths.append(str(out.relative_to(self._base)).replace("\\", "/"))

        cs = target / CONTACT_SHEET_NAME
        if not cs.exists():
            self._renderer.render_contact_sheet(pdf_path, page_indices, str(cs))

        return {
            "asset_dir": str(target.relative_to(self._base)).replace("\\", "/"),
            "page_paths": page_paths,
            "contact_sheet_path": str(cs.relative_to(self._base)).replace("\\", "/"),
        }
