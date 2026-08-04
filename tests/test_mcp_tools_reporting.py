"""MCP reporting tools（純函式層）的單元測試。

引擎呼叫以 mock 取代驗接線；get_data_status 走真 DB smoke（連不到就 skip）。
"""
from __future__ import annotations

import unittest
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

from backend.app.mcp_server import tools_reporting
from backend.app.mcp_server._shared import json_safe
from backend.app.reports.report_definitions import DEFAULT_REPORT_NAMES, REPORT_DEFINITIONS


class JsonSafeTests(unittest.TestCase):
    """DB／dataclass／Path 常見型別必須轉成 JSON 原生型別。"""

    def test_scalar_passthrough(self):
        for value in (None, True, 3, 2.5, "x"):
            self.assertEqual(json_safe(value), value)

    def test_decimal_becomes_float(self):
        self.assertEqual(json_safe(Decimal("12.50")), 12.5)

    def test_dates_become_iso_strings(self):
        self.assertEqual(json_safe(date(2026, 7, 16)), "2026-07-16")
        self.assertEqual(json_safe(datetime(2026, 7, 16, 9, 30)), "2026-07-16T09:30:00")

    def test_path_becomes_string(self):
        self.assertEqual(json_safe(Path("a") / "b"), str(Path("a") / "b"))

    def test_containers_recursive(self):
        value = {"k": (Decimal("1"), [date(2026, 1, 1)]), 5: "v"}
        self.assertEqual(json_safe(value), {"k": [1.0, ["2026-01-01"]], "5": "v"})

    def test_dataclass_becomes_dict(self):
        @dataclass
        class Row:
            name: str
            amount: Decimal

        self.assertEqual(json_safe(Row("a", Decimal("2"))), {"name": "a", "amount": 2.0})

    def test_unknown_type_falls_back_to_str(self):
        class Odd:
            def __str__(self):
                return "odd!"

        self.assertEqual(json_safe(Odd()), "odd!")


class ListReportsTests(unittest.TestCase):
    def test_default_report_names_match_definitions(self):
        """預設報表名單＝定義中**未停產**的那些，且維持定義順序。

        🔴 2026-08-03 修正：原本斷言 `len == 17` 且 `== tuple(REPORT_DEFINITIONS)`。
        兩者都已過期——`b94748e`（痛點矩陣真正停產）與 `3eb43a8`（移除失效的
        最新受讓人排名）之後，預設名單剩 15 個，而 `REPORT_DEFINITIONS` 仍有 16 個
        （`pain_point_quadrant` 的**定義保留**，只是不列入預設）。

        ⚠ 改為「不寫死數量、比對過濾後的順序」：報表增減是常態，
        寫死數字會讓每次增刪都紅在這裡，而不是紅在真正壞掉的地方。
        ⚠ 今天這是第四支同類問題（另有 acquired_count、refresh_derived、
        chart_wider_than_data）——共同成因是長期只跑 `-k` 篩選的回歸。
        """
        self.assertIsInstance(DEFAULT_REPORT_NAMES, tuple)
        selected = set(DEFAULT_REPORT_NAMES)
        self.assertTrue(selected.issubset(set(REPORT_DEFINITIONS)),
                        "預設名單出現未定義的報表")
        self.assertEqual(DEFAULT_REPORT_NAMES,
                         tuple(n for n in REPORT_DEFINITIONS if n in selected),
                         "預設名單順序與 REPORT_DEFINITIONS 不一致")
        # 停產的報表**定義仍在**（供既有版本讀取），但不得列入預設
        self.assertNotIn("pain_point_quadrant", selected,
                         "痛點矩陣已於 2026-07-29 停產，不得回到預設名單")

    def test_catalog_covers_all_definitions(self):
        catalog = tools_reporting.list_reports()
        names = [item["name"] for item in catalog["reports"]]
        self.assertEqual(sorted(names), sorted(REPORT_DEFINITIONS))
        self.assertEqual(catalog["default_report_names"], list(DEFAULT_REPORT_NAMES))
        for item in catalog["reports"]:
            self.assertIn("label_zh", item)
            self.assertIn("report_type", item)
            self.assertIn(item["filter_mode"], ("patent_level", "family_translated"))
        by_name = {item["name"]: item for item in catalog["reports"]}
        self.assertEqual(by_name["family_country_layout"]["filter_mode"], "family_translated")
        self.assertEqual(by_name["application_trend"]["filter_mode"], "patent_level")

    def test_filter_whitelist_included(self):
        catalog = tools_reporting.list_reports()
        self.assertIn("country_code", catalog["allowed_filter_columns"])
        self.assertEqual(catalog["allowed_filter_columns"], sorted(catalog["allowed_filter_columns"]))


