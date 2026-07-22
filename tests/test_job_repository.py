"""job_repository 整合測試（0021 遷移版）：backend 建立/查詢/取消 ＋ worker 領取/心跳/
完成/失敗/回收，全部對拋棄式 DB patent_ppt_jobqueue 驗證（絕不碰 patent_ppt）。

沿用 test_postgres_topic_repository 的拋棄式 DB 模式：setUpModule 建庫 →
alembic upgrade head → 種最小 fixture。0021 對映：佇列表 processing_jobs →
app_layer.workflow_runs、簿記入 worker_state_json、結果落 workflow_outputs
（output_type='job_result:'+run_type）。測試 job 以 request_json 標記 _verify，結尾清除。

另含 e2e merge 一態：PostgresTopicRepository.queue_merge 排入 → worker 認領 →
handler 解析 topic_code→topic_id 後執行 → run succeeded → get_merge_history 讀得到。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from alembic import command
from alembic.config import Config

from backend.app.db import job_repository as jr


VERIFY_KEY = "_verify_marker"
TEST_DB = "patent_ppt_jobqueue"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# fixture 主鍵（與其他測試庫慣用號段區隔）
JOB_WS = 910001       # 一般佇列測試用 workspace
MERGE_WS = 910002     # e2e merge 用 workspace
WIPS = "wips_independent_claims"

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組出連測試庫的 psycopg 參數（與 test_postgres_topic_repository 同源）。"""
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
    """建拋棄式 DB → upgrade head → 種 fixture；admin 不可用則整組 skip。"""
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
    os.environ["PGDATABASE"] = TEST_DB  # job_repository 走 get_connection_kwargs()/get_pool()
    _reset_pool()

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    _seed()


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


def _seed():
    """種最小 fixture：佇列測試 workspace ＋ e2e merge 的 topic_run 狀態。"""
    # e2e merge：最新 topic_run 帶 topic_id/topic_code/status，供 handler 反查（裁決設計）
    state_merge = {"topics": [
        {"topic_id": 913001, "topic_code": "M01", "label": "合併源A", "status": "active",
         "topic_kind": "model", "label_source": "model", "doc_count": 1},
        {"topic_id": 913002, "topic_code": "M02", "label": "合併源B", "status": "active",
         "topic_kind": "model", "label_source": "model", "doc_count": 1},
        {"topic_id": 913003, "topic_code": "MX9", "label": "已合併主題", "status": "merged",
         "topic_kind": "model", "merged_into_topic_id": 913001, "doc_count": 0},
    ]}
    with psycopg.connect(**_kw(TEST_DB)) as c:
        for ws, name in ((JOB_WS, "jobqueue_ws"), (MERGE_WS, "merge_ws")):
            c.execute(
                "INSERT INTO app_layer.workspaces (workspace_id, workspace_name) VALUES (%s, %s)",
                (ws, name))
        c.execute(
            "INSERT INTO app_layer.workflow_runs (run_id, workspace_id, run_type, status) "
            "VALUES (911001, %s, 'clustering:wips_independent_claims', 'succeeded')",
            (MERGE_WS,))
        c.execute(
            "INSERT INTO derived_layer.topic_runs (run_id, workflow_run_id, source_field, topic_state_json) "
            "VALUES (912001, 911001, %s, %s)", (WIPS, Jsonb(state_merge)))
        c.commit()


def _connect():
    return psycopg.connect(**_kw(TEST_DB))


def _cleanup():
    """清除本檔標記的佇列 run（workflow_outputs 依 FK ON DELETE CASCADE 一併清除）。"""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM app_layer.workflow_runs "
            "WHERE request_json ? %s",
            (VERIFY_KEY,),
        )
        conn.commit()


def _make_payload(**extra):
    return {VERIFY_KEY: True, **extra}


class JobRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 取一個真實存在的 workspace_id 供 FK 測試（fixture 已種）
        with _connect() as conn:
            row = conn.execute(
                "SELECT workspace_id FROM app_layer.workspaces "
                "ORDER BY workspace_id LIMIT 1"
            ).fetchone()
        cls.ws_id = int(row[0]) if row else None
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
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            jr.create_job("clustering_calibrate", _make_payload(), workspace_id=999999)

    def test_invalid_job_type_raises(self):
        with self.assertRaises(ValueError):
            jr.create_job("bogus_type", _make_payload())

    def test_idempotency_returns_existing(self):
        key = "_verify_idem_key_1"
        a = jr.create_job("report_generate", _make_payload(), idempotency_key=key)
        b = jr.create_job("report_generate", _make_payload(), idempotency_key=key)
        self.assertEqual(a.job_id, b.job_id)
        rows = jr.list_jobs(limit=100)
        same_key = [j for j in rows if j.job_id == a.job_id]
        self.assertEqual(len(same_key), 1)

    def test_idempotency_terminal_same_request_returns_existing(self):
        key = "_verify_idem_key_terminal"
        job = jr.create_job("report_generate", _make_payload(), idempotency_key=key)
        client = jr.WorkerQueueClient()
        claimed = client.claim_next_job(worker_id="w-idem-terminal")
        self.assertEqual(claimed.job_id, job.job_id)
        client.complete_job(
            job_id=claimed.job_id,
            worker_id="w-idem-terminal",
            result_json={"ok": True},
        )
        retried = jr.create_job("report_generate", _make_payload(), idempotency_key=key)
        self.assertEqual(retried.job_id, job.job_id)

    def test_idempotency_same_key_different_fingerprint_creates_new_job(self):
        key = "_verify_idem_key_fingerprint"
        a = jr.create_job("report_generate", _make_payload(report="a"), idempotency_key=key)
        b = jr.create_job("report_generate", _make_payload(report="b"), idempotency_key=key)
        self.assertNotEqual(a.job_id, b.job_id)

    def test_list_jobs_rejects_negative_limit(self):
        with self.assertRaises(ValueError):
            jr.list_jobs(limit=-1)

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
        # 0021：結果不再掛在佇列列上，改由 workflow_outputs 版本化保存後讀回
        self.assertIsNone(done.result_json)
        result = jr.fetch_job_result(claimed.job_id, claimed.job_type)
        self.assertEqual(result["ok"], True)
        with _connect() as conn:
            output_type = conn.execute(
                "SELECT output_type FROM app_layer.workflow_outputs WHERE run_id = %s",
                (claimed.job_id,),
            ).fetchone()[0]
        self.assertEqual(output_type, "job_result:clustering_calibrate")

    def test_claim_is_atomic_no_double(self):
        j1 = jr.create_job("clustering_calibrate", _make_payload())
        j2 = jr.create_job("clustering_calibrate", _make_payload())
        # 兩條並發交易各跑 contract claim SQL（0021 版），持鎖不放，應領到不同筆
        CLAIM = """
        WITH next_job AS (
            SELECT run_id FROM app_layer.workflow_runs
            WHERE status='queued'
              AND COALESCE((worker_state_json->>'attempt_count')::int, 0)
                  < COALESCE((worker_state_json->>'max_attempts')::int, 3)
              AND request_json ? %s
            ORDER BY run_id FOR UPDATE SKIP LOCKED LIMIT 1)
        UPDATE app_layer.workflow_runs AS r
        SET status='running',
            worker_state_json = r.worker_state_json || jsonb_build_object(
                'locked_by', %s::text, 'locked_at', to_jsonb(now()),
                'heartbeat_at', to_jsonb(now()),
                'started_at', COALESCE(r.worker_state_json->'started_at', to_jsonb(now())),
                'attempt_count', COALESCE((r.worker_state_json->>'attempt_count')::int, 0) + 1,
                'current_stage', 'starting')
        FROM next_job WHERE r.run_id=next_job.run_id RETURNING r.run_id
        """
        c1 = _connect()
        c2 = _connect()
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
        # 取消一律寫 finished_at（即使是 running→cancelled；0021 收在 worker_state_json）
        with _connect() as conn:
            fin = conn.execute(
                "SELECT worker_state_json->>'finished_at' FROM app_layer.workflow_runs "
                "WHERE run_id = %s",
                (claimed.job_id,),
            ).fetchone()[0]
        self.assertIsNotNone(fin)

    def test_requeue_stale(self):
        jr.create_job("clustering_calibrate", _make_payload())
        client = jr.WorkerQueueClient()
        claimed = client.claim_next_job(worker_id="w-dead")
        # 手動把 heartbeat 推到過去，模擬 worker 死亡（0021：heartbeat_at 在 worker_state_json）
        with _connect() as conn:
            conn.execute(
                "UPDATE app_layer.workflow_runs "
                "SET worker_state_json = worker_state_json "
                "    || jsonb_build_object('heartbeat_at', to_jsonb(now() - interval '1 hour')) "
                "WHERE run_id = %s",
                (claimed.job_id,),
            )
            conn.commit()
        result = client.requeue_stale_jobs(stale_after_seconds=60)
        self.assertGreaterEqual(result["requeued_count"], 1)
        self.assertEqual(jr.get_job(claimed.job_id).status, "queued")


