"""案件比對 · 使用者上傳說明書 PDF → 抽圖式頁（取代失效的 WIPS 網路下載）。

流程：收使用者上傳的 PDF bytes → 驗 PDF magic bytes → 內容 sha256 落檔到
data/patent_assets/<patent_number>/<sha256>/source.pdf（同 hash 不重寫）→ 偵測圖式頁
→ 只 render 圖式頁 → 回傳圖式頁相對路徑供 DB 版本化保存（只存相對 key，不存 binary）。

來源改為使用者上傳（瀏覽器 → backend → server volume/NAS，永不落使用者本機），下游落檔
與 render 沿用既有規約，不新增儲存概念。實體 root 由 DATA_HOST_PATH（本機/雲端/NAS）決定。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from backend.app.comparison.figure_page_detector import detect_figure_pages
from backend.app.comparison.patent_images import (
    PatentImagePipeline,
    PymupdfRenderer,
    safe_patent_component,
)
from backend.app.comparison.pdf_fetch import (
    DEFAULT_BASE_DIR,
    PDF_MAGIC,
    SOURCE_PDF_NAME,
    PdfNotPdfError,
)


def _save_pdf(pdf_bytes: bytes, patent_number: str, base_dir: str) -> tuple[str, str]:
    """驗 PDF 並以內容雜湊落檔；回傳 (pdf_path, sha256)。同 hash 已存在不重寫。"""
    if not pdf_bytes.startswith(PDF_MAGIC):
        head = pdf_bytes[:40]
        raise PdfNotPdfError(f"上傳內容非 PDF（magic bytes 不符，開頭={head!r}）")
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    target_dir = Path(base_dir) / safe_patent_component(patent_number) / sha256
    pdf_path = target_dir / SOURCE_PDF_NAME
    if not pdf_path.exists():
        target_dir.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(pdf_bytes)
    return str(pdf_path), sha256


def ingest_specification_pdf(
    patent_number: str,
    pdf_bytes: bytes,
    base_dir: str = DEFAULT_BASE_DIR,
    renderer: Any | None = None,
) -> dict[str, Any]:
    """收上傳的說明書 PDF，落檔並只抽圖式頁。

    回傳 {pdf_path, sha256, figure_pages, figure_paths, contact_sheet_path}；
    figure_paths 為相對 base_dir 的圖式頁 PNG 路徑（DB 只存這些相對 key）。
    無圖式頁時 figure_paths 為空、不產 contact sheet。
    Raises:
        PdfNotPdfError: 上傳內容非 PDF。
    """
    pdf_path, sha256 = _save_pdf(pdf_bytes, patent_number, base_dir)

    # 偵測圖式頁（依可抽取文字量），只 render 這些頁
    figure_pages = detect_figure_pages(pdf_path)
    if not figure_pages:
        return {
            "pdf_path": pdf_path,
            "sha256": sha256,
            "figure_pages": [],
            "figure_paths": [],
            "contact_sheet_path": None,
        }

    pipeline = PatentImagePipeline(base_dir, renderer or PymupdfRenderer())
    rendered = pipeline.render(patent_number, sha256, pdf_path, figure_pages)
    return {
        "pdf_path": pdf_path,
        "sha256": sha256,
        "figure_pages": figure_pages,
        "figure_paths": rendered["page_paths"],
        "contact_sheet_path": rendered["contact_sheet_path"],
    }
