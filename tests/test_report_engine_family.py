"""家族層級報表在報表引擎的契約測試（純函式，不連 DB）。

涵蓋：family_country_layout 的 SQL 組裝、filters/patent_ids 的家族集合轉譯、
surrogate 規則 SQL 與 Python 端一致、既有 patent 層級報表不受影響。
"""
from __future__ import annotations

import unittest

from backend.app.reports.report_definitions import REPORT_DEFINITIONS
from backend.app.reports.report_engine import (
    FAMILY_ID_EXPRESSION,
    build_family_scope_clause,
    build_report_sql,
)
from backend.app.transforms.family_layout import _surrogate_family_id


class FamilyReportSqlTests(unittest.TestCase):
    """build_report_sql 對家族層級報表定義的輸出契約。"""

    def test_family_country_layout_counts_families(self) -> None:
        """aggregate SQL 以 COUNT(family_id) 計數、來源為 report_family_country。"""
        definition = REPORT_DEFINITIONS["family_country_layout"]
        sql, params = build_report_sql(definition, filters=None, limit=None)
        self.assertIn('COUNT("family_id")::int AS patent_count', sql)
        self.assertIn('FROM "derived_layer"."report_family_country"', sql)
        self.assertIn('GROUP BY "country_code"', sql)
        self.assertEqual(params, {})

    def test_family_reports_translate_patent_ids_to_family_scope(self) -> None:
        """家族報表收到 patent_ids → 轉譯成「選中專利所屬家族」的 IN 子查詢。"""
        # RPT-011：family_quality_detail 已刪，家族報表只剩佈局一張。
        for name in ("family_country_layout",):
            sql, params = build_report_sql(
                REPORT_DEFINITIONS[name], filters=None, limit=None, patent_ids=[1, 2]
            )
            self.assertIn('"family_id" IN (SELECT DISTINCT', sql, msg=name)
            self.assertIn('FROM "derived_layer"."report_patent_base"', sql, msg=name)
            self.assertIn("patent_id = ANY(%(patent_ids)s)", sql, msg=name)
            self.assertEqual(params["patent_ids"], [1, 2], msg=name)

    def test_family_reports_translate_filters_to_family_scope(self) -> None:
        """家族報表收到 filters → 白名單條件套在子查詢（patent 層），外層只篩 family_id。"""
        sql, params = build_report_sql(
            REPORT_DEFINITIONS["family_country_layout"],
            filters={"applicant_display_name": "ACME"},
            limit=None,
        )
        self.assertIn('"family_id" IN (SELECT DISTINCT', sql)
        self.assertIn('"applicant_display_name" = %(filter_0)s', sql)
        self.assertEqual(params["filter_0"], "ACME")

    def test_family_scope_clause_without_conditions_is_not_built(self) -> None:
        """不帶篩選＝全庫：SQL 不含家族子查詢（維持既有行為）。"""
        sql, params = build_report_sql(
            REPORT_DEFINITIONS["family_country_layout"], filters=None, limit=None
        )
        self.assertNotIn("IN (SELECT", sql)
        self.assertEqual(params, {})

    def test_surrogate_rule_locked_between_python_and_sql(self) -> None:
        """surrogate 家族 id 規則兩端一致：Python 'P{patent_id}' ↔ SQL 'P' || patent_id。

        改任何一端都會讓本測試 fail——規則必須同步改、同步驗。
        """
        self.assertEqual(_surrogate_family_id(123), "P123")
        self.assertIn("'P' || patent_id::text", FAMILY_ID_EXPRESSION)
        self.assertIn('BTRIM("WIPS同族ID"::text)', FAMILY_ID_EXPRESSION)
        clause, _params = build_family_scope_clause({"country_code": "US"}, None)
        self.assertIn(FAMILY_ID_EXPRESSION, clause)

    def test_patent_level_reports_still_accept_patent_ids(self) -> None:
        """既有 patent 層級報表不受新 flag 影響，patent_ids 照常生效。"""
        definition = REPORT_DEFINITIONS["country_distribution"]
        sql, params = build_report_sql(definition, filters=None, limit=None, patent_ids=[1, 2])
        self.assertIn("patent_id = ANY(%(patent_ids)s)", sql)
        self.assertEqual(params["patent_ids"], [1, 2])

    def test_quality_detail_report_removed(self) -> None:
        """RPT-011（2026-08-06）：品質稽核不給決策者看，報表已刪；
        家族完整性由國家佈局頁註記承接（chart_runner 直查 view）。"""
        self.assertNotIn("family_quality_detail", REPORT_DEFINITIONS)


if __name__ == "__main__":
    unittest.main()