class TopicMergeEndToEndTests(unittest.TestCase):
    """e2e merge 一態：佇列排入 → 認領 → handler 解析（真 SQL 反查）→ succeeded → 歷史可讀。

    引擎 merge_workspace_topics 需要真實模型 artifact（BERTopic + reducer），拋棄式庫
    無從提供，故引擎以 mock 出席；解析層、佇列、結果回存、merge history 全走真 DB。
    """

    def test_merge_flow_succeeds_and_history_readable(self):
        from unittest import mock

        from backend.app.repositories.postgres_topic_repository import PostgresTopicRepository
        from backend.app.worker import handlers, runner

        repo = PostgresTopicRepository()
        queued = repo.queue_merge(
            MERGE_WS, WIPS, ["M01", "M02"], "合併後主題", "web-user", "e2e-merge-001")
        self.assertEqual(queued["status"], "queued")

        client = jr.WorkerQueueClient()
        claimed = client.claim_next_job(worker_id="w-e2e")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.job_id, queued["run_id"])
        self.assertEqual(claimed.job_type, "topic_merge")
        self.assertEqual(claimed.workspace_id, MERGE_WS)

        fake_summary = {"merge_run_id": 999, "merged_topic_ids": [913001, 913002]}
        with mock.patch.object(handlers, "merge_workspace_topics",
                               return_value=fake_summary) as engine:
            outcome = runner.execute_job(claimed, worker_id="w-e2e", store=client)
        self.assertEqual(outcome["status"], "succeeded")
        # 解析層把 topic_code 轉成引擎需要的 int topic_ids（裁決：只認 active 條目）
        self.assertEqual(engine.call_args.kwargs["topic_ids"], [913001, 913002])
        self.assertEqual(engine.call_args.kwargs["workspace_id"], MERGE_WS)
        self.assertEqual(engine.call_args.kwargs["source_field"], WIPS)

        done = jr.get_job(claimed.job_id)
        self.assertEqual(done.status, "succeeded")
        self.assertEqual(jr.fetch_job_result(claimed.job_id, "topic_merge"), fake_summary)
        hist = {h["merge_run_id"]: h for h in repo.list_merge_history(MERGE_WS, WIPS)}
        self.assertIn(claimed.job_id, hist)
        self.assertEqual(hist[claimed.job_id]["source_topics"], ["M01", "M02"])

    def test_merge_with_unresolvable_code_marks_run_failed(self):
        """裁決：任一 topic_code 查不到或非 active → run 標 failed 並留明確錯誤，不猜。"""
        from backend.app.worker import runner

        with _connect() as conn:
            run_id = conn.execute(
                "INSERT INTO app_layer.workflow_runs "
                "(workspace_id, run_type, status, request_json) "
                "VALUES (%s, 'topic_merge', 'queued', %s) RETURNING run_id",
                (MERGE_WS, Jsonb({"source_field": WIPS, "topic_keys": ["M01", "MX9"],
                                  "label": None, "requested_by": "web-user"})),
            ).fetchone()[0]
            conn.commit()
        client = jr.WorkerQueueClient()
        claimed = client.claim_next_job(worker_id="w-e2e-fail")
        self.assertEqual(claimed.job_id, run_id)
        outcome = runner.execute_job(claimed, worker_id="w-e2e-fail", store=client)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(jr.get_job(run_id).status, "failed")
        with _connect() as conn:
            error = conn.execute(
                "SELECT worker_state_json->>'error_message' FROM app_layer.workflow_runs "
                "WHERE run_id = %s",
                (run_id,),
            ).fetchone()[0]
        # MX9 在 fixture 中 status='merged'（非 active），錯誤訊息需指名該 code
        self.assertIn("MX9", error)
        self.assertIn("not active", error)


if __name__ == "__main__":
    unittest.main()
