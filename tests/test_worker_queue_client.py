from __future__ import annotations

from contextlib import nullcontext
from decimal import Decimal
import time
import unittest
from unittest import mock

from backend.app.worker import handlers
from backend.app.worker import runner
from backend.app.clustering.sources import SOURCE_FIELD_TECHNICAL
from backend.app.reports.report_definitions import DEFAULT_REPORT_NAMES
from backend.app.worker.job_context import JobCancelledError, JobContext
from backend.app.worker.queue_client import ProcessingJob, TERMINAL_STATUSES
from backend.app.worker.runner import build_parser


class ProcessingJobTests(unittest.TestCase):
    """驗證 worker job 資料物件與固定狀態集合。"""

    def test_from_row_parses_payload_and_workspace(self):
        """確認 DB row（0021 workflow_runs 形狀）可轉成 worker 使用的 ProcessingJob。"""
        row = {
            "run_id": 1,
            "run_type": "report_generate",
            "status": "queued",
            "workspace_id": None,
            "request_json": {"report_names": ["x"]},
            "worker_state_json": {
                "attempt_count": 0,
                "max_attempts": 3,
                "progress_percent": 0,
                "current_stage": "queued",
            },
        }
        job = ProcessingJob.from_row(row)
        self.assertEqual(job.job_id, 1)
        self.assertEqual(job.job_type, "report_generate")
        self.assertEqual(job.workspace_id, None)
        self.assertEqual(job.payload_json["report_names"], ["x"])
        self.assertEqual(job.attempt_count, 0)
        self.assertEqual(job.max_attempts, 3)
        # 0021：結果落 workflow_outputs，佇列列上的 result_json 一律 None
        self.assertIsNone(job.result_json)

    def test_terminal_statuses_do_not_include_running(self):
        """確認 terminal 狀態不包含 running。"""
        self.assertEqual(TERMINAL_STATUSES, {"succeeded", "failed", "cancelled"})


