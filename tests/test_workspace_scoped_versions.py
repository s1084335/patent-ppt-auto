"""報表版本與 PPT 依 workspace 區隔（2026-07-31 使用者定案）。

## 問題

使用者選全庫重產報表，匯出報告頁卻顯示上一版（滑雪機語境）的 PPT——
版本清單、版本下拉、PPT 檔案完全不分 workspace，誰最新就顯示誰。
定案：「全庫我沒有產過就不要顯示 PPT」。

## 設計

1. 產報表時寫 `version_meta.json`（version／workspace_id／workspace_name）進
   版本目錄，隨產物一起上傳 DB——**輕量標記檔**，列表端點讀它不用開
   124KB 的 report_data.json（維持列表不撈大檔的效率契約）。
2. `/reports/versions?workspace_id=N` 過濾：只回該 workspace 產的版本。
   ⚠ **無 meta 的舊版本＝不歸屬任何 workspace＝帶過濾時不顯示**
   （使用者：「沒產過就不要顯示」；舊版本重產即可）。
   不帶參數＝回全部（CLI 與既有呼叫相容）。
3. PPT 檔案清單掛在版本下，版本過濾後 PPT 自然跟著區隔。
4. 前端版本清單／匯出版本下拉一律帶當前 workspace（全庫也有真實 id）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.reports import chart_runner


class VersionMetaWrittenTests(unittest.TestCase):
    """🔴 chart_runner 產版本時要寫 version_meta.json。"""

    _ROWS = {"application_trend": [{"application_year": 2024, "patent_count": 2}]}

    def _stub_run_report(self, name, **kwargs):
        d = chart_runner.REPORT_DEFINITIONS[name]
        rows = self._ROWS.get(name, [{"x": 1}])
        return {"name": name, "label": d.label, "label_zh": d.label_zh,
                "report_type": d.report_type, "rows": rows, "row_count": len(rows)}

    def test_meta_file_written_with_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", self._stub_run_report), \
                 mock.patch.object(chart_runner, "fetch_patent_kind_summary", dict):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp), report_names=["application_trend"],
                    workspace_id=7, workspace_name="滑雪機")
            run_dir = Path(result["output_dir"])
            meta = json.loads((run_dir / "version_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["workspace_id"], 7)
        self.assertEqual(meta["workspace_name"], "滑雪機")
        self.assertEqual(meta["version"], run_dir.name)
        self.assertIn("version_meta.json", result["files"],
                      "meta 檔要進 files 清單才會被上傳 DB")

    def test_parameters_carry_workspace_id(self):
        """workspace_id 也進 parameters（name 會撞名，id 才是穩定歸屬鍵）。"""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", self._stub_run_report), \
                 mock.patch.object(chart_runner, "fetch_patent_kind_summary", dict):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp), report_names=["application_trend"],
                    workspace_id=7, workspace_name="滑雪機")
            rd = json.loads((Path(result["output_dir"]) / "report_data.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(rd["parameters"]["workspace_id"], 7)

    def test_no_workspace_no_meta_keys(self):
        """⚠ 不給 workspace＝meta 檔不含歸屬鍵（版本不歸屬任何 workspace）。"""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", self._stub_run_report), \
                 mock.patch.object(chart_runner, "fetch_patent_kind_summary", dict):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp), report_names=["application_trend"])
            meta = json.loads((Path(result["output_dir"]) / "version_meta.json")
                              .read_text(encoding="utf-8"))
        self.assertNotIn("workspace_id", meta)


class VersionListFilterTests(unittest.TestCase):
    """🔴 /reports/versions 依 workspace_id 過濾；無 meta 舊版本不顯示。"""

    def _client_with_versions(self, tmp):
        from fastapi.testclient import TestClient

        from backend.app import main as app_main

        root = Path(tmp)
        for name, meta in (
            ("report_trial_20260731_010101", {"workspace_id": 1}),
            ("report_trial_20260731_020202", {"workspace_id": 2}),
            ("report_trial_20260731_030303", None),  # 舊版本無 meta
        ):
            d = root / name
            d.mkdir(parents=True)
            (d / "report_data.json").write_text("{}", encoding="utf-8")
            if meta is not None:
                (d / "version_meta.json").write_text(
                    json.dumps({"version": name, **meta}), encoding="utf-8")
        patcher = mock.patch.object(app_main, "REPORT_OUTPUT_ROOT", root)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(app_main.app)

    def test_filtered_by_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client_with_versions(tmp)
            resp = client.get("/api/v1/reports/versions", params={"workspace_id": 1})
            got = [v["version"] for v in resp.json()["versions"]]
        self.assertEqual(got, ["report_trial_20260731_010101"],
                         "只該回 workspace 1 的版本；無 meta 舊版不得出現")

    def test_unfiltered_returns_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client_with_versions(tmp)
            resp = client.get("/api/v1/reports/versions")
            got = [v["version"] for v in resp.json()["versions"]]
        self.assertEqual(len(got), 3, "不帶參數維持回全部（CLI 相容）")


class FrontendWiringTests(unittest.TestCase):
    """🔴 前端版本清單與匯出版本下拉要帶當前 workspace。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).resolve().parents[1] / "backend" / "app" /
                    "static" / "index.html").read_text(encoding="utf-8")

    def test_report_versions_fetch_scoped(self):
        import re

        body = re.search(r"async function loadReportVersions\(\).*?\n\}", cls_html := self.html, re.S)
        self.assertIsNotNone(body)
        self.assertIn("workspace_id", body.group(0),
                      "報表種類頁版本清單未帶 workspace——會顯示別的 workspace 的版本")

    # ⚠ 2026-08-13 退場 test_export_versions_fetch_scoped：匯出頁的版本下拉
    #   （`loadExportVersionOptions`）隨 tasks 0 清空移除，與報表種類頁的下拉重複。
    #   「版本清單要帶 workspace」這條契約由上面 test_report_versions_fetch_scoped
    #   守著同一件事——現在只剩一個落點，反而不會漂移。


class HandlerWiringTests(unittest.TestCase):
    def test_handler_passes_workspace_id(self):
        import inspect

        from backend.app.worker import handlers

        src = inspect.getsource(handlers.handle_report_generate)
        self.assertIn('"workspace_id"', src, "handler 未把 workspace_id 傳進引擎")


if __name__ == "__main__":
    unittest.main()
