"""匯入檔案內容存 DB（跨容器共享）驗收：blob store、上傳端落 DB、worker 端取回。

背景（2026-07-23）：Railway 上 backend 與 worker 是不同容器、檔案系統不共享，worker 領到
patent_import job 後在自己的檔案系統找不到 backend 寫的檔案而失敗。兩容器共用同一個
PostgreSQL，故把上傳內容存進 DB 作為跨容器傳輸媒介。

本檔分三段：
1. ImportBlobStoreTests：blob store 以 mock psycopg 驗證 SQL 契約（分塊 append、hash、刪除）。
2. ImportApiBlobTests：POST /api/v1/imports 串流同時落 blob，payload 帶 blob_id。
3. HandlerBlobTests：worker 由 blob_id 取內容→暫存檔→匯入→暫存與 blob 清除。
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import settings
from backend.app.api import imports as imports_api
from backend.app.db import import_blob_store
from backend.app.main import app
from backend.app.worker import handlers


client = TestClient(app)


def _mock_pool(*, fetchone_returns=None):
    """組一個 get_pool() 相容的假連線池，回傳 (pool, cursor)。"""
    cur = mock.MagicMock()
    if fetchone_returns is not None:
        cur.fetchone.side_effect = list(fetchone_returns)
    cur_cm = mock.MagicMock()
    cur_cm.__enter__.return_value = cur
    cur_cm.__exit__.return_value = False

    conn = mock.MagicMock()
    conn.cursor.return_value = cur_cm
    conn_cm = mock.MagicMock()
    conn_cm.__enter__.return_value = conn
    conn_cm.__exit__.return_value = False

    pool = mock.MagicMock()
    pool.connection.return_value = conn_cm
    return pool, cur


class ImportBlobStoreTests(unittest.TestCase):
    """blob store 契約：建列、分塊 append、串流讀回、hash 驗證、刪除。"""

    def test_create_blob_returns_id(self):
        """create_blob 插入空內容列並回傳 blob_id。"""
        pool, cur = _mock_pool(fetchone_returns=[(7,)])
        with mock.patch.object(import_blob_store, "get_pool", return_value=pool):
            blob_id = import_blob_store.create_blob("a.csv")
        self.assertEqual(blob_id, 7)
        sql = cur.execute.call_args[0][0]
        self.assertIn("INSERT INTO app_layer.import_blobs", sql)

    def test_append_chunk_uses_concat(self):
        """append_chunk 以 bytea 串接寫入，不整包重寫。"""
        pool, cur = _mock_pool()
        with mock.patch.object(import_blob_store, "get_pool", return_value=pool):
            import_blob_store.append_chunk(7, b"abc")
        sql = cur.execute.call_args[0][0]
        self.assertIn("UPDATE app_layer.import_blobs", sql)
        self.assertIn("content ||", sql)

    def test_finalize_blob_stores_hash_and_size(self):
        """finalize_blob 落 file_hash 與 byte_size（供 worker 驗證）。"""
        pool, cur = _mock_pool()
        with mock.patch.object(import_blob_store, "get_pool", return_value=pool):
            import_blob_store.finalize_blob(7, file_hash="deadbeef", byte_size=3)
        sql = cur.execute.call_args[0][0]
        self.assertIn("file_hash", sql)
        self.assertIn("byte_size", sql)

    def test_write_blob_to_path_verifies_hash(self):
        """取回內容 hash 不符 → ValueError，且不留下暫存檔。"""
        content = b"col\nval\n"
        # 分塊取回：一塊內容後回空塊代表讀完。
        pool, cur = _mock_pool(fetchone_returns=[(memoryview(content),), (b"",)])
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "out.csv"
            with mock.patch.object(import_blob_store, "get_pool", return_value=pool):
                with self.assertRaises(ValueError):
                    import_blob_store.write_blob_to_path(7, target, expected_hash="deadbeef")
            self.assertFalse(target.exists())

    def test_write_blob_to_path_success(self):
        """hash 相符 → 內容寫入目標檔案（分塊取回後拼回原內容）。"""
        content = b"col\nval\n"
        digest = hashlib.sha256(content).hexdigest()
        pool, cur = _mock_pool(fetchone_returns=[(memoryview(content),), (b"",)])
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "out.csv"
            with mock.patch.object(import_blob_store, "get_pool", return_value=pool):
                import_blob_store.write_blob_to_path(7, target, expected_hash=digest)
            self.assertEqual(target.read_bytes(), content)
        # 逐塊 substring 取回，不是一次 SELECT content 整份進記憶體。
        self.assertIn("substring(content", cur.execute.call_args[0][0])

    def test_write_blob_missing_row_raises(self):
        """blob_id 不存在 → ValueError（worker 端明確失敗，不靜默產生空檔）。"""
        pool, cur = _mock_pool(fetchone_returns=[None])
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "out.csv"
            with mock.patch.object(import_blob_store, "get_pool", return_value=pool):
                with self.assertRaises(ValueError):
                    import_blob_store.write_blob_to_path(7, target, expected_hash="x")
            self.assertFalse(target.exists())

    def test_delete_blob(self):
        pool, cur = _mock_pool()
        with mock.patch.object(import_blob_store, "get_pool", return_value=pool):
            import_blob_store.delete_blob(7)
        self.assertIn("DELETE FROM app_layer.import_blobs", cur.execute.call_args[0][0])


class ImportApiBlobTests(unittest.TestCase):
    """POST /api/v1/imports：內容同時落 DB blob；既有防護與清理不變。"""

    def setUp(self):
        # blob store 全程 mock，不連真實 DB。
        self._blob = mock.patch.multiple(
            imports_api.import_blob_store,
            create_blob=mock.DEFAULT,
            append_chunk=mock.DEFAULT,
            finalize_blob=mock.DEFAULT,
            delete_blob=mock.DEFAULT,
        )
        self._blob_mocks = self._blob.start()
        self._blob_mocks["create_blob"].return_value = 99

    def tearDown(self):
        self._blob.stop()

    def _post(self, filename, content):
        return client.post("/api/v1/imports", params={"filename": filename}, content=content)

    def test_success_stores_blob_and_payload_carries_blob_id(self):
        """成功上傳：內容分塊進 blob、finalize 落 hash，payload 帶 blob_id。"""
        captured = {}

        def fake_create_job(job_type, payload, *, workspace_id=None, **kw):
            captured.update(payload=payload)
            return SimpleNamespace(
                job_id=1, job_type=job_type, status="queued", workspace_id=workspace_id,
                payload_json=payload, result_json=None, progress_percent=0,
                current_stage=None, attempt_count=0, max_attempts=3, error_message=None)

        body = b"col_a,col_b\nv1,v2\n"
        with mock.patch.object(imports_api.job_repository, "create_job", side_effect=fake_create_job):
            resp = self._post("sample.csv", body)

        self.assertEqual(resp.status_code, 200)
        payload = captured["payload"]
        self.assertEqual(payload["blob_id"], 99)
        self.assertEqual(payload["file_hash"], hashlib.sha256(body).hexdigest())
        # 內容確實有分塊寫進 blob。
        written = b"".join(c[0][1] for c in self._blob_mocks["append_chunk"].call_args_list)
        self.assertEqual(written, body)
        self._blob_mocks["finalize_blob"].assert_called_once()

    def test_rejects_unsupported_extension_before_blob(self):
        """副檔名不合法 → 422，且不建 blob。"""
        with mock.patch.object(imports_api.job_repository, "create_job") as m:
            resp = self._post("evil.pdf", b"data")
        self.assertEqual(resp.status_code, 422)
        m.assert_not_called()
        self._blob_mocks["create_blob"].assert_not_called()

    def test_over_capacity_streaming_deletes_blob(self):
        """串流超限 → 413，且刪除本次 blob（不留孤兒內容）。"""
        def gen():
            yield b"a" * 8
            yield b"b" * 8

        with mock.patch.object(settings, "MAX_IMPORT_UPLOAD_BYTES", 10):
            with mock.patch.object(imports_api.job_repository, "create_job") as m:
                resp = self._post("big.csv", gen())
        self.assertEqual(resp.status_code, 413)
        m.assert_not_called()
        self._blob_mocks["delete_blob"].assert_called_once_with(99)

    def test_create_job_failure_deletes_blob(self):
        """建 job 失敗 → 刪除 blob，不留孤兒內容。"""
        with mock.patch.object(imports_api.job_repository, "create_job",
                               side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                self._post("sample.csv", b"col\nval\n")
        self._blob_mocks["delete_blob"].assert_called_once_with(99)


class HandlerBlobTests(unittest.TestCase):
    """worker：由 blob_id 取內容 → 暫存檔 → import_wips_file → 清暫存與 blob。"""

    def setUp(self):
        self._content = "申请号,标题,申请日\nTW123456,測試,2020-01-01\n".encode("utf-8")
        self._hash = hashlib.sha256(self._content).hexdigest()

    def _payload(self, **over):
        p = {"blob_id": 99, "original_filename": "min.csv", "file_hash": self._hash}
        p.update(over)
        return p

    def _fake_write(self, blob_id, target, *, expected_hash):
        Path(target).write_bytes(self._content)

    def test_blob_payload_imports_and_cleans_temp(self):
        """blob_id 模式：暫存檔餵給 import_wips_file，結束後暫存檔消失。"""
        seen = {}

        def fake_import(path, *a, **kw):
            seen["path"] = Path(path)
            seen["exists_during"] = Path(path).is_file()
            seen["content"] = Path(path).read_bytes()
            return {"status": "imported", "patent_ids": []}

        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path",
                               side_effect=self._fake_write), \
             mock.patch.object(handlers.import_blob_store, "delete_blob") as del_blob, \
             mock.patch.object(handlers, "import_wips_file", side_effect=fake_import):
            result = handlers.handle_patent_import(self._payload(), mock.MagicMock())

        self.assertEqual(result["status"], "imported")
        self.assertTrue(seen["exists_during"])
        self.assertEqual(seen["content"], self._content)
        # 暫存檔用完清除。
        self.assertFalse(seen["path"].exists())
        # 匯入完成後 blob 不再需要（追溯靠 raw_records.source_file_hash）。
        del_blob.assert_called_once_with(99)

    def test_blob_hash_mismatch_fails_before_import(self):
        """blob 內容 hash 不符 → ValueError，不進 importer，暫存清除。"""
        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path",
                               side_effect=ValueError("import blob hash mismatch")), \
             mock.patch.object(handlers, "import_wips_file") as imp:
            with self.assertRaises(ValueError):
                handlers.handle_patent_import(self._payload(), mock.MagicMock())
        imp.assert_not_called()

    def test_blob_unsupported_suffix_rejected(self):
        """original_filename 副檔名不在 Web 白名單 → ValueError，不取 blob。"""
        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path") as w, \
             mock.patch.object(handlers, "import_wips_file") as imp:
            with self.assertRaises(ValueError):
                handlers.handle_patent_import(self._payload(original_filename="evil.pdf"),
                                              mock.MagicMock())
        w.assert_not_called()
        imp.assert_not_called()

    def test_duplicate_file_still_deletes_blob(self):
        """重複檔 → blob 一樣清除（內容已無保存價值）。"""
        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path",
                               side_effect=self._fake_write), \
             mock.patch.object(handlers.import_blob_store, "delete_blob") as del_blob, \
             mock.patch.object(handlers, "import_wips_file",
                               return_value={"status": "skipped_duplicate_file"}):
            result = handlers.handle_patent_import(self._payload(), mock.MagicMock())
        self.assertEqual(result["status"], "skipped_duplicate_file")
        del_blob.assert_called_once_with(99)


if __name__ == "__main__":
    unittest.main()
