"""chart_runner 選擇性出圖（section registry）的單元測試。

不碰 DB：run_report 以 stub 取代，只驗 registry 覆蓋、選擇解析、
選擇性渲染的檔案輸出與唯一輸出資料夾行為。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.reports import chart_runner
from backend.app.reports.report_definitions import REPORT_DEFINITIONS


def fake_report(name: str, rows: list[dict]) -> dict:
    """依報表定義組出與 run_report 回傳同形狀的假結果。"""
    definition = REPORT_DEFINITIONS[name]
    return {
        "report_name": name,
        "label_zh": definition.label_zh,
        "label": definition.label,
        "report_type": definition.report_type,
        "row_count": len(rows),
        "rows": rows,
    }


class SectionRegistryTests(unittest.TestCase):
    """registry 完整性與 resolve_sections 的選擇規則。"""

    def test_registry_covers_all_report_definitions(self):
        # 新報表加進引擎卻沒掛 section 時，這裡會 fail——強制選擇性出圖不漏報表。
        covered = {name for spec in chart_runner.SECTION_SPECS for name in spec.reports}
        self.assertEqual(covered, set(REPORT_DEFINITIONS))

    def test_resolve_none_returns_all_sections(self):
        self.assertEqual(chart_runner.resolve_sections(None), chart_runner.SECTION_SPECS)

    def test_resolve_subset_keeps_registry_order(self):
        specs = chart_runner.resolve_sections(["lifecycle", "country_distribution"])
        self.assertEqual([spec.key for spec in specs], ["country_map", "lifecycle"])

    def test_resolve_application_trend_includes_growth(self):
        # 申請趨勢同時驅動雙線趨勢圖與年增率折線兩個 sections。
        keys = [spec.key for spec in chart_runner.resolve_sections(["application_trend"])]
        self.assertEqual(keys, ["annual_trend", "application_growth"])

    def test_resolve_family_reports_share_one_section(self):
        keys = [spec.key for spec in chart_runner.resolve_sections(["family_quality_detail"])]
        self.assertEqual(keys, ["family_layout"])

    def test_resolve_unknown_report_raises(self):
        with self.assertRaises(ValueError):
            chart_runner.resolve_sections(["no_such_report"])

    def test_resolve_empty_list_raises(self):
        with self.assertRaises(ValueError):
            chart_runner.resolve_sections([])


class CreateRunDirTests(unittest.TestCase):
    """同秒重複執行時輸出資料夾必須唯一，不可互寫。"""

    def test_same_second_gets_suffixed_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixed = mock.Mock()
            fixed.now.return_value.strftime.return_value = "20260716_120000"
            with mock.patch.object(chart_runner, "datetime", fixed):
                first = chart_runner._create_run_dir(Path(tmp), "report_trial_")
                second = chart_runner._create_run_dir(Path(tmp), "report_trial_")
            self.assertEqual(first.name, "report_trial_20260716_120000")
            self.assertEqual(second.name, "report_trial_20260716_120000_2")
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())


class MatrixChartTests(unittest.TestCase):
    """公司×國家交叉矩陣：前 N 大截取、每列一家公司不混算。"""

    def test_resolve_applicant_country_section(self):
        keys = [s.key for s in chart_runner.resolve_sections(["applicant_country_distribution"])]
        self.assertEqual(keys, ["applicant_country"])

    def test_matrix_top_limit_and_per_company_cells(self):
        rows = []
        for i in range(25):
            rows.append({"applicant_display_name": f"Co{i:02d}", "country_code": "US", "patent_count": 100 - i})
            rows.append({"applicant_display_name": f"Co{i:02d}", "country_code": "CN", "patent_count": 10})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "matrix.svg"
            meta = chart_runner.render_matrix_chart(
                path, "測試矩陣", rows, row_key="applicant_display_name", col_key="country_code"
            )
            svg = path.read_text(encoding="utf-8")
        self.assertEqual(meta["rows_drawn"], 20)   # 前 20 大截取
        self.assertEqual(meta["rows_total"], 25)
        self.assertEqual(meta["cols"], ["US", "CN"])  # 欄按總量排序
        self.assertIn("Co00", svg)      # 總量最大的公司入圖
        self.assertNotIn("Co24", svg)   # 第 21 名之後被截掉
        self.assertIn(">100<", svg)     # 儲存格是單一公司的值（未跨公司加總）
        self.assertNotIn(">2290<", svg)  # 不出現全欄加總值——確保沒混算


class SelectiveRenderTests(unittest.TestCase):
    """選擇性出圖端到端（run_report stub）：只產選中的檔、只查依賴的報表。"""

    def test_application_trend_only(self):
        fetched: list[str] = []

        def stub_run_report(name, filters=None, limit=None, patent_ids=None):
            fetched.append(name)
            rows = {
                "application_trend": [
                    {"application_year": 2019, "patent_count": 3},
                    {"application_year": 2020, "patent_count": 6},
                ],
                "publication_trend": [
                    {"publication_year": 2020, "patent_count": 4},
                ],
            }[name]
            return fake_report(name, rows)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", stub_run_report):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp),
                    report_names=["application_trend"],
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["sections_rendered"], ["annual_trend", "application_growth"])
            # 只查趨勢 section 依賴的兩張報表（公告補齊雙線），沒有其他報表被查。
            self.assertEqual(sorted(set(fetched)), ["application_trend", "publication_trend"])
            # 產出檔只有選中 sections 的圖＋固定兩檔。
            self.assertEqual(
                sorted(result["files"]),
                sorted(["annual_trend.svg", "application_growth.svg", "report_data.json", "index.html"]),
            )
            run_dir = Path(result["output_dir"])
            for filename in result["files"]:
                self.assertTrue((run_dir / filename).is_file(), filename)
            # 未選的 section（如受理局地圖）不得落檔。
            self.assertFalse((run_dir / "country_bubble.svg").exists())

    def test_report_cache_deduplicates_fetch(self):
        calls: list[str] = []

        def stub_run_report(name, filters=None, limit=None, patent_ids=None):
            calls.append(name)
            return fake_report(name, [{"application_year": 2020, "patent_count": 1}])

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", stub_run_report):
                chart_runner.run_chart_trial(
                    output_dir=Path(tmp),
                    report_names=["application_trend", "publication_trend"],
                )
        # application_trend 被 annual_trend 與 application_growth 兩個 sections 依賴，
        # 但快取後只實際查一次 DB。
        self.assertEqual(calls.count("application_trend"), 1)


if __name__ == "__main__":
    unittest.main()
