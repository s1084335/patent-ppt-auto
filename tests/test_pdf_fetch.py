"""說明書 PDF 下載契約（單元測試不打網路，http_get 以 fake 注入）。

儲存規約：data/patent_assets/<safe_pn>/<sha256>/source.pdf（sha256=內容雜湊）。
同 hash 已存在跳過重寫；非 200／非 PDF magic bytes／傳輸錯誤一律明確報錯且不落檔。
真下載煙測 gated：RUN_NET_TESTS=1 才跑（連結可能有時效/權限，失敗如實記錄不硬通）。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

PDF_BYTES = b"%PDF-1.4\n%mini\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def _fake_get(content=PDF_BYTES, status=200):
    """回固定 (status, content) 的 fake http_get，並記錄呼叫次數。"""
    calls = {"n": 0}

    def get(url, timeout):
        calls["n"] += 1
        return status, content
    get.calls = calls
    return get


class FetchPdfTests(unittest.TestCase):
    def _fetch(self, **kwargs):
        from backend.app.comparison.pdf_fetch import fetch_patent_pdf
        return fetch_patent_pdf(**kwargs)

    def test_download_saves_to_hash_path(self):
        with tempfile.TemporaryDirectory() as base:
            res = self._fetch(url="https://example.test/a.pdf", patent_number="TWI123456",
                              base_dir=base, http_get=_fake_get())
            expect_sha = hashlib.sha256(PDF_BYTES).hexdigest()
            self.assertEqual(res["sha256"], expect_sha)
            self.assertFalse(res["from_cache"])
            p = Path(res["pdf_path"])
            self.assertTrue(p.exists())
            self.assertEqual(p.name, "source.pdf")
            self.assertEqual(p.parent.name, expect_sha)
            self.assertEqual(p.parent.parent.name, "TWI123456")
            self.assertEqual(p.read_bytes(), PDF_BYTES)
            self.assertEqual(res["size_bytes"], len(PDF_BYTES))

    def test_same_hash_skips_rewrite(self):
        with tempfile.TemporaryDirectory() as base:
            first = self._fetch(url="u", patent_number="P1", base_dir=base, http_get=_fake_get())
            # 竄改既有檔為哨兵值：若第二次 fetch 跳過重寫，哨兵應保留
            Path(first["pdf_path"]).write_bytes(b"SENTINEL")
            second = self._fetch(url="u", patent_number="P1", base_dir=base, http_get=_fake_get())
            self.assertTrue(second["from_cache"])
            self.assertEqual(Path(second["pdf_path"]).read_bytes(), b"SENTINEL")

    def test_non_200_rejected_no_file(self):
        from backend.app.comparison.pdf_fetch import PdfFetchError
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(PdfFetchError) as ctx:
                self._fetch(url="u", patent_number="P2", base_dir=base,
                            http_get=_fake_get(status=404))
            self.assertIn("404", str(ctx.exception))
            self.assertEqual(list(Path(base).rglob("*.pdf")), [])

    def test_non_pdf_content_rejected(self):
        from backend.app.comparison.pdf_fetch import PdfFetchError, PdfNotPdfError
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(PdfNotPdfError):
                self._fetch(url="u", patent_number="P3", base_dir=base,
                            http_get=_fake_get(content=b"<html>login required</html>"))
            self.assertTrue(issubclass(PdfNotPdfError, PdfFetchError))
            self.assertEqual(list(Path(base).rglob("*.pdf")), [])

    def test_transport_error_wrapped(self):
        from backend.app.comparison.pdf_fetch import PdfFetchError

        def boom(url, timeout):
            raise TimeoutError("connect timeout")
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(PdfFetchError):
                self._fetch(url="u", patent_number="P4", base_dir=base, http_get=boom)

    def test_patent_number_sanitized_in_path(self):
        with tempfile.TemporaryDirectory() as base:
            res = self._fetch(url="u", patent_number="US 1,234/567 B2", base_dir=base,
                              http_get=_fake_get())
            dir_name = Path(res["pdf_path"]).parent.parent.name
            for ch in (" ", ",", "/", "\\"):
                self.assertNotIn(ch, dir_name)


@unittest.skipUnless(os.getenv("RUN_NET_TESTS") == "1", "RUN_NET_TESTS!=1 不打網路")
class NetSmokeTests(unittest.TestCase):
    """真下載煙測：自 patent_ppt_understanding 唯讀取一條連結。失敗樣態如實記錄（skip），不硬通。"""

    def test_real_download_one_link(self):
        import psycopg

        from backend.app.comparison.pdf_fetch import PdfFetchError, fetch_patent_pdf
        kw = dict(host="127.0.0.1", port=int(os.getenv("PGPORT", "5433")),
                  user=os.getenv("PGUSER", "postgres"), dbname="patent_ppt_understanding")
        if os.getenv("PGPASSWORD"):
            kw["password"] = os.getenv("PGPASSWORD")
        with psycopg.connect(**kw) as c:
            url, pn = c.execute(
                'SELECT a."文圖像文件(PDF)連結", coalesce(p."授權公告號", p.id::text) '
                "FROM core_layer.patent_attributes a JOIN core_layer.patents p ON p.id=a.patent_id "
                'WHERE a."文圖像文件(PDF)連結" IS NOT NULL AND a."文圖像文件(PDF)連結" <> \'\' '
                "ORDER BY a.patent_id LIMIT 1").fetchone()
        with tempfile.TemporaryDirectory() as base:
            try:
                res = fetch_patent_pdf(url=url, patent_number=pn, base_dir=base)
            except PdfFetchError as exc:
                self.skipTest(f"真下載失敗樣態（如實記錄）：{exc}")
            self.assertTrue(Path(res["pdf_path"]).exists())
            self.assertGreater(res["size_bytes"], 1024)


if __name__ == "__main__":
    unittest.main()
