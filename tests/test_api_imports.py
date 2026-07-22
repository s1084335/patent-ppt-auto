"""POST /api/v1/imports 驗收：串流上傳落地＋建立 patent_import job。

全程不連真實 DB：mock `job_repository.create_job`，並用暫存目錄當 imports root
（環境變數 IMPORTS_ROOT）。覆蓋：成功、空檔、不支援副檔名、MDB、path traversal、
容量超限（Content-Length 與串流兩路）、create_job 失敗清檔，以及失敗一律不留孤兒目錄。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
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
        # imports root 導到暫存目錄（API 與 helper 都讀 settings.get_imports_root→IMPORTS_ROOT）。
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"IMPORTS_ROOT": str(self._root)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _post(self, filename, content):
        return client.post("/api/v1/imports", params={"filename": filename}, content=content)

    def _subdirs(self):
        return [p for p in self._root.iterdir() if p.is_dir()]

    # ── 成功 ─────────────────────────────────────────────
    def test_success_creates_job_and_saves_file(self):
        """合法上傳：落地受控目錄、建 patent_import job、payload 含 path/原檔名/hash＋預設 purpose。"""
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
        self.assertEqual(set(payload.keys()), {"path", "original_filename", "file_hash", "purpose"})
        self.assertEqual(payload["purpose"], "general")
        self.assertEqual(payload["original_filename"], "sample.csv")
        self.assertEqual(payload["file_hash"], hashlib.sha256(body).hexdigest())
        saved = Path(payload["path"])
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.read_bytes(), body)
        self.assertTrue(str(saved.resolve()).startswith(str(self._root.resolve())))
        # 成功匯入來源檔保留。
        self.assertEqual(len(self._subdirs()), 1)

    def test_supported_extensions(self):
        """xlsx/txt/xml 亦可上傳，且落地鎖在 imports root 下。"""
        with mock.patch.object(imports_api.job_repository, "create_job", side_effect=lambda *a, **k: _fake_job(a[1])):
            for name in ("a.xlsx", "b.txt", "c.xml"):
                resp = self._post(name, b"x")
                self.assertEqual(resp.status_code, 200, name)
                path = Path(resp.json()["payload"]["path"])
                self.assertTrue(str(path.resolve()).startswith(str(self._root.resolve())))

    # ── 驗證錯誤（不建 job、不留孤兒目錄）───────────────
    def test_reject_empty_file(self):
        with mock.patch.object(imports_api.job_repository, "create_job") as m:
            resp = self._post("sample.csv", b"")
        self.assertEqual(resp.status_code, 422)
        m.assert_not_called()
        self.assertEqual(self._subdirs(), [])

    def test_reject_unsupported_extension(self):
        with mock.patch.object(imports_api.job_repository, "create_job") as m:
            resp = self._post("evil.pdf", b"data")
        self.assertEqual(resp.status_code, 422)
        m.assert_not_called()
        self.assertEqual(self._subdirs(), [])

    def test_reject_mdb(self):
        """Web 不接受 .mdb（避免 Linux worker 缺 pyodbc 才失敗）→ 422。"""
        with mock.patch.object(imports_api.job_repository, "create_job") as m:
            resp = self._post("legacy.mdb", b"data")
        self.assertEqual(resp.status_code, 422)
        m.assert_not_called()
        self.assertEqual(self._subdirs(), [])

    def test_reject_path_traversal(self):
        with mock.patch.object(imports_api.job_repository, "create_job") as m:
            for name in ("../evil.csv", "..\\evil.csv", "sub/evil.csv", "a/../../evil.csv"):
                resp = self._post(name, b"data")
                self.assertEqual(resp.status_code, 422, name)
        m.assert_not_called()
        self.assertEqual(self._subdirs(), [])

    # ── 容量超限（413，兩路）────────────────────────────
    def test_reject_over_capacity_content_length(self):
        """Content-Length 超限 → 413（提早、未落地）。"""
        with mock.patch.object(settings, "MAX_IMPORT_UPLOAD_BYTES", 10):
            with mock.patch.object(imports_api.job_repository, "create_job") as m:
                resp = self._post("big.csv", b"x" * 100)
        self.assertEqual(resp.status_code, 413)
        m.assert_not_called()
        self.assertEqual(self._subdirs(), [])

    def test_reject_over_capacity_streaming(self):
        """無 Content-Length（chunked 串流）超限也要 413，且清除本次目錄。"""
        def gen():
            yield b"a" * 8
            yield b"b" * 8  # 累計 16 > 10

        with mock.patch.object(settings, "MAX_IMPORT_UPLOAD_BYTES", 10):
            with mock.patch.object(imports_api.job_repository, "create_job") as m:
                resp = self._post("big.csv", gen())
        self.assertEqual(resp.status_code, 413)
        m.assert_not_called()
        self.assertEqual(self._subdirs(), [])

    # ── create_job 失敗 → 清檔 ──────────────────────────
    def test_create_job_failure_cleans_up(self):
        """建 job 失敗時清除本次 UUID 目錄，不留孤兒檔。"""
        with mock.patch.object(imports_api.job_repository, "create_job", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                self._post("sample.csv", b"col\nval\n")
        self.assertEqual(self._subdirs(), [])


if __name__ == "__main__":
    unittest.main()
