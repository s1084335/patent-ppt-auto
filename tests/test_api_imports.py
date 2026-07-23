"""POST /api/v1/imports 驗收：串流上傳存進 DB blob＋建立 patent_import job。

2026-07-23 起上傳內容改存 app_layer.import_blobs（backend 與 worker 在 Railway 是不同容器、
檔案系統不共享），不再落地到 imports root；payload 帶 blob_id 而非 path。

全程不連真實 DB：mock `job_repository.create_job` 與 `import_blob_store` 的讀寫。覆蓋：
成功、空檔、不支援副檔名、MDB、path traversal、容量超限（Content-Length 與串流兩路）、
create_job 失敗清 blob，以及失敗一律不留孤兒 blob。
"""
from __future__ import annotations

import hashlib
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import settings
from backend.app.api import imports as imports_api
from backend.app.main import app


client = TestClient(app)


def _fake_job(payload):
    """組一個 job_to_dict 可讀的假 ProcessingJob。"""
    return SimpleNamespace(
        job_id=1,
        job_type="patent_import",
        status="queued",
        workspace_id=None,
        payload_json=payload,
        result_json=None,
        progress_percent=0,
        current_stage=None,
        attempt_count=0,
        max_attempts=3,
        error_message=None,
    )


class ImportApiTests(unittest.TestCase):
    def setUp(self):
        # blob store 全程 mock，不連真實 DB；內容累積在 self._written 供斷言。
        self._written = bytearray()
        self._blob = mock.patch.multiple(
            imports_api.import_blob_store,
            create_blob=mock.DEFAULT,
            append_chunk=mock.DEFAULT,
            finalize_blob=mock.DEFAULT,
            delete_blob=mock.DEFAULT,
        )
        self._m = self._blob.start()
        self._m["create_blob"].return_value = 42
        self._m["append_chunk"].side_effect = lambda blob_id, chunk: self._written.extend(chunk)

    def tearDown(self):
        self._blob.stop()

    def _post(self, filename, content):
        return client.post("/api/v1/imports", params={"filename": filename}, content=content)

    # ── 成功 ─────────────────────────────────────────────
    def test_success_creates_job_and_stores_blob(self):
        """合法上傳：內容進 DB blob、建 patent_import job、payload 含 blob_id/原檔名/hash＋預設 purpose。"""
        captured = {}

        def fake_create_job(job_type, payload, *, workspace_id=None, **kw):
            captured.update(job_type=job_type, payload=payload, workspace_id=workspace_id)
            return _fake_job(payload)

        body = b"col_a,col_b\nv1,v2\n"
        with mock.patch.object(imports_api.job_repository, "create_job", side_effect=fake_create_job):
            resp = self._post("sample.csv", body)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["job_type"], "patent_import")
        self.assertIsNone(captured["workspace_id"])
        payload = captured["payload"]
        # 2026-07-22：payload 一律帶用途標籤（未指定 workspace 時只多 purpose；預設 general）。
        # 2026-07-23：path 換成 blob_id（內容存 DB，不落檔）。
        self.assertEqual(set(payload.keys()),
                         {"blob_id", "original_filename", "file_hash", "purpose"})
        self.assertEqual(payload["blob_id"], 42)
        self.assertEqual(payload["purpose"], "general")
        self.assertEqual(payload["original_filename"], "sample.csv")
        self.assertEqual(payload["file_hash"], hashlib.sha256(body).hexdigest())
        # 內容完整進 blob，且 hash/大小落款。
        self.assertEqual(bytes(self._written), body)
        self._m["finalize_blob"].assert_called_once_with(
            42, file_hash=hashlib.sha256(body).hexdigest(), byte_size=len(body))
        # 成功不刪 blob（留給 worker 取用）。
        self._m["delete_blob"].assert_not_called()

    def test_supported_extensions(self):
        """xlsx/txt/xml 亦可上傳，且各自建立自己的 blob。"""
        with mock.patch.object(imports_api.job_repository, "create_job", side_effect=lambda *a, **k: _fake_job(a[1])):
            for name in ("a.xlsx", "b.txt", "c.xml"):
                resp = self._post(name, b"x")
                self.assertEqual(resp.status_code, 200, name)
                self.assertEqual(resp.json()["payload"]["blob_id"], 42)
        self.assertEqual(self._m["create_blob"].call_count, 3)

    # ── 驗證錯誤（不建 job、不留孤兒 blob）───────────────
    def test_reject_empty_file(self):
        """空檔 → 422，且刪除已建的空 blob。"""
        with mock.patch.object(imports_api.job_repository, "create_job") as m:
            resp = self._post("sample.csv", b"")
        self.assertEqual(resp.status_code, 422)
        m.assert_not_called()
        self._m["delete_blob"].assert_called_once_with(42)

    def test_reject_unsupported_extension(self):
        """副檔名不合法在建 blob 前就擋下 → 422，完全不碰 DB。"""
        with mock.patch.object(imports_api.job_repository, "create_job") as m:
            resp = self._post("evil.pdf", b"data")
        self.assertEqual(resp.status_code, 422)
        m.assert_not_called()
        self._m["create_blob"].assert_not_called()

    def test_reject_mdb(self):
        """Web 不接受 .mdb（避免 Linux worker 缺 pyodbc 才失敗）→ 422。"""
        with mock.patch.object(imports_api.job_repository, "create_job") as m:
            resp = self._post("legacy.mdb", b"data")
        self.assertEqual(resp.status_code, 422)
        m.assert_not_called()
        self._m["create_blob"].assert_not_called()

    def test_reject_path_traversal(self):
        """檔名帶路徑成分 → 422；驗證先於建 blob，不留孤兒內容。"""
        with mock.patch.object(imports_api.job_repository, "create_job") as m:
            for name in ("../evil.csv", "..\\evil.csv", "sub/evil.csv", "a/../../evil.csv"):
                resp = self._post(name, b"data")
                self.assertEqual(resp.status_code, 422, name)
        m.assert_not_called()
        self._m["create_blob"].assert_not_called()

    # ── 容量超限（413，兩路）────────────────────────────
    def test_reject_over_capacity_content_length(self):
        """Content-Length 超限 → 413（提早，連 blob 都不建）。"""
        with mock.patch.object(settings, "MAX_IMPORT_UPLOAD_BYTES", 10):
            with mock.patch.object(imports_api.job_repository, "create_job") as m:
                resp = self._post("big.csv", b"x" * 100)
        self.assertEqual(resp.status_code, 413)
        m.assert_not_called()
        self._m["create_blob"].assert_not_called()

    def test_reject_over_capacity_streaming(self):
        """無 Content-Length（chunked 串流）超限也要 413，且刪除本次 blob。"""
        def gen():
            yield b"a" * 8
            yield b"b" * 8  # 累計 16 > 10

        with mock.patch.object(settings, "MAX_IMPORT_UPLOAD_BYTES", 10):
            with mock.patch.object(imports_api.job_repository, "create_job") as m:
                resp = self._post("big.csv", gen())
        self.assertEqual(resp.status_code, 413)
        m.assert_not_called()
        self._m["delete_blob"].assert_called_once_with(42)

    # ── create_job 失敗 → 清 blob ───────────────────────
    def test_create_job_failure_cleans_up(self):
        """建 job 失敗時刪除本次 blob，不留孤兒內容。"""
        with mock.patch.object(imports_api.job_repository, "create_job", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                self._post("sample.csv", b"col\nval\n")
        self._m["delete_blob"].assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
