"""案件比對 · 上傳說明書 PDF → 抽圖式頁 接線測試（Red 先行）。

驗證 ingest_specification_pdf()：收使用者上傳的 PDF bytes → 落檔到 patent_assets 內容
雜湊目錄 → 偵測圖式頁 → 只 render 圖式頁 → 存 illustrations 相對路徑。取代失效的 WIPS
網路下載；來源改為使用者上傳，下游落檔/render 沿用既有規約。
"""
from __future__ import annotations

import shutil
import tempfile
import unittest

try:
    import pymupdf
    _HAS_PYMUPDF = True
except ImportError:
    _HAS_PYMUPDF = False

from backend.app.comparison.specification_ingest import ingest_specification_pdf


def _build_spec_pdf() -> bytes:
    """生成含 2 文字頁 + 2 圖式頁的 PDF，回傳 bytes（模擬使用者上傳的說明書）。"""
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 780),
                            "specification text " * 60, fontsize=10)
    for _ in range(2):
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(100, 100, 400, 400))
        page.draw_circle(pymupdf.Point(250, 500), 80)
    data = doc.tobytes()
    doc.close()
    return data


@unittest.skipUnless(_HAS_PYMUPDF, "需要 pymupdf")
class IngestSpecificationTests(unittest.TestCase):

    def setUp(self):
        self._base = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._base, ignore_errors=True)

    def test_only_figure_pages_rendered(self):
        """上傳 PDF 後，只有圖式頁（3、4）被 render，文字頁不出圖。"""
        result = ingest_specification_pdf(
            patent_number="US9300001B",
            pdf_bytes=_build_spec_pdf(),
            base_dir=self._base,
        )
        # 只抽 2 張圖式頁
        self.assertEqual(len(result["figure_paths"]), 2)
        # 圖式頁命名對齊 page_003/page_004
        self.assertTrue(any("page_003" in p for p in result["figure_paths"]))
        self.assertTrue(any("page_004" in p for p in result["figure_paths"]))
        self.assertFalse(any("page_001" in p for p in result["figure_paths"]))

    def test_rejects_non_pdf(self):
        """非 PDF 內容拒收（防呆），不落檔。"""
        from backend.app.comparison.pdf_fetch import PdfNotPdfError
        with self.assertRaises(PdfNotPdfError):
            ingest_specification_pdf(
                patent_number="US9300001B",
                pdf_bytes=b"<html>not a pdf</html>",
                base_dir=self._base,
            )

    def test_paths_relative_to_base(self):
        """回傳相對路徑（相對 base_dir），供 DB 只存相對 key。"""
        result = ingest_specification_pdf(
            patent_number="US9300001B",
            pdf_bytes=_build_spec_pdf(),
            base_dir=self._base,
        )
        for p in result["figure_paths"]:
            self.assertFalse(p.startswith(self._base))
            self.assertIn("patent_assets" if "patent_assets" in p else "US9300001B", p)


if __name__ == "__main__":
    unittest.main()
