"""AI bridge runner 契約測試。

橋接器的責任是把需要外部 CLI 的 AI 任務從一般 worker 分離出來：
一般 worker 不必安裝 Claude CLI；AI bridge 可以部署在同一台 server、另一台
server，或 Lightning Studio 外的受控執行環境，只要能連同一個資料庫即可。
"""

from __future__ import annotations

import unittest
from unittest import mock

from backend.app.worker import ai_bridge
from backend.app.worker.queue_client import ProcessingJob


class FakeAiQueue:
    """記錄 AI bridge claim 參數的假 queue client。"""

    def __init__(self, job: ProcessingJob | None):
        """保存下一筆要回傳的 job，並記錄呼叫參數。"""
        self.job = job
        self.claimed_job_types: tuple[str, ...] | None = None
        self.claimed_job_id: int | None = None
        self.requeued_with: int | None = None
        self.heartbeats: list[tuple[str | None, int | None]] = []
        self.completed: list[dict[str, object]] = []

    def requeue_stale_jobs(self, *, stale_after_seconds: int) -> dict[str, int]:
        """模擬 stale job 回收；橋接器仍需沿用一般 worker 的保活規則。"""
        self.requeued_with = stale_after_seconds
        return {"failed_count": 0, "requeued_count": 0}

    def claim_next_job(self, *, worker_id: str, job_types=None):
        """記錄 bridge 是否只 claim AI job type。"""
        self.claimed_job_types = tuple(job_types or ())
        return self.job

    def claim_job_by_id(self, *, job_id: int, worker_id: str, job_types=None):
        """記錄 smoke 是否只 claim 自己建立的那筆 job。"""
        self.claimed_job_id = job_id
        self.claimed_job_types = tuple(job_types or ())
        return self.job if self.job and self.job.job_id == job_id else None

    def heartbeat(self, *, job_id: int, worker_id: str, current_stage=None, progress_percent=None):
        """記錄 smoke heartbeat。"""
        self.heartbeats.append((current_stage, progress_percent))

    def complete_job(self, *, job_id: int, worker_id: str, result_json: dict[str, object]):
        """記錄 smoke 完成結果。"""
        self.completed.append(result_json)


def _ai_job() -> ProcessingJob:
    """建立一筆 ai:narrative 測試 job。"""
    return ProcessingJob(
        job_id=88,
        job_type="ai:narrative",
        status="queued",
        workspace_id=None,
        payload_json={"based_on_version": "report_trial_x"},
        result_json=None,
        progress_percent=0,
        current_stage="queued",
        attempt_count=0,
        max_attempts=3,
    )


class AiBridgeTests(unittest.TestCase):
    """AI bridge 只消費 AI 任務，並沿用既有 execute_job 流程。"""

    def test_run_once_claims_only_ai_jobs(self):
        """bridge run-once 必須用 AI job type 過濾 claim。"""
        store = FakeAiQueue(_ai_job())
        with mock.patch.object(ai_bridge, "execute_job", return_value={"status": "succeeded"}) as patched:
            result = ai_bridge.run_once(
                worker_id="ai-bridge-test",
                stale_after_seconds=120,
                store=store,
            )

        self.assertEqual(store.claimed_job_types, ("ai:narrative",))
        self.assertEqual(store.requeued_with, 120)
        patched.assert_called_once()
        self.assertEqual(result, {"status": "succeeded"})

    def test_run_once_idle_when_no_ai_job(self):
        """沒有 AI job 時回 idle，不處理一般 worker 任務。"""
        store = FakeAiQueue(None)
        result = ai_bridge.run_once(
            worker_id="ai-bridge-test",
            stale_after_seconds=120,
            store=store,
        )
        self.assertEqual(result["status"], "idle")
        self.assertEqual(store.claimed_job_types, ("ai:narrative",))

    def test_parser_accepts_run_once(self):
        """bridge CLI 支援 run-once，方便 smoke test 與排程單步驗收。"""
        args = ai_bridge.build_parser().parse_args(["run-once", "--worker-id", "ai-bridge-test"])
        self.assertEqual(args.command, "run-once")
        self.assertEqual(args.worker_id, "ai-bridge-test")

    def test_parser_accepts_smoke(self):
        """bridge CLI 支援受控 DB smoke，不必建立外部假資料。"""
        args = ai_bridge.build_parser().parse_args(["smoke", "--worker-id", "ai-bridge-smoke"])
        self.assertEqual(args.command, "smoke")
        self.assertEqual(args.worker_id, "ai-bridge-smoke")

    def test_smoke_claims_only_its_own_job_and_completes(self):
        """DB smoke 建立專屬 job 後只 claim 該 job，不會吃掉其他 queued AI 任務。"""
        job = _ai_job()
        store = FakeAiQueue(job)

        with mock.patch.object(ai_bridge.job_repository, "create_job", return_value=job) as create_job:
            result = ai_bridge.run_smoke(worker_id="ai-bridge-smoke", store=store)

        create_job.assert_called_once()
        self.assertEqual(create_job.call_args.args[0], "ai:narrative")
        self.assertTrue(create_job.call_args.kwargs["idempotency_key"].startswith("ai-bridge-smoke-"))
        self.assertEqual(store.claimed_job_id, job.job_id)
        self.assertEqual(store.claimed_job_types, ("ai:narrative",))
        self.assertEqual(store.heartbeats[-1], ("bridge_smoke_completing", 90))
        self.assertEqual(store.completed[0]["smoke"], True)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["job_id"], job.job_id)


if __name__ == "__main__":
    unittest.main()