class HandlerContractTests(unittest.TestCase):
    """驗證 worker 支援的 job_type 與 JSON 安全轉換。"""

    def test_required_handler_keys(self):
        """確認 worker 只支援定案的 job type（2026-07-21 補 patent_import——匯入線 handler
        已上線但本契約集合漏更新，OpenCode 批 G 前置檢查抓到；同日 0021 遷移輪加
        topic_merge/topic_unmerge——佇列由 PostgresTopicRepository 排入、worker 執行；
        2026-07-22 補 embeddings——匯入後建立向量；補 ai:narrative——報表解讀 AI
        任務消費者，補齊前端→佇列→headless CLI→MCP／檔案回存→SSE 的 E2E 鏈）。"""
        self.assertEqual(
            set(handlers.HANDLERS),
            {
                "clustering_calibrate",
                "clustering_finalize",
                "clustering_incremental",
                "report_generate",
                "patent_import",
                "embeddings",
                "topic_merge",
                "topic_unmerge",
                "ai:narrative",
            },
        )

    def test_json_safe_converts_decimal(self):
        """確認 handler 結果會轉成 JSONB 可保存的基本型別。"""
        result = handlers._json_safe({"value": Decimal("1.25")})
        self.assertEqual(result, {"value": 1.25})

    def test_calibrate_default_source_field_is_legal(self):
        """確認 calibrate 預設 source_field 使用合法分群欄位值。"""
        context = mock.Mock()
        context.heartbeat.return_value = None
        context.keepalive.return_value = nullcontext()
        fake_summary = mock.Mock()
        fake_summary.__dataclass_fields__ = {}
        with mock.patch.object(handlers, "calibrate_top_level", return_value={"ok": True}) as patched:
            result = handlers.handle_clustering_calibrate({"workspace_id": 2}, context)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(patched.call_args.kwargs["source_field"], SOURCE_FIELD_TECHNICAL)
        context.keepalive.assert_called_once()

    def test_calibrate_rejects_illegal_source_field(self):
        """確認 calibrate 會拒絕非法 source_field。"""
        context = mock.Mock()
        context.heartbeat.return_value = None
        context.keepalive.return_value = nullcontext()
        with self.assertRaises(ValueError):
            handlers.handle_clustering_calibrate(
                {"workspace_id": 2, "source_field": "technical"},
                context,
            )

    def test_incremental_rejects_illegal_source_field(self):
        """確認 incremental 會拒絕非法 source_field。"""
        context = mock.Mock()
        context.heartbeat.return_value = None
        context.keepalive.return_value = nullcontext()
        with self.assertRaises(ValueError):
            handlers.handle_clustering_incremental(
                {"workspace_id": 2, "source_field": "technical"},
                context,
            )

    @staticmethod
    def _report_generate_names(payload):
        """跑 handle_report_generate 並回傳它實際交給出圖引擎的 report_names。

        2026-07-23 起 handler 改呼叫 run_chart_trial（真的出圖＋落 DB），不再只跑
        run_reports_batch；本組測試只驗 report_names 預設／子集合語意，故把出圖與上傳
        都替身掉，不碰檔案系統與 DB。
        """
        context = mock.Mock()
        context.heartbeat.return_value = None
        context.keepalive.return_value = nullcontext()
        context.job.workspace_id = None
        captured = {}

        def _fake_run_chart_trial(**kwargs):
            captured.update(kwargs)
            return {"status": "ok", "output_dir": "x", "version": "v", "files": []}

        with mock.patch.object(handlers, "run_chart_trial", _fake_run_chart_trial), \
                mock.patch.object(handlers.report_artifact_store, "upload_run_dir",
                                  return_value=0):
            handlers.handle_report_generate(payload, context)
        return captured["report_names"]

    def test_report_generate_missing_names_uses_default_reports(self):
        """確認 worker 缺少 report_names 時使用固定預設報表名單。"""
        self.assertEqual(self._report_generate_names({}), list(DEFAULT_REPORT_NAMES))

    def test_report_generate_null_names_uses_default_reports(self):
        """確認 worker 收到 null report_names 時使用固定預設報表名單。"""
        self.assertEqual(
            self._report_generate_names({"report_names": None}), list(DEFAULT_REPORT_NAMES)
        )

    def test_report_generate_empty_names_uses_default_reports(self):
        """確認 worker 收到空 report_names 時使用固定預設報表名單。"""
        self.assertEqual(
            self._report_generate_names({"report_names": []}), list(DEFAULT_REPORT_NAMES)
        )

    def test_report_generate_explicit_subset_is_preserved(self):
        """確認 worker 明確報表子集合不會被預設名單覆蓋。"""
        self.assertEqual(
            self._report_generate_names({"report_names": ["application_trend"]}),
            ["application_trend"],
        )

    def test_report_generate_non_list_names_still_rejected(self):
        """確認 worker 仍拒絕非 list 型別的 report_names。"""
        context = mock.Mock()
        context.heartbeat.return_value = None
        with self.assertRaises(ValueError):
            handlers.handle_report_generate({"report_names": "application_trend"}, context)

    def test_topic_merge_passes_topic_codes_to_engine(self):
        """確認 merge handler 把佇列的 topic_code 原樣交給引擎（引擎參數即 topic_keys）。

        0021 後主題以 topic_code 為唯一識別，不再做 code→int topic_id 轉換；
        _resolve_active_topic_ids 只留作連 DB 的前置診斷，與本測試無關故 mock 掉。
        參數名一致性由 tests/test_topic_merge_call_contract.py 以 autospec 鎖住。
        """
        context = mock.Mock()
        context.heartbeat.return_value = None
        context.keepalive.return_value = nullcontext()
        context.job.workspace_id = 7
        with mock.patch.object(
            handlers, "_resolve_active_topic_ids", autospec=True, return_value=[11, 12]
        ), mock.patch.object(
            handlers, "merge_workspace_topics", autospec=True, return_value={"ok": True}
        ) as engine:
            result = handlers.handle_topic_merge(
                {
                    "source_field": SOURCE_FIELD_TECHNICAL,
                    "topic_keys": ["T01", "T02"],
                    "label": "合併後主題",
                    "requested_by": "web-user",
                },
                context,
            )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(engine.call_args.kwargs["topic_keys"], ["T01", "T02"])
        self.assertEqual(engine.call_args.kwargs["merged_by"], "web-user")
        self.assertEqual(engine.call_args.kwargs["label"], "合併後主題")

    def test_topic_merge_requires_workspace_id_on_run(self):
        """確認 merge handler 拒絕沒有 workspace_id 的 run（workspace_id 由 run 帶）。"""
        context = mock.Mock()
        context.heartbeat.return_value = None
        context.job.workspace_id = None
        with self.assertRaises(ValueError):
            handlers.handle_topic_merge(
                {"source_field": SOURCE_FIELD_TECHNICAL, "topic_keys": ["T01", "T02"]},
                context,
            )

    def test_topic_unmerge_calls_engine_with_merge_run_id(self):
        """確認 unmerge handler 直接以 int merge_run_id 呼叫引擎（無 code 解析需求）。"""
        context = mock.Mock()
        context.heartbeat.return_value = None
        context.keepalive.return_value = nullcontext()
        context.job.workspace_id = 7
        with mock.patch.object(
            handlers, "unmerge_workspace_topics", return_value={"ok": True}
        ) as engine:
            result = handlers.handle_topic_unmerge(
                {
                    "source_field": SOURCE_FIELD_TECHNICAL,
                    "merge_run_id": 55,
                    "requested_by": "web-user",
                },
                context,
            )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(engine.call_args.kwargs["merge_run_id"], 55)
        self.assertEqual(engine.call_args.kwargs["reverted_by"], "web-user")
        self.assertEqual(engine.call_args.kwargs["workspace_id"], 7)


