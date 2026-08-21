"""趨勢年度四欄（openspec `improve-report-professionalism`，問題 9，2026-08-05 定案）。

## 定案

`application_trend` 改版加年度四欄：**件數／家族數／涉及技術群／首現技術群**。
圖不改（仍是件數雙線）；四欄是**確定性資料**，供趨勢頁解讀寫出
「真爆發 vs 同族延伸」——沒有這組數字，AI 只能對著件數編故事
（踩「AI 不算正式統計」紅線）。

規格實測範例（技術通道）：2020＝4 件 2 族 0 首現（同族延伸）、
2022＝10 件 10 族 2 首現（真爆發）、2025＝2 件 1 族（同族延伸）。

## 口徑

- **家族數**＝該年申請案 distinct `WIPS同族ID`；⚠ 無同族 ID 的案各算一族
  （COALESCE 到 patent_id），不得整批消失。
- **涉及技術群**＝該年案件觸及的技術通道主題數（`source_field` 過濾，
  功效通道不算——技術演進看的是技術線）。
- **首現技術群**＝首次出現年＝該年的主題數。
- ⚠ 無分群資料（全庫檢視／未分群）→ 技術群兩欄**不出現**，不補 0
  ——0 是「有分群但沒觸及」，缺鍵才是「沒分群」。兩者混同會誤導解讀。
"""
from __future__ import annotations

import unittest