class RunReportAnalysisTests(unittest.TestCase):
    """驗證輸入檢查與對引擎的接線（引擎本體另有 tests）。"""

    def test_none_report_names_uses_default_reports(self):
        """確認 MCP 未指定 report_names 時會把完整預設名單交給 data 與 chart。"""
        chart_result = {
            "output_dir": "out/report_trial_default",
            "files": [],
            "sections_rendered": [],
        }
        with mock.patch.object(tools_reporting, "run_reports_batch", return_value={}) as batch, \
                mock.patch.object(tools_reporting, "run_chart_trial", return_value=chart_result) as charts:
            result = tools_reporting.run_report_analysis()
        batch.assert_called_once_with(
            list(DEFAULT_REPORT_NAMES),
            filters=None,
            limit=tools_reporting.DEFAULT_ROW_LIMIT,
            patent_ids=None,
        )
        charts.assert_called_once_with(
            analysis_id=None, report_names=list(DEFAULT_REPORT_NAMES), filters=None
        )
        self.assertEqual(result["parameters"]["report_names"], list(DEFAULT_REPORT_NAMES))

    def test_empty_report_names_uses_default_reports(self):
        """確認 MCP 傳入空 report_names 時會把完整預設名單交給 data 與 chart。"""
        chart_result = {
            "output_dir": "out/report_trial_default",
            "files": [],
            "sections_rendered": [],
        }
        with mock.patch.object(tools_reporting, "run_reports_batch", return_value={}) as batch, \
                mock.patch.object(tools_reporting, "run_chart_trial", return_value=chart_result) as charts:
            result = tools_reporting.run_report_analysis([])
        batch.assert_called_once_with(
            list(DEFAULT_REPORT_NAMES),
            filters=None,
            limit=tools_reporting.DEFAULT_ROW_LIMIT,
            patent_ids=None,
        )
        charts.assert_called_once_with(
            analysis_id=None, report_names=list(DEFAULT_REPORT_NAMES), filters=None
        )
        self.assertEqual(result["parameters"]["report_names"], list(DEFAULT_REPORT_NAMES))

    def test_unknown_report_name_raises(self):
        with self.assertRaisesRegex(ValueError, "no_such_report"):
            tools_reporting.run_report_analysis(["no_such_report"])

    def test_data_only_skips_charts(self):
        with mock.patch.object(tools_reporting, "run_reports_batch", return_value={"application_trend": {"rows": []}}) as batch, \
                mock.patch.object(tools_reporting, "run_chart_trial") as charts:
            result = tools_reporting.run_report_analysis(
                ["application_trend"], filters={"country_code": "US"}, limit=10, with_charts=False,
            )
        batch.assert_called_once_with(["application_trend"], filters={"country_code": "US"}, limit=10, patent_ids=None)
        charts.assert_not_called()
        self.assertNotIn("charts", result)
        self.assertEqual(result["parameters"]["row_limit"], 10)

    def test_charts_wiring_and_shape(self):
        chart_result = {
            "output_dir": "out/report_trial_x",
            "files": ["annual_trend.svg", "report_data.json", "index.html"],
            "sections_rendered": ["annual_trend"],
        }
        with mock.patch.object(tools_reporting, "run_reports_batch", return_value={}) as batch, \
                mock.patch.object(tools_reporting, "run_chart_trial", return_value=chart_result) as charts:
            result = tools_reporting.run_report_analysis(["application_trend"])
        charts.assert_called_once_with(analysis_id=None, report_names=["application_trend"], filters=None)
        self.assertEqual(result["charts"]["output_dir"], "out/report_trial_x")
        self.assertTrue(result["charts"]["index_html"].endswith("index.html"))
        self.assertEqual(result["charts"]["sections_rendered"], ["annual_trend"])
        # 預設 limit 保護 context。
        batch.assert_called_once_with(
            ["application_trend"], filters=None, limit=tools_reporting.DEFAULT_ROW_LIMIT, patent_ids=None,
        )

    def test_analysis_snapshot_shared_by_data_and_charts(self):
        with mock.patch.object(tools_reporting, "fetch_analysis_patent_ids", return_value=[1, 2]) as fetch, \
                mock.patch.object(tools_reporting, "run_reports_batch", return_value={}) as batch, \
                mock.patch.object(tools_reporting, "run_chart_trial", return_value={
                    "output_dir": "o", "files": [], "sections_rendered": [], "export_count": 3,
                }), \
                mock.patch.object(tools_reporting, "_load_report_data_json", return_value={"reports": {}}), \
                mock.patch.object(tools_reporting, "save_workflow_output", return_value={
                    "run_id": 7, "output_type": "report_data", "version": 1,
                }):
            result = tools_reporting.run_report_analysis(["application_trend"], analysis_id=7)
        fetch.assert_called_once_with(7)
        self.assertEqual(batch.call_args.kwargs["patent_ids"], [1, 2])
        self.assertEqual(result["charts"]["export_count"], 3)

    def test_analysis_chart_payload_saved_to_workflow_outputs(self):
        """analysis_id 存在時，報表頁面用的 report_data.json 也要版本化回存 DB。"""
        with mock.patch.object(tools_reporting, "fetch_analysis_patent_ids", return_value=[1, 2]), \
                mock.patch.object(tools_reporting, "run_reports_batch", return_value={}), \
                mock.patch.object(tools_reporting, "run_chart_trial", return_value={
                    "output_dir": "o", "files": ["report_data.json"], "sections_rendered": [],
                }), \
                mock.patch.object(tools_reporting, "_load_report_data_json", return_value={"reports": {"x": {}}}), \
                mock.patch.object(tools_reporting, "save_workflow_output", return_value={
                    "run_id": 7, "output_type": "report_data", "version": 4,
                }) as save:
            result = tools_reporting.run_report_analysis(["application_trend"], analysis_id=7)
        save.assert_called_once_with(7, "report_data", {"reports": {"x": {}}})
        self.assertEqual(result["charts"]["report_data_version"], 4)


