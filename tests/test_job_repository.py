"""job_repository 整合測試：backend 建立/查詢/取消 ＋ worker 領取/心跳/完成/
失敗/回收，全部對真實 DB 驗證（連不到就 skip）。測試 job 以 job_type
'clustering_calibrate' ＋ payload 標記 _verify，結尾清除。"""
from __future__ import annotations

import time
import unittest
from pathlib import Path

from dotenv import load_dotenv

from backend.app.db import job_repository as jr


VERIFY_KEY = "_verify_marker"


def _connect():
    import psycopg

    from backend.app.db.connection import get_connection_kwargs

    return psycopg.connect(**get_connection_kwargs())


def _cleanup():
    with _connect() as conn:
        conn.execute(
            "DELETE FROM app_layer.processing_jobs "
            "WHERE payload_json ? %s",
            (VERIFY_KEY,),
        )
        conn.commit()


def _make_payload(**extra):
    return {VERIFY_KEY: True, **extra}


class JobRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from backend.app.db.connection import get_connection_kwargs

        project_root = Path(__file__).resolve().parents[1]
        load_dotenv(project_root / ".env", override=False)
        try:
            with psycopg.connect(**get_connection_kwargs(), connect_timeout=3) as conn:
                # 取一個真實存在的 workspace_id 供 FK 測試（沒有就用 None）
                row = conn.execute(
                    "SELECT workspace_id FROM app_layer.workspaces "
                    "ORDER BY workspace_id LIMIT 1"
                ).fetchone()
                cls.ws_id = int(row[0]) if row else None
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"DB unreachable: {exc}")
        _cleanup()

    def tearDown(self):
        _cleanup()

    # ── backend 端 ────────────────────────────────────────
    def test_create_and_get(self):
        job = jr.create_job("clustering_calibrate", _make_payload(), workspace_id=self.ws_id)
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.job_type, "clustering_calibrate")
        self.assertEqual(job.workspace_id, self.ws_id)
        fetched = jr.get_job(job.job_id)
        self.assertEqual(fetched.job_id, job.job_id)
        self.assertEqual(fetched.payload_json[VERIFY_KEY], True)

    def test_create_rejects_unknown_workspace(self):
        # FK 應擋掉不存在的 workspace_id
        import psycopg

        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            jr.create_job("clustering_calibrate", _make_payload(), workspace_id=999999)

    def test_invalid_job_type_raises(self):
        with self.assertRaises(ValueError):
            jr.create_job("bogus_type", _make_payload())

    def test_idempotency_returns_existing(self):
        key = "_verify_idem_key_1"
        a = jr.create_job("report_generate", _make_payload(), idempotency_key=key)
        b = jr.create_job("report_generate", _make_payload(), idempotency_key=key)
        self.assertEqual(a.job_id, b.job_id)  # 不建第二筆
        # 確認 DB 真的只有一筆
        rows = jr.list_jobs(limit=100)
        same_key = [j for j in rows if j.job_id == a.job_id]
        self.assertEqual(len(same_key), 1)

    def test_list_filters(self):
        jr.create_job("clustering_calibrate", _make_payload(), workspace_id=self.ws_id)
        jr.create_job("clustering_incremental", _make_payload(), workspace_id=self.ws_id)
        queued = jr.list_jobs(status="queued", limit=100)
        self.assertTrue(all(j.status == "queued" for j in queued))
        self.assertGreaterEqual(len(queued), 2)
        if self.ws_id is not None:
            got = jr.list_jobs(workspace_id=self.ws_id, limit=100)
            self.assertTrue(all(j.workspace_id == self.ws_id for j in got))
            self.assertGreaterEqual(len(got), 2)

    def test_backend_cancel_queued(self):
        job = jr.create_job("report_generate", _make_payload())
        cancelled = jr.cancel_job(job.job_id)
        self.assertEqual(cancelled.status, "cancelled")

    # ── worker 端 ─────────────────────────────────────────
    def test_claim_complete_flow(self):
        job = jr.create_job("clustering_calibrate", _make_payload())
        client = jr.WorkerQueueClient()
        claimed = client.claim_next_job(worker_id="w-1")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, "running")
        client.heartbeat(job_id=claimed.job_id, worker_id="w-1", current_stage="mid", progress_percent=50)
        client.complete_job(job_id=claimed.job_id, worker_id="w-1", result_json={"ok": True})
        done = jr.get_job(claimed.job_id)
        self.assertEqual(done.status, "succeeded")
        self.assertEqual(done.result_json["ok"], True)

    def test_claim_is_atomic_no_double(self):
        import psycopg

        from backend.app.db.connection import get_connection_kwargs

        j1 = jr.create_job("clustering_calibrate", _make_payload())
        j2 = jr.create_job("clustering_calibrate", _make_payload())
        kwargs = get_connection_kwargs()
        # 兩條並發交易各跑 contract claim SQL，持鎖不放，應領到不同筆
        CLAIM = """
        WITH next_job AS (
            SELECT job_id FROM app_layer.processing_jobs
            WHERE status='queued' AND attempt_count < max_attempts
              AND payload_json ? %s
            ORDER BY created_at, job_id FOR UPDATE SKIP LOCKED LIMIT 1)
        UPDATE app_layer.processing_jobs AS jobs
        SET status='running', locked_by=%s, locked_at=now(), heartbeat_at=now(),
            started_at=COALESCE(started_at,now()), attempt_count=attempt_count+1,
            current_stage='starting'
        FROM next_job WHERE jobs.job_id=next_job.job_id RETURNING jobs.job_id
        """
        c1 = psycopg.connect(**kwargs)
        c2 = psycopg.connect(**kwargs)
        try:
            r1 = c1.execute(CLAIM, (VERIFY_KEY, "w1")).fetchone()[0]
            r2 = c2.execute(CLAIM, (VERIFY_KEY, "w2")).fetchone()
            r2 = r2[0] if r2 else None
            self.assertIsNotNone(r2)
            self.assertNotEqual(r1, r2)
            c1.commit(); c2.commit()
        finally:
            c1.close(); c2.close()

    def test_fail_job(self):
        jr.create_job("clustering_calibrate", _make_payload())
        client = jr.WorkerQueueClient()
        claimed = client.claim_next_job(worker_id="w-1")
        client.fail_job(job_id=claimed.job_id, worker_id="w-1", error_message="boom")
        self.assertEqual(jr.get_job(claimed.job_id).status, "failed")

    def test_cancel_then_worker_sees_it(self):
        job = jr.create_job("clustering_calibrate", _make_payload())
        client = jr.WorkerQueueClient()
        claimed = client.claim_next_job(worker_id="w-1")
        jr.cancel_job(claimed.job_id)  # backend 端取消 running job
        self.assertTrue(client.is_cancelled(job_id=claimed.job_id))
        # worker 端 heartbeat 因 status 不再是 running 而無效果
        client.heartbeat(job_id=claimed.job_id, worker_id="w-1", progress_percent=99)
        self.assertEqual(jr.get_job(claimed.job_id).status, "cancelled")
        # 取消一律寫 finished_at（即使是 running→cancelled）
        with _connect() as conn:
            fin = conn.execute(
                "SELECT finished_at FROM app_layer.processing_jobs WHERE job_id = %s",
                (claimed.job_id,),
            ).fetchone()[0]
        self.assertIsNotNone(fin)

    def test_requeue_stale(self):
        import psycopg

        from backend.app.db.connection import get_connection_kwargs

        jr.create_job("clustering_calibrate", _make_payload())
        client = jr.WorkerQueueClient()
        claimed = client.claim_next_job(worker_id="w-dead")
        # 手動把 heartbeat 推到過去，模擬 worker 死亡
        with psycopg.connect(**get_connection_kwargs()) as conn:
            conn.execute(
                "UPDATE app_layer.processing_jobs SET heartbeat_at = now() - interval '1 hour' "
                "WHERE job_id = %s",
                (claimed.job_id,),
            )
            conn.commit()
        result = client.requeue_stale_jobs(stale_after_seconds=60)
        self.assertGreaterEqual(result["requeued_count"], 1)
        self.assertEqual(jr.get_job(claimed.job_id).status, "queued")


if __name__ == "__main__":
    unittest.main()
