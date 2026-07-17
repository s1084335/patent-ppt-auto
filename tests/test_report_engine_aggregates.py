"""報表引擎額外聚合欄（ReportDefinition.aggregates）的契約測試。"""
from __future__ import annotations

import unittest

from backend.app.reports.report_definitions import REPORT_DEFINITIONS, ReportDefinition
from backend.app.reports.report_engine import build_report_sql


class AggregateColumnsTests(unittest.TestCase):
    """build_report_sql 對 aggregates 的 SQL 組裝契約。"""

    def test_company_rd_energy_sql(self) -> None:
        """研發能量報表：SUM 被引用/發明人數＋非空計數，全部帶別名。"""
        sql, _ = build_report_sql(REPORT_DEFINITIONS["company_rd_energy"], filters=None, limit=None)
        self.assertIn('COALESCE(SUM("(F1)引用文獻數"), 0)::bigint AS "cited_total"', sql)
        self.assertIn('COALESCE(SUM("發明人數"), 0)::bigint AS "inventor_total"', sql)
        self.assertIn('COUNT("(F1)引用文獻數")::int AS "cited_rows"', sql)
        self.assertIn('GROUP BY "applicant_display_name"', sql)

    def test_lifecycle_count_distinct(self) -> None:
        """生命週期報表：COUNT(DISTINCT 申請人) 別名 applicant_count。"""
        sql, _ = build_report_sql(REPORT_DEFINITIONS["lifecycle"], filters=None, limit=None)
        self.assertIn('COUNT(DISTINCT "applicant_display_name")::int AS "applicant_count"', sql)

    def test_top_cited_orders_by_citations(self) -> None:
        """高被引用排名：detail 型、依被引用數 DESC、排除無引用欄的列。"""
        sql, params = build_report_sql(REPORT_DEFINITIONS["top_cited_patents"], filters=None, limit=None)
        self.assertIn('ORDER BY "(F1)引用文獻數" DESC', sql)
        self.assertIn('NULLIF(BTRIM("(F1)引用文獻數"::text), \'\') IS NOT NULL', sql)
        self.assertEqual(params.get("limit"), 50)

    def test_applicant_country_matrix_groups_two_columns(self) -> None:
        """公司×國家交叉表：group by 申請人＋受理局兩欄、按件數排序、雙欄皆排空值。"""
        sql, _ = build_report_sql(
            REPORT_DEFINITIONS["applicant_country_distribution"], filters=None, limit=None
        )
        self.assertIn('GROUP BY "applicant_display_name", "country_code"', sql)
        self.assertIn('ORDER BY "patent_count" DESC', sql)
        self.assertIn('NULLIF(BTRIM("applicant_display_name"::text), \'\') IS NOT NULL', sql)
        self.assertIn('NULLIF(BTRIM("country_code"::text), \'\') IS NOT NULL', sql)

    def test_unknown_aggregate_function_rejected(self) -> None:
        """白名單外的聚合函式必須 raise，不可拼進 SQL。"""
        definition = ReportDefinition(
            name="bad",
            report_type="aggregate",
            label="Bad",
            label_zh="壞",
            source_table="derived_layer.report_patent_base",
            columns=("country_code",),
            group_by=("country_code",),
            aggregates=(("string_agg", "title", "x"),),
        )
        with self.assertRaises(ValueError):
            build_report_sql(definition, filters=None, limit=None)

    def test_existing_reports_unaffected(self) -> None:
        """沒有 aggregates 的既有報表 SQL 不含額外聚合片段。"""
        sql, _ = build_report_sql(REPORT_DEFINITIONS["country_distribution"], filters=None, limit=None)
        self.assertNotIn("SUM(", sql)
        self.assertNotIn("DISTINCT", sql)


if __name__ == "__main__":
    unittest.main()
