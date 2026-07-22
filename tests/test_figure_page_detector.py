"""案件比對 · 圖式頁偵測測試（Red 先行）。

驗證 detect_figure_pages() 能從專利說明書 PDF 中，依「該頁可抽取文字量」判定哪些是
圖式頁（整頁幾乎無文字層），供「只抽圖式頁」流程使用。用 pymupdf 程式內生成測試 PDF：
文字頁塞大量文字、圖式頁只畫圖形不放文字。
"""
from __future__ import annotations

import unittest

try:
    import pymupdf
    _HAS_PYMUPDF = True
except ImportError:
    _HAS_PYMUPDF = False

from backend.app.comparison.figure_page_detector import detect_figure_pages


def _build_pdf(path: str) -> None:
    """生成 4 頁 PDF：頁1-2 大量文字（說明書/claim），頁3-4 純圖形（圖式頁）。"""
    doc = pymupdf.open()
    # 文字頁：塞滿文字，模擬說明書與權利要求
    for _ in range(2):
        page = doc.new_page()
        body = ("This is a specification paragraph describing the invention in detail. " * 40)
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 780), body, fontsize=10)
    # 圖式頁：只畫線與矩形，不放可抽取文字
    for _ in range(2):
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(100, 100, 400, 400))
        page.draw_line(pymupdf.Point(100, 100), pymupdf.Point(400, 400))
        page.draw_circle(pymupdf.Point(250, 500), 80)
    doc.save(path)
    doc.close()


@unittest.skipUnless(_HAS_PYMUPDF, "需要 pymupdf")
class DetectFigurePagesTests(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.pdf = f"{self._tmp}/spec.pdf"
        _build_pdf(self.pdf)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_detects_only_figure_pages(self):
        """圖式頁（3、4，1-based）被辨識；文字頁（1、2）排除。"""
        pages = detect_figure_pages(self.pdf)
        self.assertEqual(pages, [3, 4])

    def test_returns_1_based_page_numbers(self):
        """回傳頁碼為 1-based（對齊 PatentImagePipeline 命名）。"""
        pages = detect_figure_pages(self.pdf)
        self.assertTrue(all(p >= 1 for p in pages))

    def test_all_text_pdf_returns_empty(self):
        """整份都是文字頁時，回空清單（無圖式頁可抽）。"""
        import pymupdf as fitz
        doc = fitz.open()
        for _ in range(3):
            page = doc.new_page()
            page.insert_textbox(fitz.Rect(50, 50, 550, 780),
                                "text " * 200, fontsize=10)
        allpdf = f"{self._tmp}/alltext.pdf"
        doc.save(allpdf)
        doc.close()
        self.assertEqual(detect_figure_pages(allpdf), [])


if __name__ == "__main__":
    unittest.main()
