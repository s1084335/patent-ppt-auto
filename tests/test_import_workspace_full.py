"""完整輪1：import_wips_file（真實fixture CSV）→ workspace 建立/更新 → API 查回 patents。

覆蓋「用 fixture 匯入專利後，可建立或更新 workspace，且可用 API 查回 workspace patents」
的正向路徑與資料正確性。使用拋棄式 DB patent_ppt_import_full（upgrade head），不碰正式庫。

2026-07-23 第一輪：主流程 API/後端收尾驗收。"""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_import_full"
HEAD_REV = "head"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.environ["PGPASSWORD"]
    return kw


def setUpModule():
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB
    _reset_pool()

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, HEAD_REV)


def tearDownModule():
    _reset_pool()
    for k, v in _prev_env.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    except Exception:
        pass


def _reset_pool():
    from backend.app.db import connection

    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:
            pass
        connection._pool = None


def _members(workspace_id: int) -> list[int]:
    with psycopg.connect(**_kw(TEST_DB)) as c:
        row = c.execute(
            "SELECT patent_ids_json FROM app_layer.workspaces WHERE workspace_id=%s",
            (workspace_id,),
        ).fetchone()
    return sorted(int(v) for v in (row[0] or []))


def _purpose(workspace_id: int) -> str | None:
    with psycopg.connect(**_kw(TEST_DB)) as c:
        row = c.execute(
            "SELECT settings_json->>'purpose' FROM app_layer.workspaces WHERE workspace_id=%s",
            (workspace_id,),
        ).fetchone()
    return row[0]


