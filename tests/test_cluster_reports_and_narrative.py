"""分群三報表註冊 ＋ 報表產完自動接 AI 解讀（2026-07-24）。

覆蓋兩件事，皆不碰正式庫、不真跑 CLI／不產真 AI job：
  A. cluster_topic_table／opportunity_quadrant／pain_point_quadrant 三支報表已註冊進
     REPORT_DEFINITIONS，且無分群資料時優雅回空、不爆；pain_point 註明待市場線痛點資料。
  B. handle_report_generate 完成後自動 enqueue ai:narrative（帶 based_on_version＝該次報表版本），
     enqueue 失敗不影響報表本身，且複用既有 ai_narrative_runner／handle_ai_narrative（不另寫解讀）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.reports import chart_runner
from backend.app.reports.report_definitions import REPORT_DEFINITIONS
from backend.app.reports.report_engine import run_reports_batch


CLUSTER_REPORT_NAMES = ("cluster_topic_table", "opportunity_quadrant", "pain_point_quadrant")


class ClusterReportDefinitionTests(unittest.TestCase):
    """三支分群報表註冊進 REPORT_DEFINITIONS 並掛進 chart section registry。"""

    def test_three_cluster_reports_registered(self):
        """三支報表都要在 REPORT_DEFINITIONS 中（前端報表種類才列得出來）。"""
        for name in CLUSTER_REPORT_NAMES:
            self.assertIn(name, REPORT_DEFINITIONS, f"{name} 未註冊進 REPORT_DEFINITIONS")
            self.assertEqual(REPORT_DEFINITIONS[name].report_type, "cluster",
                             f"{name} report_type 應為 cluster（不走 SQL 引擎）")

    def test_cluster_reports_covered_by_section_registry(self):
        """三支報表都要掛進某個 SectionSpec，否則 resolve_sections 會 fail loud。"""
        covered = {name for spec in chart_runner.SECTION_SPECS for name in spec.reports}
        for name in CLUSTER_REPORT_NAMES:
            self.assertIn(name, covered, f"{name} 未掛進 section registry")
        specs = chart_runner.resolve_sections(["cluster_topic_table"])
        self.assertEqual([s.key for s in specs], ["cluster_analytics"],
                         "cluster_topic_table 應解析到 cluster_analytics section")

    def test_pain_point_marks_pending_market_data(self):
        """pain_point_quadrant 須註明依賴市場線痛點資料（尚未實作）。"""
        note = getattr(REPORT_DEFINITIONS["pain_point_quadrant"], "data_source_note", "")
        self.assertIn("市場", note, "pain_point_quadrant 應註明待市場線痛點資料")


class ClusterReportGracefulEmptyTests(unittest.TestCase):
    """走 SQL 引擎的 run_reports_batch 對 cluster 型報表優雅回空、不爆。"""

    def test_run_reports_batch_skips_cluster_reports_without_crash(self):
        """cluster 型報表不吃單表 SQL，run_reports_batch 應以 skipped_reason 回報而非丟例外。"""
        # 不連 DB：cluster 型報表在建 SQL 前就該被引擎攔下，故此呼叫不應觸及 psycopg。
        results = run_reports_batch(list(CLUSTER_REPORT_NAMES))
        for name in CLUSTER_REPORT_NAMES:
            self.assertIn(name, results)
            self.assertIn("skipped_reason", results[name],
                          f"{name} 應被引擎跳過（改由分群 section 出圖）")

    def test_cluster_section_graceful_empty_when_no_cluster_data(self):
        """尚未分群（cluster_data=None）時，分群 section 靜默跳過、不產表、不爆。"""
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            ctx = SimpleNamespace(
                run_dir=Path(tmp), chart_rows={}, sections=[], report=None,
                cluster_data=None, ipc_levels=(4, 5), cpc_levels=(4, 5))
            chart_runner._build_cluster_analytics_section(ctx)
        self.assertEqual(ctx.sections, [], "無分群資料時不得產出分群 section")


class ClusterReportArtifactMappingTests(unittest.TestCase):
    """artifact → report_name 對映：三個檔各自對回自己的報表名（供 manifest／解讀查找）。"""

    def test_each_artifact_maps_to_own_report_name(self):
        self.assertEqual(
            chart_runner.report_names_for_artifact("cluster_topic_table.html"),
            ["cluster_topic_table"])
        self.assertEqual(
            chart_runner.report_names_for_artifact("opportunity_quadrant.svg"),
            ["opportunity_quadrant"])
        self.assertEqual(
            chart_runner.report_names_for_artifact("pain_point_quadrant.svg"),
            ["pain_point_quadrant"])


class ReportGenerateAutoNarrativeTests(unittest.TestCase):
    """report_generate 完成後**不再**自動 enqueue ai:narrative（2026-07-29 移除）。

    ## 為何反轉 2026-07-24 的定案

    1. **架構定案是「按需」**：workflows.md（AI 產出落點定案）明載「報表解讀等採
       **按需**觸發，確定性報表先顯示、不等 AI、AI 掛掉也看得到」。07-24 的
       自動接續與此矛盾——當時解讀線剛建，前端還沒有手動鈕。
    2. **重複排程實錘**（待辦 B4）：自動 enqueue ＋ 前端手動鈕各建一筆，
       同版本兩個 narrative job 搶寫同一份 narratives.json、Companion 燒兩倍 token。
    3. **先例一致**：patent_note、company_zh_name（皆 07-27）都因「自動觸發失敗
       無補救入口」改手動鈕；解讀的手動鈕 07-28 已上線。
    """

    def _context(self, payload: dict):
        """最小 JobContext 替身：只記 heartbeat，不碰 DB。"""
        from backend.app.worker.job_context import JobContext
        from backend.app.worker.queue_client import ProcessingJob

        class _Store:
            def __init__(self):
                self.beats = []

            def heartbeat(self, *, job_id, worker_id, current_stage=None, progress_percent=None):
                self.beats.append((current_stage, progress_percent))

            def is_cancelled(self, *, job_id):
                return False

        job = ProcessingJob(
            job_id=7, job_type="report_generate", status="running", workspace_id=None,
            payload_json=payload, result_json=None, progress_percent=0,
            current_stage="queued", attempt_count=1, max_attempts=3)
        return JobContext(job=job, worker_id="worker-report", store=_Store())

    def _patched_report(self, version="report_trial_20260724_120000"):
        """把出圖與上傳都 mock 掉，回傳含 version 的假結果。"""
        from backend.app.worker import handlers

        chart = mock.patch.object(
            handlers, "run_chart_trial",
            return_value={"status": "ok", "output_dir": f"/tmp/{version}", "version": version})
        cluster = mock.patch.object(handlers, "_resolve_report_cluster_data", return_value=None)
        upload = mock.patch("backend.app.db.report_artifact_store.upload_run_dir", return_value=0)
        return chart, cluster, upload

    def test_report_generate_does_not_auto_enqueue_narrative(self):
        """報表完成後**不得**自動建立 ai:narrative——解讀一律由使用者按鈕觸發。"""
        from backend.app.worker import handlers

        chart, cluster, upload = self._patched_report()
        with chart, cluster, upload, \
                mock.patch("backend.app.db.job_repository.create_job") as mock_create:
            ctx = self._context({})
            result = handlers.handle_report_generate({}, ctx)

        narrative_calls = [c for c in mock_create.call_args_list
                           if c.args and c.args[0] == "ai:narrative"]
        self.assertEqual(narrative_calls, [],
                         "報表完成不得自動排解讀——與前端手動鈕各建一筆＝同版本雙 job")
        self.assertNotIn("narrative_job_enqueued", result,
                         "自動排程移除後不得再回報此欄位（誤導前端以為有自動解讀）")

    def test_report_does_not_wait_for_narrative(self):
        """報表回傳不含解讀結果——解讀非同步補上，報表先出（不等 AI）。"""
        from backend.app.worker import handlers

        chart, cluster, upload = self._patched_report()
        with chart, cluster, upload, \
                mock.patch("backend.app.db.job_repository.create_job") as mock_create:
            mock_create.return_value = mock.Mock(job_id=99)
            ctx = self._context({})
            result = handlers.handle_report_generate({}, ctx)

        # 報表結果只帶自己的產物，不含 narratives／解讀文字（那是後續 job 的事）。
        self.assertNotIn("narratives", result)
        self.assertNotIn("narrative_text", result)

    def test_reuses_existing_narrative_runner(self):
        """自動觸發的 ai:narrative 由既有 handle_ai_narrative 消費，複用既有 ai_narrative_runner。"""
        from backend.app.worker import handlers

        # 證據一：ai:narrative 由既有 handler 消費（不另寫解讀 handler）。
        self.assertIs(handlers.HANDLERS["ai:narrative"], handlers.handle_ai_narrative)
        # 證據二：handle_ai_narrative 委派既有 ai_narrative_runner.run_narrative。
        import inspect
        src = inspect.getsource(handlers.handle_ai_narrative)
        self.assertIn("ai_narrative_runner.run_narrative", src,
                      "解讀邏輯應複用既有 runner，不在 handler 內重寫")


if __name__ == "__main__":
    unittest.main()
