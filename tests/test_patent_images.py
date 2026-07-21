"""圖片管線基礎契約（純檔案系統，fake renderer，無 DB、不裝 pymupdf）。

驗證：路徑規約與 patent_number 安全化、sha256 目錄隔離、fake renderer 產頁圖與
contact sheet、重跑不覆蓋（已存在的檔不重 render）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class FakeRenderer:
    """測試用 renderer：寫入假 PNG 並記錄呼叫次數（驗重跑不覆蓋）。"""

    def __init__(self):
        self.page_calls = 0
        self.contact_calls = 0

    def render_page(self, pdf_path, page_index, out_path):
        self.page_calls += 1
        Path(out_path).write_bytes(b"FAKE-PNG-page")

    def render_contact_sheet(self, pdf_path, page_indices, out_path):
        self.contact_calls += 1
        Path(out_path).write_bytes(b"FAKE-PNG-contact")


class PathConventionTests(unittest.TestCase):
    def test_page_image_name_zero_padded(self):
        from backend.app.comparison.patent_images import page_image_name, CONTACT_SHEET_NAME
        self.assertEqual(page_image_name(1), "page_001.png")
        self.assertEqual(page_image_name(42), "page_042.png")
        self.assertEqual(CONTACT_SHEET_NAME, "contact_sheet.png")

    def test_patent_number_sanitized(self):
        from backend.app.comparison.patent_images import safe_patent_component
        # 空白、逗號、斜線等 path-unsafe 字元需安全化；不得產生路徑穿越
        self.assertNotIn("/", safe_patent_component("US 1,234,567 B2"))
        self.assertNotIn("\\", safe_patent_component("a\\b"))
        self.assertNotIn("..", safe_patent_component(".."))
        self.assertTrue(safe_patent_component("../../etc"))  # 非空且安全


class PipelineTests(unittest.TestCase):
    def _pipeline(self, base, renderer):
        from backend.app.comparison.patent_images import PatentImagePipeline
        return PatentImagePipeline(base, renderer)

    def test_render_produces_pages_and_contact_sheet(self):
        with tempfile.TemporaryDirectory() as base:
            rnd = FakeRenderer()
            res = self._pipeline(base, rnd).render("TWI123456", "abc123sha", "src.pdf", [1, 2])
            page_files = [Path(base) / p for p in res["page_paths"]]
            cs = Path(base) / res["contact_sheet_path"]
            self.assertTrue(all(f.exists() for f in page_files))
            self.assertTrue(cs.exists())
            self.assertEqual(len(res["page_paths"]), 2)
            self.assertTrue(res["page_paths"][0].endswith("page_001.png"))
            # 相對路徑含 patent_number 與 sha256 兩層
            self.assertIn("abc123sha", res["page_paths"][0])

    def test_sha256_directory_isolation(self):
        with tempfile.TemporaryDirectory() as base:
            rnd = FakeRenderer()
            p = self._pipeline(base, rnd)
            a = p.render("TWI123456", "sha_A", "src.pdf", [1])
            b = p.render("TWI123456", "sha_B", "src.pdf", [1])
            self.assertNotEqual(Path(a["page_paths"][0]).parent, Path(b["page_paths"][0]).parent)

    def test_rerun_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as base:
            rnd = FakeRenderer()
            p = self._pipeline(base, rnd)
            p.render("TWI123456", "sha_A", "src.pdf", [1, 2])
            self.assertEqual((rnd.page_calls, rnd.contact_calls), (2, 1))
            # 重跑：檔已存在，不再呼叫 renderer
            p.render("TWI123456", "sha_A", "src.pdf", [1, 2])
            self.assertEqual((rnd.page_calls, rnd.contact_calls), (2, 1))


class PymupdfRendererTests(unittest.TestCase):
    """真 renderer 契約：pymupdf 程式內生成迷你 PDF → 頁圖 PNG、contact sheet 真拼圖。"""

    @classmethod
    def setUpClass(cls):
        import pymupdf
        cls._tmp = tempfile.TemporaryDirectory()
        cls.pdf_path = str(Path(cls._tmp.name) / "mini.pdf")
        doc = pymupdf.open()
        # 兩頁 200x100，各寫一行字，供 render 驗證
        for i in range(2):
            page = doc.new_page(width=200, height=100)
            page.insert_text((20, 50), f"page {i + 1}")
        doc.save(cls.pdf_path)
        doc.close()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _renderer(self, **kwargs):
        from backend.app.comparison.patent_images import PymupdfRenderer
        return PymupdfRenderer(**kwargs)

    def test_render_page_produces_real_png(self):
        import pymupdf
        out = str(Path(self._tmp.name) / "p1.png")
        self._renderer().render_page(self.pdf_path, 1, out)
        data = Path(out).read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG"))  # 真 PNG magic bytes
        pix = pymupdf.Pixmap(out)
        self.assertGreater(pix.width, 0)
        self.assertGreater(pix.height, 0)

    def test_contact_sheet_is_real_mosaic(self):
        import pymupdf
        tile_w = 160
        single = str(Path(self._tmp.name) / "one.png")
        sheet = str(Path(self._tmp.name) / "sheet.png")
        r = self._renderer(tile_width=tile_w)
        r.render_contact_sheet(self.pdf_path, [1], single)
        r.render_contact_sheet(self.pdf_path, [1, 2], sheet)
        self.assertTrue(Path(sheet).read_bytes().startswith(b"\x89PNG"))
        one, two = pymupdf.Pixmap(single), pymupdf.Pixmap(sheet)
        # 兩頁排成 2 欄：寬度應為單頁 sheet 的兩倍（真拼圖，非單圖充數）
        self.assertEqual(two.width, tile_w * 2)
        self.assertEqual(one.width, tile_w)

    def test_pipeline_with_real_renderer(self):
        from backend.app.comparison.patent_images import PatentImagePipeline
        with tempfile.TemporaryDirectory() as base:
            res = PatentImagePipeline(base, self._renderer()).render(
                "TWI999999", "sha_real", self.pdf_path, [1, 2])
            self.assertEqual(len(res["page_paths"]), 2)
            for rel in res["page_paths"] + [res["contact_sheet_path"]]:
                self.assertTrue((Path(base) / rel).read_bytes().startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
