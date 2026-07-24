"""孤兒 import_blobs 掃描清理驗收（維運：反覆匯入導致表膨脹，清無主 blob）。

無主判定雙重保護（缺一不可，紅線）：
1. 未被任何「非終結態（queued/running）patent_import job」的 request_json.blob_id 引用——
   還會重試／進行中的 job 其 blob 必須留著。
2. created_at 夠舊（超過門檻）——保護「剛 create_blob、job 還沒建」的上傳中內容。

沿 test_job_repository 的拋棄式 DB 模式（建庫 → alembic upgrade head → 直接種 import_blobs
與 workflow_runs 列），絕不碰正式庫 patent_ppt。本檔用獨立測試庫，與 test_job_repository 互不干擾。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from alembic import command
from alembic.config import Config

from backend.app.db import import_blob_store as blob_store


TEST_DB = "patent_ppt_bloborphan"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ORPHAN_WS = 920001

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組出連測試庫的 psycopg 參數（與 test_job_repository 同源）。"""
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    password = os.getenv("PGPASSWORD")
    if password:
        kw["password"] = password
    return kw


def _reset_pool():
    """關閉並清空 lazy 連線池單例，讓 get_pool() 依目前 env 重建（避免綁到別庫）。"""
    from backend.app.db import connection

    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:  # noqa: BLE001
            pass
        connection._pool = None


def setUpModule():
    """建拋棄式 DB → upgrade head → 種一個 workspace 供 FK；admin 不可用則整組 skip。"""
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"  # Windows localhost 走 IPv6 會慢
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB
    _reset_pool()

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    with psycopg.connect(**_kw(TEST_DB)) as c:
        c.execute(
            "INSERT INTO app_layer.workspaces (workspace_id, workspace_name) VALUES (%s, %s)",
            (ORPHAN_WS, "orphan_ws"))
        c.commit()


def tearDownModule():
    _reset_pool()
    for k, v in _prev_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    except Exception:  # noqa: BLE001
        pass


def _connect():
    return psycopg.connect(**_kw(TEST_DB))


class OrphanBlobCleanupTests(unittest.TestCase):
    """cleanup_orphan_blobs：無主且夠舊才刪；活躍 job 引用或太新一律保留。"""

    def tearDown(self):
        with _connect() as conn:
            conn.execute("DELETE FROM app_layer.workflow_runs")
            conn.execute("DELETE FROM app_layer.import_blobs")
            conn.commit()

    def _make_blob(self, *, age_hours: float) -> int:
        """建一列 import_blob，並把 created_at 回撥 age_hours 小時，回 blob_id。"""
        with _connect() as conn:
            blob_id = conn.execute(
                "INSERT INTO app_layer.import_blobs (original_filename, content) "
                "VALUES (%s, %s) RETURNING blob_id",
                ("orphan.csv", b"col\nval\n"),
            ).fetchone()[0]
            conn.execute(
                "UPDATE app_layer.import_blobs "
                "SET created_at = now() - make_interval(secs => %s) WHERE blob_id = %s",
                (float(age_hours) * 3600.0, blob_id))
            conn.commit()
        return int(blob_id)

    def _make_import_run(self, blob_id: int, *, status: str) -> int:
        """建一列 patent_import workflow_run，request_json 引用 blob_id，回 run_id。"""
        with _connect() as conn:
            run_id = conn.execute(
                "INSERT INTO app_layer.workflow_runs "
                "(workspace_id, run_type, status, request_json) "
                "VALUES (%s, 'patent_import', %s, %s) RETURNING run_id",
                (ORPHAN_WS, status, Jsonb({"blob_id": blob_id, "file_hash": "x"})),
            ).fetchone()[0]
            conn.commit()
        return int(run_id)

    def _blob_exists(self, blob_id: int) -> bool:
        with _connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM app_layer.import_blobs WHERE blob_id = %s", (blob_id,)
            ).fetchone()
        return row is not None

    def test_old_orphan_is_deleted(self):
        """夠舊、無任何 job 引用的 blob → 被刪除。"""
        blob_id = self._make_blob(age_hours=48)
        result = blob_store.cleanup_orphan_blobs(min_age_hours=24)
        self.assertIn(blob_id, result["blob_ids"])
        self.assertEqual(result["deleted_count"], 1)
        self.assertFalse(self._blob_exists(blob_id))

    def test_fresh_blob_is_kept(self):
        """🔴 剛上傳（太新）的 blob 即使無 job 引用也保留（防刪到上傳中內容）。"""
        blob_id = self._make_blob(age_hours=1)
        result = blob_store.cleanup_orphan_blobs(min_age_hours=24)
        self.assertNotIn(blob_id, result["blob_ids"])
        self.assertTrue(self._blob_exists(blob_id))

    def test_blob_referenced_by_queued_job_is_kept(self):
        """🔴 被 queued patent_import job 引用（會執行/重試）→ 即使夠舊也保留。"""
        blob_id = self._make_blob(age_hours=48)
        self._make_import_run(blob_id, status="queued")
        result = blob_store.cleanup_orphan_blobs(min_age_hours=24)
        self.assertNotIn(blob_id, result["blob_ids"])
        self.assertTrue(self._blob_exists(blob_id))

    def test_blob_referenced_by_running_job_is_kept(self):
        """🔴 被 running patent_import job 引用（進行中）→ 保留。"""
        blob_id = self._make_blob(age_hours=48)
        self._make_import_run(blob_id, status="running")
        result = blob_store.cleanup_orphan_blobs(min_age_hours=24)
        self.assertNotIn(blob_id, result["blob_ids"])
        self.assertTrue(self._blob_exists(blob_id))

    def test_blob_of_terminal_job_is_deleted(self):
        """只被終結態（failed/cancelled）job 引用的 blob → 視為孤兒刪除。"""
        blob_failed = self._make_blob(age_hours=48)
        self._make_import_run(blob_failed, status="failed")
        blob_cancelled = self._make_blob(age_hours=48)
        self._make_import_run(blob_cancelled, status="cancelled")
        result = blob_store.cleanup_orphan_blobs(min_age_hours=24)
        self.assertIn(blob_failed, result["blob_ids"])
        self.assertIn(blob_cancelled, result["blob_ids"])
        self.assertFalse(self._blob_exists(blob_failed))
        self.assertFalse(self._blob_exists(blob_cancelled))

    def test_dry_run_reports_without_deleting(self):
        """dry_run：只回報將刪的 blob_id，不真的刪。"""
        blob_id = self._make_blob(age_hours=48)
        result = blob_store.cleanup_orphan_blobs(min_age_hours=24, dry_run=True)
        self.assertIn(blob_id, result["blob_ids"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["deleted_count"], 0)
        self.assertTrue(self._blob_exists(blob_id))

    def test_negative_min_age_rejected(self):
        with self.assertRaises(ValueError):
            blob_store.cleanup_orphan_blobs(min_age_hours=-1)


if __name__ == "__main__":
    unittest.main()