class _FakeCursor:
    """模擬 get_data_status 需要的 cursor 行為。"""

    def __init__(self):
        self._rows = iter([(10,), (10,), (3,), (2,), (datetime(2026, 7, 20, 12, 0),)])
        self.sql: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql):
        self.sql.append(sql)

    def fetchone(self):
        return next(self._rows)


class _FakeConnection:
    """模擬 pool connection。"""

    def __init__(self):
        self.cursor_obj = _FakeCursor()

    def cursor(self):
        return self.cursor_obj


class _FakeConnectionContext:
    """模擬 get_pool().connection() context manager。"""

    def __init__(self):
        self.conn = _FakeConnection()

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    """模擬 production pool，不連真 DB。"""

    def __init__(self):
        self.context = _FakeConnectionContext()

    def connection(self):
        return self.context


class DataStatusUnitTests(unittest.TestCase):
    """get_data_status 結構測試使用 mock，不連真 DB。"""

    def test_status_shape_with_mocked_pool(self):
        fake_pool = _FakePool()
        with mock.patch.object(tools_reporting, "get_pool", return_value=fake_pool), mock.patch.object(
            tools_reporting,
            "get_connection_kwargs",
            return_value={"host": "localhost", "port": 5433, "dbname": "patent"},
        ):
            status = tools_reporting.get_data_status()
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["database"]["port"], 5433)
        self.assertEqual(status["row_counts"]["patents"], 10)
        self.assertEqual(status["row_counts"]["report_patent_base"], 10)
        self.assertIsInstance(status["warnings"], list)


@unittest.skipUnless(os.environ.get("RUN_DB_TESTS") == "1", "set RUN_DB_TESTS=1 to run DB smoke")
class DataStatusSmokeTests(unittest.TestCase):
    """真 DB smoke：僅 RUN_DB_TESTS=1 時跑；連不到就 skip。"""

    @classmethod
    def setUpClass(cls):
        import psycopg
        from dotenv import load_dotenv

        from backend.app.db.connection import get_connection_kwargs

        project_root = Path(__file__).resolve().parents[1]
        load_dotenv(project_root / ".env", override=False)
        try:
            with psycopg.connect(**get_connection_kwargs(), connect_timeout=3):
                pass
        except Exception as exc:  # noqa: BLE001 - 任何連線失敗都代表環境沒 DB
            raise unittest.SkipTest(f"DB unreachable: {exc}")

    def test_status_shape(self):
        status = tools_reporting.get_data_status()
        self.assertIn(status["status"], ("ok", "warning"))
        self.assertIn("patents", status["row_counts"])
        self.assertIn("report_patent_base", status["row_counts"])
        self.assertIsInstance(status["warnings"], list)
        self.assertIn("port", status["database"])


if __name__ == "__main__":
    unittest.main()
