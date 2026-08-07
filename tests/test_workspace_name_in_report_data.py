"""report_data.parameters 帶 workspace 顯示名稱（P3-2，2026-07-31）。

## 為什麼

P1-8 定案封面主標＝workspace 名稱（`cover.title` AI slot 退場），但
`report_data.json` 的 parameters 原本沒有這個資訊——build_ppt 拿不到就只能
退回通用標題。資料源頭在容器端 `report_generate`：handler 依 payload 的
workspace_id 查 `app_layer.workspaces.workspace_name` 傳給引擎。

⚠ 查不到（workspace 不存在／DB 失敗）→ 傳 None、封面走通用標題 fallback——
標題缺名字是小事，報表產不出來是大事，不得因此讓 job 失敗。
"""
from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.reports import chart_runner


class RunChartTrialWorkspaceNameTests(unittest.TestCase):
    """🔴 run_chart_trial 收 workspace_name 並落進 parameters。"""

    _ROWS = {"application_trend": [{"application_year": 2024, "patent_count": 2}]}

    def _stub_run_report(self, name, **kwargs):
        d = chart_runner.REPORT_DEFINITIONS[name]
        rows = self._ROWS.get(name, [{"x": 1}])
        return {"name": name, "label": d.label, "label_zh": d.label_zh,
                "report_type": d.report_type, "rows": rows, "row_count": len(rows)}

    def test_parameter_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            # ⚠ fetch_patent_kind_summary 是 A4 的 DB 接縫（刻意不軟退化）——
            # 本測試寫於 A4 之前漏補 stub，DB 不可達時整個 trial 炸（2026-08-07 補）。
            with mock.patch.object(chart_runner, "run_report", self._stub_run_report), \
                 mock.patch.object(chart_runner, "fetch_patent_kind_summary", dict):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp), report_names=["application_trend"],
                    workspace_name="拉繩訓練機")
            rd = json.loads((Path(result["output_dir"]) / "report_data.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(rd["parameters"]["workspace_name"], "拉繩訓練機")

    def test_absent_when_not_given(self):
        """⚠ 不給＝不落鍵（封面端以「鍵不存在」走 fallback，不要落 null 混淆）。"""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", self._stub_run_report), \
                 mock.patch.object(chart_runner, "fetch_patent_kind_summary", dict):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp), report_names=["application_trend"])
            rd = json.loads((Path(result["output_dir"]) / "report_data.json")
                            .read_text(encoding="utf-8"))
        self.assertNotIn("workspace_name", rd["parameters"])


class HandlerWiringTests(unittest.TestCase):
    """🔴 report_generate handler 依 workspace_id 查名並轉傳；查掛不得炸 job。"""

    def test_handler_resolves_and_passes(self):
        from backend.app.worker import handlers

        src = inspect.getsource(handlers.handle_report_generate)
        self.assertIn("workspace_name", src, "handler 未把 workspace_name 傳給引擎")
        self.assertIn("_resolve_workspace_name", src, "handler 未經 resolver 查名")
        resolver_src = inspect.getsource(handlers._resolve_workspace_name)
        self.assertIn("except", resolver_src, "查名失敗必須吞掉走 fallback，不得炸 job")


if __name__ == "__main__":
    unittest.main()
