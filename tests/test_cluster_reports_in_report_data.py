"""分群兩份報表必須進 report_data.json 的 reports（2026-07-30 實機定位）。

## 問題

實機 PPT 只出 11 頁（規劃 17 頁）——`cluster_topic_table`／`opportunity_quadrant`
兩頁消失。job #133 的 `report_names` 明確含這兩個、SVG（`opportunity_quadrant_tech.svg`
等）也產出來了，但 `report_data.json` 的 `reports` bucket **根本沒有這兩個 key**
（13 個 key 全列出來確認過）。

## 根因

`reports`／`family_reports` bucket 只裝 SQL fetch 的報表（`persist_reports`）。
這兩份是 `report_type="cluster"`、`source_table=""`——資料由分群引擎經
`ctx.cluster_data` 注入、在 `_build_cluster_analytics_section` 內部產
（`topic_rows`／`build_opportunity_matrix`），只落到 `chart_rows` 與 `sections`，
從頭就進不了 `reports`。而 build_ppt 的 `_page_should_render` 只查
`reports`／`family_reports` → 判定無資料 → 跳頁。

## 定案

`_build_cluster_analytics_section` 把兩份組成報表形狀存 `ctx.cluster_reports`，
組檔時**顯式併入 `reports`**。

⚠ 兩個設計點：
1. **不走既有 `supports_patent_ids` 分流**——兩份該欄是 False，照現有條件會被
   丟進 `family_reports`（語意是家族報表，不對）。
2. **只在有 cluster_data 時併**——沒跑分群的版本不得出現空殼報表
   （空殼會讓 PPT 出空頁）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.app.reports import chart_runner

TWO_SOURCE_DATA = {
    "topics": [
        {"topic_code": "T001", "label": "散熱防塵", "source_field": "wips_independent_claims"},
        {"topic_code": "T002", "label": "速度控制", "source_field": "wips_independent_claims"},
        {"topic_code": "E001", "label": "降噪效果", "source_field": "effect_summary"},
    ],
    "assignments": [
        {"topic_code": "T001", "patent_id": 1},
        {"topic_code": "T002", "patent_id": 2},
        {"topic_code": "E001", "patent_id": 3},
    ],
    "normalized_applicants": [
        {"patent_id": 1, "applicant_name": "TSMC"},
        {"patent_id": 2, "applicant_name": "UMC"},
        {"patent_id": 3, "applicant_name": "TSMC"},
    ],
    "top_applicants_ws": ["TSMC"],
}


def _fake_ctx(tmp: str):
    return SimpleNamespace(
        run_dir=Path(tmp), chart_rows={}, sections=[], report=None,
        cluster_data=None, cluster_reports={}, ipc_levels=(4, 5), cpc_levels=(4, 5))


class ClusterReportsBuilderTests(unittest.TestCase):
    """section builder 要把兩份報表組成 report 形狀存 ctx.cluster_reports。"""

    def _built(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _fake_ctx(tmp)
            ctx.cluster_data = TWO_SOURCE_DATA
            chart_runner._build_cluster_analytics_section(ctx)
            return ctx

    def test_both_reports_registered(self):
        ctx = self._built()
        for key in ("cluster_topic_table", "opportunity_quadrant"):
            with self.subTest(key=key):
                self.assertIn(key, ctx.cluster_reports,
                              f"{key} 未進 cluster_reports——PPT 端會判無資料跳頁")

    def test_report_shape_matches_sql_reports(self):
        """形狀要與 SQL 報表一致（label_zh／rows／row_count），消費端才不用特判。"""
        ctx = self._built()
        for key, label in (("cluster_topic_table", "主題分類統計表"),
                           ("opportunity_quadrant", "機會四象限")):
            with self.subTest(key=key):
                entry = ctx.cluster_reports[key]
                self.assertEqual(entry["label_zh"], label)
                self.assertTrue(entry["rows"], f"{key} rows 不得為空")
                self.assertEqual(entry["row_count"], len(entry["rows"]))

    def test_opportunity_rows_carry_source_field_and_thresholds(self):
        """機會矩陣列要帶通道（source_field），entry 要帶各通道中位數門檻。

        ⚠ source_field：成對報表在 PPT 可分頁／同頁比較，消費端靠它切分。
        ⚠ thresholds：象限判讀（相對中位數的位置）要能重現，不得每次重算。
        """
        ctx = self._built()
        entry = ctx.cluster_reports["opportunity_quadrant"]
        fields = {row.get("source_field") for row in entry["rows"]}
        self.assertEqual(fields, {"wips_independent_claims", "effect_summary"},
                         "兩通道的列都要在且帶 source_field")
        self.assertIn("thresholds", entry)
        for th in entry["thresholds"].values():
            self.assertIn("patent_count_median", th)
            self.assertIn("applicant_count_median", th)

    def test_no_cluster_data_no_reports(self):
        """⚠ 沒跑分群＝兩鍵不存在，不得出現空殼（PPT 會出空頁）。"""
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _fake_ctx(tmp)  # cluster_data=None
            chart_runner._build_cluster_analytics_section(ctx)
            self.assertEqual(ctx.cluster_reports, {})


class ReportDataPersistenceTests(unittest.TestCase):
    """端到端：report_data.json 的 reports bucket 要含兩鍵。"""

    _REPORT_STUB_ROWS = {
        "application_trend": [{"application_year": 2020, "patent_count": 3}],
    }

    def _stub_run_report(self, name, **kwargs):
        rows = self._REPORT_STUB_ROWS.get(name, [{"x": 1}])
        d = chart_runner.REPORT_DEFINITIONS[name]
        return {"name": name, "label": d.label, "label_zh": d.label_zh,
                "report_type": d.report_type, "rows": rows, "row_count": len(rows)}

    def _render(self, tmp, **kwargs):
        with mock.patch.object(chart_runner, "run_report", self._stub_run_report):
            result = chart_runner.run_chart_trial(output_dir=Path(tmp), **kwargs)
        run_dir = Path(result["output_dir"])
        return json.loads((run_dir / "report_data.json").read_text(encoding="utf-8"))

    def test_reports_bucket_contains_cluster_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = self._render(tmp, report_names=["cluster_analytics"],
                              cluster_data=TWO_SOURCE_DATA)
        for key in ("cluster_topic_table", "opportunity_quadrant"):
            with self.subTest(key=key):
                self.assertIn(key, rd["reports"],
                              f"{key} 不在 reports bucket——build_ppt 會跳頁")
                self.assertTrue(rd["reports"][key]["rows"])
        # ⚠ 不得落到 family_reports（那是家族報表的 bucket）
        for key in ("cluster_topic_table", "opportunity_quadrant"):
            self.assertNotIn(key, rd.get("family_reports", {}))

    def test_without_cluster_data_keys_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = self._render(tmp, report_names=["application_trend"])
        for key in ("cluster_topic_table", "opportunity_quadrant"):
            self.assertNotIn(key, rd["reports"])


if __name__ == "__main__":
    unittest.main()
