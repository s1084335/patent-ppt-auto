"""分群報表接通：報表 job 必須拿得到 workspace_id（2026-07-28 使用者實機發現）。

症狀：分群完成後產報表，勾了「主題分類統計表」「機會四象限」「痛點四象限」，
三份都不會出現（實測 17 份定義只產出 13 張卡）。

根因——`workspace_id` 全程沒傳到，四層每層都靜默：

    前端 submitReports()          只送 report_names，沒送 workspace_id
      → API ReportRequest         沒有 workspace_id 欄位（收了也接不住）
      → create_job(...)           沒帶 workspace_id= 參數
      → _resolve_report_cluster_data  workspace_id is None → return None
      → run_chart_trial           cluster_data=None → 靜默跳過整張分群卡

沒有任何一層會報錯。**就算有分群資料也一樣產不出來**——與「有沒有跑過分群」無關。

⚠ 出圖端本來就做好了（`_build_cluster_analytics_section` 支援雙通道分段、
市場資料用 `data.get("pain_data", [])` 已是「有就用、沒有也能跑」），
缺的純粹是這條參數線。

市場資料契約（使用者定案 2026-07-28）：**有市場就用、沒市場也要能產出報表**。
痛點板在無市場資料時，嚴重度落 unknown（前端顯示灰色待調查帶，不當 low），
報表照常產出、AI 解讀照常有。
"""
from __future__ import annotations

import unittest
from unittest import mock


class ReportRequestCarriesWorkspaceTests(unittest.TestCase):
    """API 層要收得住 workspace_id。"""

    def test_request_model_has_workspace_id(self):
        from backend.app.api.reports import ReportRequest

        self.assertIn(
            "workspace_id", ReportRequest.model_fields,
            "ReportRequest 沒有 workspace_id——前端送了也接不住，"
            "分群報表永遠拿不到範圍")

    def test_job_created_with_workspace_id(self):
        """create_job 必須帶 workspace_id=，否則 job 上沒有範圍可供 worker 取用。"""
        import inspect
        from backend.app.api import reports

        src = inspect.getsource(reports.create_report)
        self.assertIn(
            "workspace_id=", src,
            "create_job 沒帶 workspace_id=，worker 端 context.job.workspace_id 會是 None")


class FrontendSendsWorkspaceTests(unittest.TestCase):
    """前端產報表時要帶上目前 workspace。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.app.main import app

        cls.html = TestClient(app).get("/").text

    def test_submit_reports_sends_workspace_id(self):
        import re

        body = re.search(r"async function submitReports\s*\([^)]*\)\s*\{(.*?)\n\}",
                         self.html, re.S)
        self.assertIsNotNone(body, "找不到 submitReports 函式本體")
        self.assertIn(
            "workspace_id", body.group(1),
            "submitReports 沒送 workspace_id——分群三份報表不會產出")


# 🔴 2026-08-04：MarketOptionalTests 已刪除——痛點板與其市場資料通道已整個移除（使用者定案）


class ClusterDataResolutionTests(unittest.TestCase):
    """有 workspace_id 時要真的去載分群資料。"""

    def test_resolve_returns_none_without_workspace(self):
        """全庫報表（無 workspace）沒有分群範圍可談，回 None 是正確行為。"""
        from backend.app.worker import handlers

        ctx = mock.Mock()
        ctx.job.workspace_id = None
        self.assertIsNone(handlers._resolve_report_cluster_data({}, ctx))

    def test_resolve_uses_job_workspace_id(self):
        """payload 沒帶時要退回 job 的 workspace_id（前端經 create_job 設在 job 上）。"""
        from backend.app.worker import handlers

        ctx = mock.Mock()
        ctx.job.workspace_id = 7
        with mock.patch.object(
                handlers, "_load_report_cluster_data",
                return_value={"topics": [1], "assignments": [], "topic_rows": []}) as loader:
            handlers._resolve_report_cluster_data({}, ctx)
        self.assertTrue(loader.called)
        self.assertEqual(loader.call_args[0][0], 7)

    def test_resolve_loads_both_channels_by_default(self):
        """未指定通道時技術與功效都要載（使用者定案：兩個通道都做報表）。"""
        from backend.app.worker import handlers
        from backend.app.clustering.sources import source_fields

        ctx = mock.Mock()
        ctx.job.workspace_id = 7
        with mock.patch.object(
                handlers, "_load_report_cluster_data",
                return_value={"topics": [1], "assignments": [], "topic_rows": []}) as loader:
            handlers._resolve_report_cluster_data({}, ctx)
        called = [c[0][1] for c in loader.call_args_list]
        self.assertEqual(sorted(called), sorted(source_fields()),
                         "只載了一個通道——功效分群的報表會缺席")

    def test_resolve_honours_explicit_channel(self):
        """payload 明確指定通道時只載該通道。"""
        from backend.app.worker import handlers
        from backend.app.clustering.sources import SOURCE_FIELD_TECHNICAL

        ctx = mock.Mock()
        ctx.job.workspace_id = 7
        with mock.patch.object(
                handlers, "_load_report_cluster_data",
                return_value={"topics": [1], "assignments": [], "topic_rows": []}) as loader:
            handlers._resolve_report_cluster_data(
                {"source_field": SOURCE_FIELD_TECHNICAL}, ctx)
        self.assertEqual(len(loader.call_args_list), 1)

    def test_merge_keeps_partial_channels(self):
        """只有一個通道有主題時，照樣回傳那部分——不因另一通道沒分群就整張不給。"""
        from backend.app.worker import handlers

        parts = iter([
            {"topics": [{"topic_code": "T001"}], "assignments": [1], "topic_rows": [{"a": 1}]},
            None,
        ])
        with mock.patch.object(handlers, "_load_report_cluster_data",
                               side_effect=lambda *a, **k: next(parts)):
            merged = handlers._merge_cluster_channels(7, ["tech", "effect"])
        self.assertIsNotNone(merged)
        self.assertEqual(len(merged["topics"]), 1)


if __name__ == "__main__":
    unittest.main()
