"""受理局「申請 vs 現存有效」合併頁（2026-08-07 使用者定案）。

p04（受理局分布，件）＋ p06（國家佈局現有保護，存活家族數）合成一張：
每國兩條 bar、口徑「件 vs 件」（申請件數 vs 狀態桶「已授權」件數）、
備註寫清楚定義；家族數降為頁尾註記。判準見 output/p1_jur_merge_criteria.md。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.reports import chart_runner
from backend.app.reports.report_definitions import REPORT_DEFINITIONS
from backend.app.reports.report_engine import build_report_sql
from backend.app.transforms.legal_status import STATUS_BUCKET_ORDER


ROWS = [
    # (country, legal_status, patent_count)——模擬 SQL 群組結果，含 None 未知。
    {"country_code": "CN", "legal_status": "授权", "patent_count": 20},
    {"country_code": "CN", "legal_status": "到期(Expiration of the term)", "patent_count": 15},
    {"country_code": "CN", "legal_status": "审查中", "patent_count": 3},
    {"country_code": "TW", "legal_status": None, "patent_count": 7},
    {"country_code": "TW", "legal_status": "已核准", "patent_count": 2},
    {"country_code": "US", "legal_status": "授权", "patent_count": 6},
    {"country_code": "EP", "legal_status": "撤回", "patent_count": 2},
]


class DefinitionContractTests(unittest.TestCase):
    def test_country_distribution_groups_status(self):
        """🔴 契約更新（2026-08-07 合併頁定案）：受理局報表加 legal_status 群組，
        SQL 只回原值——桶收斂唯一定義處在 transforms/legal_status。"""
        d = REPORT_DEFINITIONS["country_distribution"]
        self.assertEqual(tuple(d.group_by), ("country_code", "legal_status"))

    def test_sql_has_no_status_literals(self):
        sql, _ = build_report_sql(REPORT_DEFINITIONS["country_distribution"], {}, 1000)
        for literal in ("授权", "已核准", "到期", "granted"):
            self.assertNotIn(literal, sql)

    def test_blank_status_not_excluded(self):
        """未知桶要現形：legal_status 空值不得被 exclude。"""
        d = REPORT_DEFINITIONS["country_distribution"]
        self.assertNotIn("legal_status", tuple(d.exclude_blank_columns or ()))


class PivotTests(unittest.TestCase):
    def test_pivot_four_buckets_and_total(self):
        rows = chart_runner.country_status_pivot(ROWS)
        cn = next(r for r in rows if r["country_code"] == "CN")
        self.assertEqual(cn["已授權"], 20)
        self.assertEqual(cn["已失效"], 15)
        self.assertEqual(cn["審查中"], 3)
        self.assertEqual(cn["未知"], 0)
        self.assertEqual(cn["申請件數"], 38)
        tw = next(r for r in rows if r["country_code"] == "TW")
        self.assertEqual(tw["未知"], 7)
        self.assertEqual(tw["已授權"], 2)

    def test_pivot_column_order_follows_bucket_order(self):
        rows = chart_runner.country_status_pivot(ROWS)
        keys = list(rows[0])
        self.assertEqual(keys[:2], ["country_code", "申請件數"])
        self.assertEqual(keys[2:], list(STATUS_BUCKET_ORDER))

    def test_pivot_sorted_by_application_desc(self):
        rows = chart_runner.country_status_pivot(ROWS)
        totals = [r["申請件數"] for r in rows]
        self.assertEqual(totals, sorted(totals, reverse=True))


class PairedBarChartTests(unittest.TestCase):
    def _render(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "jurisdiction_distribution.svg"
        chart_runner.render_paired_bar_chart(
            path, "專利受理局分布", chart_runner.country_status_pivot(ROWS),
            label_key="country_code",
            series=(("申請件數", "申請件數"), ("現存有效", "已授權")),
        )
        return path.read_text(encoding="utf-8")

    def test_two_bars_per_country_with_values(self):
        svg = self._render()
        # CN 兩條：申請 38、現存有效 20；值以「N 件」標示。
        self.assertIn("38 件", svg)
        self.assertIn("20 件", svg)
        # TW：申請 9、有效 2。
        self.assertIn("9 件", svg)
        self.assertIn("2 件", svg)

    def test_legend_defines_both_series(self):
        svg = self._render()
        self.assertIn("申請件數", svg)
        self.assertIn("現存有效", svg)


class BuilderIntegrationTests(unittest.TestCase):
    def test_country_map_section_note_defines_calibers(self):
        """備註要寫清楚定義（使用者原話）＋未知件數點名＋家族一行註記。"""
        src = Path("backend/app/reports/chart_runner.py").read_text(encoding="utf-8")
        start = src.index("def _build_country_map_section")
        end = src.index("def ", start + 10)
        body = src[start:end]
        for required in ("已授權", "含死案", "未知", "家族"):
            self.assertIn(required, body, f"合併頁 note 缺定義關鍵字：{required}")

    def test_family_layout_section_removed_from_ppt_flow(self):
        """🔴 刪頁：family 卡不再出（報表定義保留給 Web）。"""
        specs = [s.key for s in chart_runner.SECTION_SPECS]
        self.assertNotIn("family_layout", specs)
        self.assertIn("country_map", specs)
        self.assertIn("family_country_layout", REPORT_DEFINITIONS)


if __name__ == "__main__":
    unittest.main()
