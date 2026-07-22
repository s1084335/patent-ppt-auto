"""worker handlers 的 ai:narrative 消費者測試（fake CLI runner，不真跑 CLI）。

專測 E2E 鏈上補齊的一環：前端按 AI 解讀 → 佇列 → handle_ai_narrative → headless CLI
→ narratives.json → refresh-index → SSE 回推。既有 handler 測試留在 test_worker_queue_client.py。
"""

from __future__ import annotations

import unittest

from backend.app.worker import ai_narrative_runner, handlers
from backend.app.worker.job_context import JobContext
from backend.app.worker.queue_client import ProcessingJob


class FakeStore:
    """記錄 heartbeat 呼叫，供斷言階段序列；不碰資料庫。"""

    def __init__(self):
        """初始化 heartbeat 紀錄。"""
        self.heartbeats: list[tuple[str | None, int | None]] = []

    def heartbeat(self, *, job_id, worker_id, current_stage=None, progress_percent=None):
        """記錄 heartbeat 的 (階段, 百分比)。"""
        self.heartbeats.append((current_stage, progress_percent))

    def is_cancelled(self, *, job_id):
        """測試不觸發取消。"""
        return False


def _context(store: FakeStore, payload: dict) -> JobContext:
    """建立帶 ai:narrative payload 的 JobContext（走真實 JobContext，不 mock）。"""
    job = ProcessingJob(
        job_id=42,
        job_type="ai:narrative",
        status="running",
        workspace_id=None,
        payload_json=payload,
        result_json=None,
        progress_percent=0,
        current_stage="queued",
        attempt_count=1,
        max_attempts=3,
    )
    return JobContext(job=job, worker_id="worker-narrative", store=store)


class AiNarrativeHandlerTests(unittest.TestCase):
    """handle_ai_narrative 用 fake runner 跑通並驗階段序列與回傳結構。"""

    def _fake_run_narrative(self, captured):
        """回傳一個 fake run_narrative：把呼叫參數記進 captured dict，呼叫 progress 模擬 CLI 緩進。"""

        def _run(based_on_version, *, cli_kind, cli_runner, timeout_seconds, progress, model=None):
            captured["based_on_version"] = based_on_version
            captured["cli_kind"] = cli_kind
            captured["model"] = model
            captured["timeout_seconds"] = timeout_seconds
            # 模擬 runner 內 CLI 執行期間的緩進 heartbeat。
            progress("cli_running", 30)
            progress("cli_running", 85)
            return {
                "based_on_version": based_on_version or "report_trial_latest",
                "narrated": 13,
                "variants_total": 14,
                "pending": ["cluster_analytics:pain"],
                "cli_kind": cli_kind,
                "prompt_version": ai_narrative_runner.PROMPT_VERSION,
                "narratives_path": "/x/narratives.json",
            }

        return _run

    def test_handler_heartbeat_stage_sequence_and_result(self):
        """階段映射 15 → 30 → 85 → 90 → 100，回傳結構含版本與覆蓋變體數。"""
        store = FakeStore()
        payload = {"based_on_version": "report_trial_20260722_001036"}
        context = _context(store, payload)
        captured: dict = {}

        original = ai_narrative_runner.run_narrative
        ai_narrative_runner.run_narrative = self._fake_run_narrative(captured)
        try:
            result = handlers.handle_ai_narrative(payload, context)
        finally:
            ai_narrative_runner.run_narrative = original

        percents = [p for _, p in store.heartbeats]
        # 起始 15 → CLI 緩進 30 → 85 → 回存 90 → 完成 100，且單調不遞減。
        self.assertEqual(percents[0], 15)
        self.assertIn(30, percents)
        self.assertIn(85, percents)
        self.assertEqual(percents[-2], 90)
        self.assertEqual(percents[-1], 100)
        self.assertEqual(percents, sorted(percents))
        # 回傳結構
        self.assertEqual(result["based_on_version"], "report_trial_20260722_001036")
        self.assertEqual(result["variants_narrated"], 13)
        self.assertEqual(result["variants_total"], 14)
        self.assertEqual(result["pending"], ["cluster_analytics:pain"])
        self.assertEqual(result["prompt_version"], ai_narrative_runner.PROMPT_VERSION)

    def test_handler_default_cli_kind_is_claude(self):
        """未指定 cli_kind 時預設 claude；based_on_version 缺省傳 None（runner 取最新）。"""
        store = FakeStore()
        payload: dict = {}
        context = _context(store, payload)
        captured: dict = {}

        original = ai_narrative_runner.run_narrative
        ai_narrative_runner.run_narrative = self._fake_run_narrative(captured)
        try:
            handlers.handle_ai_narrative(payload, context)
        finally:
            ai_narrative_runner.run_narrative = original

        self.assertEqual(captured["cli_kind"], "claude")
        self.assertIsNone(captured["based_on_version"])

    def test_handler_forwards_cli_kind_override(self):
        """payload 指定 cli_kind=opencode 時 handler 轉給 runner（雙 CLI 可換）。"""
        store = FakeStore()
        payload = {"cli_kind": "opencode"}
        context = _context(store, payload)
        captured: dict = {}

        original = ai_narrative_runner.run_narrative
        ai_narrative_runner.run_narrative = self._fake_run_narrative(captured)
        try:
            handlers.handle_ai_narrative(payload, context)
        finally:
            ai_narrative_runner.run_narrative = original

        self.assertEqual(captured["cli_kind"], "opencode")

    def test_handler_forwards_model(self):
        """payload 指定 model 時 handler 轉給 runner（選具體模型，未給則 None 用 CLI 預設）。"""
        store = FakeStore()
        payload = {"model": "claude-opus-4-8"}
        context = _context(store, payload)
        captured: dict = {}

        original = ai_narrative_runner.run_narrative
        ai_narrative_runner.run_narrative = self._fake_run_narrative(captured)
        try:
            handlers.handle_ai_narrative(payload, context)
        finally:
            ai_narrative_runner.run_narrative = original

        self.assertEqual(captured["model"], "claude-opus-4-8")


class HandlerRegistrationTests(unittest.TestCase):
    """HANDLERS 註冊與 dispatch 不再視 ai:narrative 為 unsupported。"""

    def test_ai_narrative_registered(self):
        """ai:narrative 已註冊進 HANDLERS。"""
        self.assertIn("ai:narrative", handlers.HANDLERS)
        self.assertIs(handlers.HANDLERS["ai:narrative"], handlers.handle_ai_narrative)

    def test_dispatch_ai_narrative_not_unsupported(self):
        """dispatch_job 對 ai:narrative 不再 raise unsupported job_type。"""
        store = FakeStore()
        payload = {"based_on_version": "report_trial_x"}
        context = _context(store, payload)

        called: dict = {}

        def _fake_handler(pl, ctx):
            called["hit"] = True
            return {"ok": True}

        original = handlers.HANDLERS["ai:narrative"]
        handlers.HANDLERS["ai:narrative"] = _fake_handler
        try:
            result = handlers.dispatch_job(payload, context)
        finally:
            handlers.HANDLERS["ai:narrative"] = original

        self.assertTrue(called.get("hit"))
        self.assertEqual(result, {"ok": True})


if __name__ == "__main__":
    unittest.main()
