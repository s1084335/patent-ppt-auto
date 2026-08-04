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
        self.failed: list[dict[str, object]] = []
        self.cancelled: list[dict[str, object]] = []
        self.cancelled_job_ids: set[int] = set()

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

    def fail_job(self, *, job_id: int, worker_id: str, error_message: str, current_stage: str = "failed"):
        """記錄橋接器失敗收斂結果，避免測試真的寫入資料庫。"""
        self.failed.append(
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "error_message": error_message,
                "current_stage": current_stage,
            }
        )

    def cancel_job(self, *, job_id: int, worker_id: str, error_message: str):
        """記錄橋接器取消收斂結果，避免測試真的寫入資料庫。"""
        self.cancelled.append({"job_id": job_id, "worker_id": worker_id, "error_message": error_message})

    def is_cancelled(self, *, job_id: int) -> bool:
        """讓 JobContext.check_cancelled 可在測試中被安全呼叫。"""
        return job_id in self.cancelled_job_ids


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


def _market_summary_job(payload: dict | None = None) -> ProcessingJob:
    """建立一筆 ai:market_summary 測試 job（市場摘要綁 workspace，payload 帶 workspace_id）。"""
    return ProcessingJob(
        job_id=91,
        job_type="ai:market_summary",
        status="queued",
        workspace_id=7,
        payload_json={"workspace_id": 7} if payload is None else payload,
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
        with mock.patch.object(ai_bridge, "execute_ai_job", return_value={"status": "succeeded"}) as patched:
            result = ai_bridge.run_once(
                worker_id="ai-bridge-test",
                stale_after_seconds=120,
                store=store,
            )

        # 只 claim AI 類任務（目前含 ai:narrative 與 ai:topic_label）；斷言集合而非固定字面值，
        # 新增 AI 任務類型時不必回頭改這條。
        self.assertEqual(set(store.claimed_job_types), set(ai_bridge.AI_JOB_TYPES))
        self.assertIn("ai:narrative", store.claimed_job_types)
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
        self.assertEqual(set(store.claimed_job_types), set(ai_bridge.AI_JOB_TYPES))

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

    def test_parser_accepts_doctor(self):
        """doctor 指令用來在正式環境先檢查 DB 與本機 CLI 條件。"""
        args = ai_bridge.build_parser().parse_args(["doctor", "--cli-kind", "claude"])
        self.assertEqual(args.command, "doctor")
        self.assertEqual(args.cli_kind, "claude")

    def test_execute_ai_job_completes_narrative_job(self):
        """AI bridge 自己執行 ai:narrative，不再透過一般 worker runner。"""
        job = _ai_job()
        store = FakeAiQueue(job)
        with mock.patch.object(ai_bridge, "_run_ai_narrative_job", return_value={"ok": True}) as patched:
            result = ai_bridge.execute_ai_job(job, worker_id="ai-bridge-test", store=store)

        patched.assert_called_once()
        self.assertEqual(store.heartbeats[0], ("running", 1))
        self.assertEqual(store.completed[0], {"ok": True})
        self.assertEqual(result, {"job_id": job.job_id, "status": "succeeded", "result": {"ok": True}})

    # 🔴 2026-08-04：test_execute_ai_job_routes_market_summary_job 已刪除——市場線整個移除（使用者定案），規格沒了測試就失去存在理由

    # 🔴 2026-08-04：test_market_summary_dispatch_requires_workspace_id 已刪除——市場線整個移除（使用者定案），規格沒了測試就失去存在理由

    def test_execute_ai_job_rejects_non_ai_job(self):
        """橋接器只處理 AI job，避免錯吃一般 worker 任務。"""
        job = ProcessingJob(
            job_id=89,
            job_type="report_generate",
            status="queued",
            workspace_id=None,
            payload_json={},
            result_json=None,
            progress_percent=0,
            current_stage="queued",
            attempt_count=0,
            max_attempts=3,
        )
        store = FakeAiQueue(job)

        result = ai_bridge.execute_ai_job(job, worker_id="ai-bridge-test", store=store)

        self.assertEqual(result["status"], "failed")
        self.assertIn("unsupported AI bridge job_type", store.failed[0]["error_message"])

    def test_doctor_reports_db_and_cli_status(self):
        """doctor 不寫入 workflow_runs，只回報 DB/CLI 是否可用。"""
        with (
            mock.patch.object(ai_bridge, "_db_check", return_value={"ok": True}),
            mock.patch.object(ai_bridge, "_cli_check", return_value={"ok": False, "binary": "claude"}),
        ):
            result = ai_bridge.run_doctor(cli_kind="claude")

        self.assertEqual(result["database"], {"ok": True})
        self.assertEqual(result["cli"], {"ok": False, "binary": "claude"})

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
        # smoke 仍只建立／claim ai:narrative 這一種（不因新增 AI 任務類型而改變 smoke 標的），
        # 但 claim 過濾沿用 bridge 的 AI 集合。
        self.assertEqual(set(store.claimed_job_types), set(ai_bridge.AI_JOB_TYPES))
        self.assertEqual(store.heartbeats[-1], ("bridge_smoke_completing", 90))
        self.assertEqual(store.completed[0]["smoke"], True)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["job_id"], job.job_id)


if __name__ == "__main__":
    unittest.main()