class FamilyCountAggregateTests(unittest.TestCase):
    """SQL 端：家族數聚合進 application_trend 定義。"""

    def test_aggregate_function_registered(self):
        from backend.app.reports.report_engine import AGGREGATE_FUNCTIONS

        sql = AGGREGATE_FUNCTIONS["count_distinct_family"]
        self.assertIn("COUNT(DISTINCT", sql)
        self.assertIn("COALESCE", sql, "無同族 ID 的案要各算一族，不得消失")
        self.assertIn("patent_id", sql)

    def test_application_trend_has_family_count(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        aliases = {entry[2] for entry in REPORT_DEFINITIONS["application_trend"].aggregates}
        self.assertIn("family_count", aliases)

    def test_sql_contract(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS
        from backend.app.reports.report_engine import build_report_sql

        sql, _ = build_report_sql(
            REPORT_DEFINITIONS["application_trend"], filters=None, limit=None)
        self.assertIn('COUNT(DISTINCT', sql)
        self.assertIn('"WIPS同族ID"', sql)
        self.assertIn('AS "family_count"', sql)



class TrendRowEnrichmentTests(unittest.TestCase):
    """合併表：四欄進前端數據表 rows；無分群時技術群欄不出現。"""

    APP_ROWS = [
        {"application_year": 2020, "patent_count": 4, "family_count": 2},
        {"application_year": 2022, "patent_count": 10, "family_count": 10},
    ]
    PUB_ROWS = [{"授權公告年": 2022, "patent_count": 3}]


    def test_merge_carries_family_count(self):
        """family_count 仍隨 application_rows 帶入。

        ⚠ 原本這支同時驗 topic_count／new_topic_count，那條路已於 2026-08-18
        隨「趨勢表不放技術主題」整段移除（含 `topic_columns` 參數與
        `annual_topic_columns`）——留著參數卻不產生欄位，等於每次算完就丟。
        """
        from backend.app.reports.chart_runner import merge_annual_trend_rows

        by_year = {r["year"]: r for r in
                   merge_annual_trend_rows(self.APP_ROWS, self.PUB_ROWS)}
        self.assertEqual(by_year[2020]["family_count"], 2)
        self.assertEqual(by_year[2022]["family_count"], 10)
        self.assertNotIn("topic_count", by_year[2022])

    def test_without_cluster_topic_columns_absent(self):
        """⚠ 無分群 → 技術群欄**缺鍵**，不補 0——0 與「沒分群」是兩回事。"""
        from backend.app.reports.chart_runner import merge_annual_trend_rows

        rows = merge_annual_trend_rows(self.APP_ROWS, self.PUB_ROWS)
        self.assertNotIn("topic_count", rows[0])
        self.assertNotIn("new_topic_count", rows[0])
        self.assertEqual(rows[0]["family_count"], 2)

    def test_column_labels_registered(self):
        """欄名對照要有中文——缺了網頁表格會印英文鍵（欄名外洩）。"""
        from backend.app.reports.chart_runner import DATA_COLUMN_LABELS

        for key, zh in (("family_count", "家族數"),
                        ("topic_count", "涉及技術主題"),
                        ("new_topic_count", "首現技術主題")):
            self.assertEqual(DATA_COLUMN_LABELS.get(key), zh)


class TrendSectionIntegrationTests(unittest.TestCase):
    """builder 接線：cluster_data 有無 → chart_rows 四欄形狀（覆蓋 2574–2575）。"""

    def _run(self, cluster_data):
        from unittest import mock

        from backend.app.reports import chart_runner

        app_rows = [{"application_year": 2022, "patent_count": 10, "family_count": 10}]
        pub_rows = [{"授權公告年": 2022, "patent_count": 3}]

        def stub_run_report(name, filters=None, limit=None, patent_ids=None, **kwargs):
            rows = app_rows if name == "application_trend" else pub_rows
            return {"report_name": name, "label": name, "label_zh": name,
                    "rows": rows, "row_count": len(rows)}

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            ctx = chart_runner.ChartContext(
                run_dir=Path(tmp), ranking_limit=20, ipc_levels=(4,), cpc_levels=(4,),
                patent_ids=None, filters=None, analysis_id=None,
                report_scope="company", cluster_data=cluster_data)
            with mock.patch.object(chart_runner, "run_report", stub_run_report):
                chart_runner._build_trend_section(ctx)
            return ctx.chart_rows["annual_trend"]

    def test_trend_rows_have_no_topic_columns(self):
        """🔴 契約更新（2026-08-17 使用者「這個技術主題欄位拿掉」）：

        趨勢表只留年份／申請件數／授權公告件數三欄。技術主題兩欄退場的理由是
        **圖上呈現不出來**——表格有一個維度、圖讀不到，讀者無從對照。
        ⚠ `merge_annual_trend_rows` 仍算得出這兩欄（單元測試保留），
        只是 `_trend_row` 不再把它們放進顯示用的 rows。
        """
        rows = self._run({
            "topics": [{"topic_code": "T001", "source_field": "wips_independent_claims"}],
            "assignments": [{"topic_code": "T001", "patent_id": 1}],
            "patents": {1: {"application_year": 2022}},
        })
        self.assertNotIn("topic_count", rows[0])
        self.assertNotIn("new_topic_count", rows[0])
        self.assertEqual(rows[0]["family_count"], 10)

    def test_without_cluster_topic_columns_absent(self):
        rows = self._run(None)
        self.assertNotIn("topic_count", rows[0])
        self.assertEqual(rows[0]["family_count"], 10)


class FamilyQualityFetchTests(unittest.TestCase):
    """`_fetch_family_quality_rows`：查詢失敗回空、成功回列（覆蓋 2612–2623、2667）。

    ⚠ RPT-011 後家族品質只剩「國家佈局頁註記」一個用途；查詢炸掉時註記顯示
    「本次無家族資料可核對」而**不得**讓整批出圖失敗——註記是附註不是主體。
    """

    def test_pool_failure_returns_empty(self):
        from unittest import mock

        from backend.app.reports import chart_runner

        with mock.patch("backend.app.db.connection.get_pool",
                        side_effect=RuntimeError("no db")):
            self.assertEqual(chart_runner._fetch_family_quality_rows(), [])

    def test_success_returns_rows(self):
        from unittest import mock

        from backend.app.reports import chart_runner

        fake_rows = [{"family_incomplete": True, "is_surrogate_family": False,
                      "unknown_status_count": 0, "pending_status_count": 0,
                      "ep_in_transition_count": 0, "ep_missing_epc_count": 0}]
        cur = mock.MagicMock()
        cur.fetchall.return_value = fake_rows
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        pool = mock.MagicMock()
        pool.connection.return_value.__enter__.return_value = conn
        with mock.patch("backend.app.db.connection.get_pool", return_value=pool):
            rows = chart_runner._fetch_family_quality_rows()
        self.assertEqual(rows, fake_rows)


if __name__ == "__main__":
    unittest.main()
