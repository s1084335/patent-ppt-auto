"""技術文獻上傳 API 驗收（/api/v1/workspaces/{id}/documents）。

技術文獻為**長期保存**內容（CLI 每次市場研究重讀），與用完即刪的 import_blobs 分離，
故走獨立表 app_layer.workspace_documents 與獨立端點。

全程不連真實 DB：mock `workspace_document_store` 的讀寫。覆蓋：
上傳（串流分塊、hash 落款）、列出（**護欄：不回 content**）、取內容、刪除、
空檔、非 PDF 副檔名、path traversal、容量超限（Content-Length 與串流兩路）、
失敗清列，以及「PDF 仍不得進專利匯入端點」的反向護欄。
"""
from __future__ import annotations

import hashlib
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import settings
from backend.app.api import workspaces as workspaces_api
from backend.app.main import app


client = TestClient(app)


class WorkspaceDocumentApiTests(unittest.TestCase):
    def setUp(self):
        # document store 全程 mock，不連真實 DB；內容累積在 self._written 供斷言。
        self._written = bytearray()
        self._store = mock.patch.multiple(
            workspaces_api.workspace_document_store,
            create_document=mock.DEFAULT,
            append_chunk=mock.DEFAULT,
            finalize_document=mock.DEFAULT,
            delete_document=mock.DEFAULT,
            list_documents=mock.DEFAULT,
            read_document=mock.DEFAULT,
        )
        self._m = self._store.start()
        self._m["create_document"].return_value = 7
        self._m["append_chunk"].side_effect = lambda document_id, chunk: self._written.extend(chunk)

    def tearDown(self):
        self._store.stop()

    def _post(self, filename, content, workspace_id=3):
        return client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            params={"filename": filename},
            content=content,
        )

    # ── 上傳 ─────────────────────────────────────────────
    def test_upload_pdf_stores_content_and_hash(self):
        """合法 PDF 上傳：內容分塊進 DB、hash 落款、回傳 document_id 與檔名。"""
        body = b"%PDF-1.4\nmarket report body\n"
        resp = self._post("industry.pdf", body)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["document_id"], 7)
        self.assertEqual(data["original_filename"], "industry.pdf")
        self.assertEqual(data["workspace_id"], 3)
        self.assertEqual(data["byte_size"], len(body))
        self.assertEqual(bytes(self._written), body)
        self._m["create_document"].assert_called_once_with(3, "industry.pdf")
        self._m["finalize_document"].assert_called_once_with(
            7, file_hash=hashlib.sha256(body).hexdigest()
        )
        self._m["delete_document"].assert_not_called()

    def test_upload_multiple_documents_same_workspace(self):
        """同一 workspace 可傳多份文獻（不寫死只能一份，不覆蓋既有）。"""
        for name in ("a.pdf", "b.pdf", "c.PDF"):
            resp = self._post(name, b"%PDF-1.4 x")
            self.assertEqual(resp.status_code, 200, name)
        self.assertEqual(self._m["create_document"].call_count, 3)
        self._m["delete_document"].assert_not_called()

    # ── 驗證錯誤（不留孤兒列）─────────────────────────────
    def test_reject_empty_file(self):
        """空檔 → 422，且刪除已建的空列。"""
        resp = self._post("empty.pdf", b"")
        self.assertEqual(resp.status_code, 422)
        self._m["delete_document"].assert_called_once_with(7)

    def test_reject_non_pdf_extension(self):
        """技術文獻通道只收 PDF：xlsx/csv/txt/xml 一律 422，且不建列。"""
        for name in ("sheet.xlsx", "data.csv", "notes.txt", "wips.xml", "legacy.mdb"):
            resp = self._post(name, b"data")
            self.assertEqual(resp.status_code, 422, name)
        self._m["create_document"].assert_not_called()

    def test_reject_path_traversal(self):
        """檔名帶路徑成分 → 422；驗證先於建列，不留孤兒內容。"""
        for name in ("../evil.pdf", "..\\evil.pdf", "sub/evil.pdf", "a/../../evil.pdf"):
            resp = self._post(name, b"data")
            self.assertEqual(resp.status_code, 422, name)
        self._m["create_document"].assert_not_called()

    # ── 容量超限（413，兩路）────────────────────────────
    def test_reject_over_capacity_content_length(self):
        """Content-Length 超限 → 413（提早，連列都不建）；沿用既有 MAX_IMPORT_UPLOAD_BYTES。"""
        with mock.patch.object(settings, "MAX_IMPORT_UPLOAD_BYTES", 10):
            resp = self._post("big.pdf", b"x" * 100)
        self.assertEqual(resp.status_code, 413)
        self._m["create_document"].assert_not_called()

    def test_reject_over_capacity_streaming(self):
        """無 Content-Length（chunked 串流）超限也要 413，且刪除本次列。"""
        def gen():
            yield b"a" * 8
            yield b"b" * 8  # 累計 16 > 10

        with mock.patch.object(settings, "MAX_IMPORT_UPLOAD_BYTES", 10):
            resp = self._post("big.pdf", gen())
        self.assertEqual(resp.status_code, 413)
        self._m["delete_document"].assert_called_once_with(7)

    # ── 列出（護欄：不得回 content）──────────────────────
    def test_list_documents_excludes_content(self):
        """列出只回 metadata：document_id／檔名／大小／時間／hash，**絕不含 content**。"""
        self._m["list_documents"].return_value = [
            {
                "document_id": 7,
                "original_filename": "industry.pdf",
                "byte_size": 2048,
                "file_hash": "abc",
                "uploaded_at": "2026-07-23T10:00:00+00:00",
            }
        ]
        resp = client.get("/api/v1/workspaces/3/documents")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        item = data["documents"][0]
        self.assertNotIn("content", item)
        self.assertEqual(item["document_id"], 7)
        self.assertEqual(item["byte_size"], 2048)
        # 護欄的本體在 store 層 SQL；此處確認 API 未走「撈全欄再篩」的路。
        self._m["list_documents"].assert_called_once_with(3)
        self._m["read_document"].assert_not_called()

    # ── 取內容 ───────────────────────────────────────────
    def test_get_document_content(self):
        """單筆取內容回原始位元組（供 Companion 落本機暫存檔給 CLI 讀）。"""
        body = b"%PDF-1.4\nbody\n"
        self._m["read_document"].return_value = {
            "original_filename": "industry.pdf",
            "content": body,
        }
        resp = client.get("/api/v1/workspaces/3/documents/7/content")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, body)
        self.assertEqual(resp.headers["content-type"], "application/pdf")
        self._m["read_document"].assert_called_once_with(3, 7)

    def test_get_document_content_not_found(self):
        """文獻不存在或不屬於該 workspace → 404。"""
        self._m["read_document"].return_value = None
        resp = client.get("/api/v1/workspaces/3/documents/999/content")
        self.assertEqual(resp.status_code, 404)

    # ── 刪除 ─────────────────────────────────────────────
    def test_delete_document(self):
        """刪除既有文獻 → 200；依 workspace_id + document_id 定位（不可跨 workspace 刪）。"""
        self._m["delete_document"].return_value = True
        resp = client.delete("/api/v1/workspaces/3/documents/7")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["deleted"])
        self._m["delete_document"].assert_called_once_with(7, workspace_id=3)

    def test_delete_document_not_found(self):
        """刪不存在的文獻 → 404。"""
        self._m["delete_document"].return_value = False
        resp = client.delete("/api/v1/workspaces/3/documents/999")
        self.assertEqual(resp.status_code, 404)


class PatentImportStillRejectsPdfTests(unittest.TestCase):
    """反向護欄：開了技術文獻 PDF 通道後，專利匯入端點**仍須拒收 PDF**。

    PDF 進 WIPS parser 必定失敗且可能誤判內容；兩條通道的副檔名白名單必須物理隔離。
    """

    def test_patent_import_rejects_pdf(self):
        from backend.app.api import imports as imports_api

        with mock.patch.object(imports_api.job_repository, "create_job") as create_job:
            with mock.patch.object(imports_api.import_blob_store, "create_blob") as create_blob:
                resp = client.post(
                    "/api/v1/imports", params={"filename": "report.pdf"}, content=b"%PDF-1.4"
                )
        self.assertEqual(resp.status_code, 422)
        create_job.assert_not_called()
        create_blob.assert_not_called()

    def test_web_import_suffixes_exclude_pdf(self):
        """WIPS 白名單常數本身不得含 .pdf（防後續有人「順手」加進去）。"""
        from backend.app.importers.import_paths import WEB_IMPORT_SUFFIXES

        self.assertNotIn(".pdf", WEB_IMPORT_SUFFIXES)


if __name__ == "__main__":
    unittest.main()