class RunnerCliTests(unittest.TestCase):
    """驗證 worker CLI 保留 run-once 與 serve 兩種執行模式。"""

    def test_parser_accepts_run_once(self):
        """確認 CLI 保留單次執行模式。"""
        args = build_parser().parse_args(["run-once", "--worker-id", "test-worker"])
        self.assertEqual(args.command, "run-once")
        self.assertEqual(args.worker_id, "test-worker")

    def test_default_worker_job_types_exclude_ai_jobs(self):
        """一般 worker 不 claim AI CLI 任務；AI 任務交給 host-side bridge。"""
        self.assertNotIn("ai:narrative", runner.DEFAULT_WORKER_JOB_TYPES)
        self.assertIn("report_generate", runner.DEFAULT_WORKER_JOB_TYPES)


class FakeQueueClient:
    """記錄 runner 對 queue client 的呼叫，避免單元測試碰資料庫。"""

    def __init__(self):
        """初始化呼叫紀錄。"""
        self.heartbeats: list[tuple[str | None, int | None]] = []
        self.completed: list[dict[str, object]] = []
        self.failed: list[dict[str, object]] = []
        self.cancelled: list[dict[str, object]] = []

    def heartbeat(
        self,
        *,
        job_id: int,
        worker_id: str,
        current_stage: str | None = None,
        progress_percent: int | None = None,
    ) -> None:
        """記錄 heartbeat 呼叫參數。"""
        self.heartbeats.append((current_stage, progress_percent))

    def complete_job(self, *, job_id: int, worker_id: str, result_json: dict[str, object]) -> None:
        """記錄成功回寫呼叫。"""
        self.completed.append(result_json)

    def fail_job(
        self,
        *,
        job_id: int,
        worker_id: str,
        error_message: str,
        current_stage: str = "failed",
    ) -> None:
        """記錄失敗回寫呼叫。"""
        self.failed.append({"error_message": error_message, "current_stage": current_stage})

    def cancel_job(self, *, job_id: int, worker_id: str, error_message: str) -> None:
        """記錄取消回寫呼叫。"""
        self.cancelled.append({"error_message": error_message})

    def is_cancelled(self, *, job_id: int) -> bool:
        """測試預設不從 context 觸發取消。"""
        return False


class JobContextKeepaliveTests(unittest.TestCase):
    """測試長任務背景 heartbeat 會持續補訊號。"""

    def test_keepalive_emits_periodic_heartbeats(self):
        """確認 keepalive 進入時先打一筆，等待間隔後會再補 heartbeat。"""
        store = FakeQueueClient()
        job = ProcessingJob(
            job_id=9,
            job_type="clustering_calibrate",
            status="running",
            workspace_id=1,
            payload_json={},
            result_json=None,
            progress_percent=0,
            current_stage="starting",
            attempt_count=1,
            max_attempts=3,
        )
        context = JobContext(job=job, worker_id="worker-keepalive", store=store)
        with context.keepalive("long_running", 20, interval_seconds=0.01):
            time.sleep(0.035)
        self.assertGreaterEqual(len(store.heartbeats), 2)
        self.assertTrue(all(item == ("long_running", 20) for item in store.heartbeats))


class RunnerExecutionTests(unittest.TestCase):
    """驗證 runner 不靠資料庫也能正確收斂成功、失敗與取消狀態。"""

    def _job(self) -> ProcessingJob:
        """建立測試用 ProcessingJob。"""
        return ProcessingJob(
            job_id=7,
            job_type="report_generate",
            status="running",
            workspace_id=None,
            payload_json={"report_names": ["x"]},
            result_json=None,
            progress_percent=0,
            current_stage="starting",
            attempt_count=1,
            max_attempts=3,
        )

    def test_execute_job_completes_success(self):
        """成功時 runner 應呼叫 complete_job，不應寫 failed/cancelled。"""
        store = FakeQueueClient()
        with mock.patch.object(runner, "dispatch_job", return_value={"ok": True}):
            result = runner.execute_job(self._job(), worker_id="worker-1", store=store)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(store.completed, [{"ok": True}])
        self.assertEqual(store.failed, [])
        self.assertEqual(store.cancelled, [])

    def test_execute_job_marks_failure(self):
        """一般例外時 runner 應呼叫 fail_job 並回傳 failed。"""
        store = FakeQueueClient()
        with mock.patch.object(runner, "dispatch_job", side_effect=ValueError("bad payload")):
            with mock.patch.object(runner.LOGGER, "exception"):
                result = runner.execute_job(self._job(), worker_id="worker-1", store=store)
        self.assertEqual(result["status"], "failed")
        self.assertIn("ValueError", store.failed[0]["error_message"])
        self.assertEqual(store.completed, [])

    def test_execute_job_marks_cancelled(self):
        """取消時 runner 應呼叫 cancel_job，不應當成 failed。"""
        store = FakeQueueClient()
        with mock.patch.object(runner, "dispatch_job", side_effect=JobCancelledError("stop")):
            with mock.patch.object(runner.LOGGER, "warning"):
                result = runner.execute_job(self._job(), worker_id="worker-1", store=store)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(store.cancelled, [{"error_message": "stop"}])
        self.assertEqual(store.failed, [])


if __name__ == "__main__":
    unittest.main()
