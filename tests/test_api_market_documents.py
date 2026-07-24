"""市場資料 PDF 上傳 API 驗收（/api/v1/market-documents）。

市場 PDF 落**檔案系統**（MARKET_DOC_ROOT，NAS 佔位）——讀取者是本機 Companion CLI，
非 Railway 容器；DB 只存 metadata（與存 bytea 的 workspace_documents 不同）。

全程用真實暫存目錄（tmp_path）＋mock metadata store；覆蓋：
上傳落檔＋落 metadata、hash 落款、多份並存、空檔、非 PDF、path traversal、容量超限、
失敗清檔清列，以及「PDF 仍不得進專利匯入端點」的反向護欄。
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import settings
from backend.app.api import market_documents as md_api
from backend.app.main import app


client = TestClient(app)


class MarketDocumentUploadTests(unittest.TestCase):
    def setUp(self):
        # 真實暫存目錄當 MARKET_DOC_ROOT；store 全程 mock，不連 DB。
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        self._root_patch = mock.patch.object(
            md_api, "get_market_doc_root", return_value=self._root
        )
        self._root_patch.start()

        self._store = mock.patch.multiple(
            md_api.market_doc_store.MarketDocumentStore,
            record_document=mock.DEFAULT,
            delete_document=mock.DEFAULT,
            list_documents=mock.DEFAULT,
        )
        self._m = self._store.start()
        self._m["record_document"].return_value = 11

    def tearDown(self):
        self._store.stop()
        self._root_patch.stop()
        self._tmp.cleanup()

    def _post(self, filename, content, workspace_id=5):
        return client.post(
            "/api/v1/market-documents",
            params={"filename": filename, "workspace_id": workspace_id},
            content=content,
        )

    def _stored_files(self):
        return list(self._root.rglob("*.pdf"))

    def test_upload_pdf_lands_on_filesystem_and_records_metadata(self):
        """合法 PDF：內容落 MARKET_DOC_ROOT、metadata 進 DB、hash 落款、回 document_id。"""
        body = b"%PDF-1.4\nmarket size report\n"
        resp = self._post("market.pdf", body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["document_id"], 11)
        self.assertEqual(data["original_filename"], "market.pdf")
        self.assertEqual(data["workspace_id"], 5)
        self.assertEqual(data["byte_size"], len(body))

        files = self._stored_files()
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), body)

        # record_document 收到 hash 與落地檔名（stored_filename），供之後刪檔定位。
        self._m["record_document"].assert_called_once()
        kwargs = self._m["record_document"].call_args.kwargs
        self.assertEqual(kwargs["file_hash"], hashlib.sha256(body).hexdigest())
        self.assertEqual(kwargs["byte_size"], len(body))
        self.assertTrue(kwargs["stored_filename"].endswith(".pdf"))
        self._m["delete_document"].assert_not_called()

    def test_upload_multiple_documents_same_workspace(self):
        """同一 workspace 可多份並存，落地檔名不衝突（不覆蓋既有）。"""
        for name in ("a.pdf", "a.pdf", "b.pdf"):
            resp = self._post(name, b"%PDF-1.4 x")
            self.assertEqual(resp.status_code, 200, name)
        # 三次上傳（含兩次同原檔名）產生三個獨立落地檔，內容不互相覆蓋。
        self.assertEqual(len(self._stored_files()), 3)
        self.assertEqual(self._m["record_document"].call_count, 3)

    def test_reject_empty_file(self):
        """空檔 → 422，不落檔、不記 metadata。"""
        resp = self._post("empty.pdf", b"")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(self._stored_files(), [])
        self._m["record_document"].assert_not_called()

    def test_reject_non_pdf_extension(self):
        """市場資料通道只收 PDF：xlsx/csv/txt/xml/mdb 一律 422，不落檔。"""
        for name in ("sheet.xlsx", "data.csv", "notes.txt", "wips.xml", "legacy.mdb"):
            resp = self._post(name, b"data")
            self.assertEqual(resp.status_code, 422, name)
        self.assertEqual(self._stored_files(), [])
        self._m["record_document"].assert_not_called()

    def test_reject_path_traversal(self):
        """檔名帶路徑成分 → 422，不落檔。"""
        for name in ("../evil.pdf", "..\\evil.pdf", "sub/evil.pdf"):
            resp = self._post(name, b"data")
            self.assertEqual(resp.status_code, 422, name)
        self.assertEqual(self._stored_files(), [])
        self._m["record_document"].assert_not_called()

    def test_reject_over_capacity_streaming(self):
        """串流累計超限 → 413，且不留落地檔（清檔）。"""
        def gen():
            yield b"a" * 8
            yield b"b" * 8

        with mock.patch.object(settings, "MAX_IMPORT_UPLOAD_BYTES", 10):
            resp = self._post("big.pdf", gen())
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(self._stored_files(), [])

    def test_metadata_write_failure_cleans_file(self):
        """metadata 寫入失敗 → 刪除已落地檔，不留孤兒檔。"""
        self._m["record_document"].side_effect = RuntimeError("db down")
        resp = self._post("m.pdf", b"%PDF-1.4 body")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(self._stored_files(), [])


class MarketDocumentListTests(unittest.TestCase):
    def test_list_documents(self):
        with mock.patch.object(
            md_api.market_doc_store.MarketDocumentStore, "list_documents"
        ) as list_docs:
            list_docs.return_value = [
                {
                    "document_id": 11,
                    "original_filename": "market.pdf",
                    "stored_filename": "ws5-abc.pdf",
                    "byte_size": 2048,
                    "file_hash": "abc",
                    "uploaded_at": "2026-07-24T10:00:00+00:00",
                }
            ]
            resp = client.get("/api/v1/market-documents", params={"workspace_id": 5})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["documents"][0]["document_id"], 11)


class PatentImportStillRejectsPdfTests(unittest.TestCase):
    """反向護欄：開了市場 PDF 通道後，專利匯入端點**仍須拒收 PDF**。"""

    def test_patent_import_rejects_pdf(self):
        from backend.app.api import imports as imports_api

        with mock.patch.object(imports_api.job_repository, "create_job") as create_job:
            with mock.patch.object(imports_api.import_blob_store, "create_blob") as create_blob:
                resp = client.post(
                    "/api/v1/imports", params={"filename": "market.pdf"}, content=b"%PDF-1.4"
                )
        self.assertEqual(resp.status_code, 422)
        create_job.assert_not_called()
        create_blob.assert_not_called()


if __name__ == "__main__":
    unittest.main()