class ImportCreateWorkspaceQueryApiTests(unittest.TestCase):
    """用 fixture CSV 匯入專利 → 建立 workspace → API 查回 patents 驗證。"""

    _seq = 0
    _cleanup_patents: list[int] = []
    _cleanup_workspaces: list[int] = []

    def _uniq(self, base: str) -> str:
        type(self)._seq += 1
        return f"{base}_{type(self)._seq}"

    def _import_csv(self, rows: str) -> list[int]:
        """寫暫時 CSV → 以 import_wips_file 匯入 → 回 patent_ids。"""
        from backend.app.importers.wips_importer import import_wips_file

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fixture.csv"
            p.write_text(f"申请号,标题,申请日\n{rows}", encoding="utf-8")
            summary = import_wips_file(p, dry_run=False)
        self.assertEqual(summary["status"], "imported")
        pids = summary["patent_ids"]
        self.assertTrue(pids, "import should return at least one patent_id")
        self._cleanup_patents.extend(pids)
        return pids

    def setUp(self):
        self._cleanup_patents = []
        self._cleanup_workspaces = []

    def tearDown(self):
        with psycopg.connect(**_kw(TEST_DB)) as c:
            if self._cleanup_workspaces:
                c.execute(
                    "DELETE FROM app_layer.workspaces WHERE workspace_id = ANY(%s)",
                    (self._cleanup_workspaces,),
                )
            if self._cleanup_patents:
                c.execute(
                    "DELETE FROM core_layer.patents WHERE id = ANY(%s)",
                    (self._cleanup_patents,),
                )
            c.commit()

    # ── Tests ────────────────────────────────────────────

    def test_import_create_workspace_then_api_returns_patents(self):
        """Fixture CSV 匯入 → create_workspace 建立 → API 查回正確專利。"""
        pids = self._import_csv(
            "TWROUND1A,首件專利,2020-01-01\nTWROUND1B,次件專利,2020-02-02\n"
        )

        from backend.app.app_layer.workspace_create import create_workspace

        ws = create_workspace(
            workspace_name=self._uniq("ws-create"),
            patent_ids=pids,
            created_by="test",
            purpose="general",
        )
        self._cleanup_workspaces.append(ws["workspace_id"])
        self.assertEqual(ws["patent_count"], len(pids))
        self.assertEqual(ws["purpose"], "general")

        # API 查回
        from fastapi.testclient import TestClient
        from backend.app.main import app

        client = TestClient(app)
        resp = client.get(f"/api/v1/workspaces/{ws['workspace_id']}/patents")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        returned_ids = {it["patent_id"] for it in data["items"]}
        for pid in pids:
            self.assertIn(pid, returned_ids, f"patent {pid} should be in workspace")

    def test_import_update_workspace_then_api_returns_union(self):
        """兩次 fixture 匯入 → 先建 workspace → union 加第二批 → API 查回合成。"""
        batch1 = self._import_csv("TWROUND2A,首批,2020-01-01\n")
        batch2 = self._import_csv("TWROUND2B,次批,2020-02-02\n")

        from backend.app.app_layer.workspace_create import create_workspace, add_patents_to_workspace

        ws = create_workspace(
            workspace_name=self._uniq("ws-update"),
            patent_ids=batch1,
            created_by="test",
        )
        self._cleanup_workspaces.append(ws["workspace_id"])

        update = add_patents_to_workspace(workspace_id=ws["workspace_id"], patent_ids=batch2)
        self.assertEqual(update["added_count"], 1)
        self.assertEqual(update["patent_count"], 2)

        # API 查回 → 聯集兩批
        from fastapi.testclient import TestClient
        from backend.app.main import app

        client = TestClient(app)
        resp = client.get(f"/api/v1/workspaces/{ws['workspace_id']}/patents")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        returned_ids = {it["patent_id"] for it in data["items"]}
        self.assertIn(batch1[0], returned_ids)
        self.assertIn(batch2[0], returned_ids)
        self.assertEqual(len(data["items"]), 2)

    def test_import_create_with_purpose_then_api_detail_shows_purpose(self):
        """建立 workspace 帶 purpose → detail API 投影 purpose。"""
        pids = self._import_csv("TWROUND3A,有用途,2020-01-01\n")

        from backend.app.app_layer.workspace_create import create_workspace

        ws = create_workspace(
            workspace_name=self._uniq("ws-purpose"),
            patent_ids=pids,
            created_by="test",
            purpose="case_comparison",
        )
        self._cleanup_workspaces.append(ws["workspace_id"])

        from fastapi.testclient import TestClient
        from backend.app.main import app

        client = TestClient(app)
        resp = client.get(f"/api/v1/workspaces/{ws['workspace_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("purpose"), "case_comparison")


class HandlerEndToEndTests(unittest.TestCase):
    """handle_patent_import 完整流程（真實 import_wips_file 與真實 blob store，不 mock）。

    2026-07-23 起來源檔走 DB（app_layer.import_blobs），不再依賴共享檔案系統：
    真實 CSV 內容存進 blob → 正確 SHA-256 → payload 帶 blob_id ＋ workspace 意圖 →
    handler 由 blob 取回落暫存檔完成匯入＋圈 workspace → 資料庫與 API 雙重驗證。
    """

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"PGDATABASE": TEST_DB})
        self._env.start()
        self._import_ids: list[int] = []
        self._ws_ids: list[int] = []

    def tearDown(self):
        self._env.stop()
        with psycopg.connect(**_kw(TEST_DB)) as c:
            if self._ws_ids:
                c.execute(
                    "DELETE FROM app_layer.workspaces WHERE workspace_id = ANY(%s)",
                    (self._ws_ids,),
                )
            if self._import_ids:
                c.execute(
                    "DELETE FROM core_layer.patents WHERE id = ANY(%s)",
                    (self._import_ids,),
                )
            c.commit()

    def _seed_blob(self, rows: str) -> tuple[int, str]:
        """把 CSV 內容存進真實 import_blobs，回 (blob_id, sha256)；模擬上傳端產物。"""
        from backend.app.db import import_blob_store

        content = f"申请号,标题,申请日\n{rows}".encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        blob_id = import_blob_store.create_blob("handler_fixture.csv")
        import_blob_store.append_chunk(blob_id, content)
        import_blob_store.finalize_blob(blob_id, file_hash=digest, byte_size=len(content))
        return blob_id, digest

    def test_handler_real_import_creates_workspace(self):
        """handler 由 DB blob 真實匯入 CSV → 建立 workspace → DB 與 API 雙重驗證。"""
        from backend.app.worker.handlers import handle_patent_import

        blob_id, digest = self._seed_blob("TWE2EA,案子甲,2020-01-01\nTWE2EB,案子乙,2020-02-02\n")
        payload = {
            "blob_id": blob_id,
            "original_filename": "handler_fixture.csv",
            "file_hash": digest,
            "new_workspace_name": "e2e-ws",
            "purpose": "general",
        }
        result = handle_patent_import(payload, mock.MagicMock())
        # 匯入完成後 blob 已清除（內容無保存價值，追溯靠 raw_records.source_file_hash）。
        with psycopg.connect(**_kw(TEST_DB)) as c:
            left = c.execute(
                "SELECT count(*) FROM app_layer.import_blobs WHERE blob_id = %s",
                (blob_id,)).fetchone()[0]
        self.assertEqual(left, 0)
        self.assertEqual(result["status"], "imported")
        self.assertIn("workspace_id", result)
        wid = result["workspace_id"]
        self._ws_ids.append(wid)
        self._import_ids.extend(result.get("patent_ids", []))

        # DB 驗證
        members = _members(wid)
        self.assertGreaterEqual(len(members), 2)
        self.assertEqual(_purpose(wid), "general")

        # API 驗證
        from fastapi.testclient import TestClient
        from backend.app.main import app

        client = TestClient(app)
        resp = client.get(f"/api/v1/workspaces/{wid}/patents")
        self.assertEqual(resp.status_code, 200)
        api_ids = {it["patent_id"] for it in resp.json()["items"]}
        self.assertTrue(api_ids.issuperset(set(members)))

    def test_handler_real_import_updates_existing_workspace(self):
        """handler 真實匯入兩批 → 先建 workspace → 第二批 union 進既有 workspace。"""
        from backend.app.worker.handlers import handle_patent_import

        # 第一批：直接匯入＋建 workspace
        blob1, digest1 = self._seed_blob("TWE2EA1,首批,2020-01-01\n")
        payload1 = {
            "blob_id": blob1,
            "original_filename": "handler_fixture.csv",
            "file_hash": digest1,
            "new_workspace_name": "e2e-update-ws",
            "purpose": "case_comparison",
        }
        r1 = handle_patent_import(payload1, mock.MagicMock())
        self.assertEqual(r1["status"], "imported")
        wid = r1["workspace_id"]
        self._ws_ids.append(wid)
        self._import_ids.extend(r1.get("patent_ids", []))

        # 第二批：匯入 + 指向既有 workspace
        blob2, digest2 = self._seed_blob("TWE2EA2,次批,2020-03-03\n")
        payload2 = {
            "blob_id": blob2,
            "original_filename": "handler_fixture.csv",
            "file_hash": digest2,
            "workspace_id": wid,
        }
        r2 = handle_patent_import(payload2, mock.MagicMock())
        self.assertEqual(r2["status"], "imported")
        self.assertEqual(r2["workspace_id"], wid)
        self._import_ids.extend(r2.get("patent_ids", []))

        # 聯集驗證：兩批都在
        members = _members(wid)
        self.assertEqual(len(members), 2)

        from fastapi.testclient import TestClient
        from backend.app.main import app

        client = TestClient(app)
        resp = client.get(f"/api/v1/workspaces/{wid}/patents")
        self.assertEqual(resp.status_code, 200)
        api_ids = {it["patent_id"] for it in resp.json()["items"]}
        for pid in members:
            self.assertIn(pid, api_ids)

    def test_handler_skips_workspace_without_params(self):
        """無 workspace 參數 → handler 只匯入不圈 workspace。"""
        from backend.app.worker.handlers import handle_patent_import

        blob_id, digest = self._seed_blob("TWE2EA3,無workspace,2020-01-01\n")
        payload = {"blob_id": blob_id, "original_filename": "handler_fixture.csv",
                   "file_hash": digest}
        result = handle_patent_import(payload, mock.MagicMock())
        self.assertEqual(result["status"], "imported")
        self.assertIsNone(result.get("workspace_id"))


if __name__ == "__main__":
    unittest.main()
