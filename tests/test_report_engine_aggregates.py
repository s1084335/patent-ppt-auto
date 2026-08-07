"""report_engine aggregate SQL contract tests."""

from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.reports.report_definitions import DEFAULT_REPORT_NAMES, REPORT_DEFINITIONS, ReportDefinition
from backend.app.reports.report_engine import build_report_sql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DEFINITIONS_FILE = PROJECT_ROOT / "backend" / "app" / "reports" / "report_definitions.py"
DEPRECATED_REPORTS_ARCHIVE = PROJECT_ROOT / "archive" / "deprecated" / "reports-citation-rd-energy"


class AggregateColumnsTests(unittest.TestCase):
    """驗證 active aggregate 報表的 SQL 組裝規則。"""

    def test_deprecated_reports_are_not_active(self) -> None:
        """已移出的報表不得留在正式 active report catalog。"""
        self.assertNotIn("company_rd_energy", REPORT_DEFINITIONS)
        self.assertNotIn("top_cited_patents", REPORT_DEFINITIONS)
        self.assertNotIn("recent_assignee_year_matrix", REPORT_DEFINITIONS)
        self.assertNotIn("company_rd_energy", DEFAULT_REPORT_NAMES)
        self.assertNotIn("top_cited_patents", DEFAULT_REPORT_NAMES)
        self.assertNotIn("recent_assignee_year_matrix", DEFAULT_REPORT_NAMES)

    def test_deprecated_definitions_removed_from_production_file(self) -> None:
        """top_cited_patents/company_rd_energy 的 definition 只能留在 archive。"""
        source = REPORT_DEFINITIONS_FILE.read_text(encoding="utf-8")
        self.assertNotIn('"top_cited_patents": ReportDefinition', source)
        self.assertNotIn('"company_rd_energy": ReportDefinition', source)
        self.assertNotIn("DEPRECATED_REPORT_DEFINITIONS", source)
        self.assertNotIn("REPORT_DEFINITIONS.pop", source)

    def test_archive_keeps_readable_python_sources(self) -> None:
        """archive 需有實際 Python 留存，不能只有 README。"""
        python_files = sorted(DEPRECATED_REPORTS_ARCHIVE.glob("*.py"))
        self.assertTrue(python_files, "deprecated report archive must include Python source files")
        archive_text = "\n".join(path.read_text(encoding="utf-8") for path in python_files)
        self.assertIn("top_cited_patents", archive_text)
        self.assertIn("company_rd_energy", archive_text)

    def test_lifecycle_status_analysis_sql(self) -> None:
        """🔴 2026-08-07 契約變更：lifecycle 改版「專利狀態分析」。

        原「年度 × COUNT(DISTINCT 申請人)」散點已裁撤（與趨勢頁重複，年度家數
        判讀由趨勢頁年度四欄承接）。新契約＝展開 VIEW 上 (申請人, 原始狀態) 聚合，
        狀態桶收斂在 chart_runner 端（唯一定義處 transforms/legal_status），
        **SQL 不得出現任何狀態字面**。"""
        sql, _ = build_report_sql(REPORT_DEFINITIONS["lifecycle"], filters=None, limit=None)
        self.assertIn('GROUP BY "applicant_display_name", "legal_status"', sql)
        self.assertIn('"derived_layer"."report_patent_applicant_expanded"', sql)
        self.assertNotIn("已授權", sql)
        self.assertNotIn("授权", sql)

    def test_publication_trend_reads_announcement_year(self) -> None:
        """授權公告趨勢要讀中文欄位「授權公告年」，不得沿用代表日期 publication_year。"""
        definition = REPORT_DEFINITIONS["publication_trend"]
        self.assertEqual(definition.label_zh, "專利授權公告趨勢")
        self.assertEqual(definition.columns, ("授權公告年",))
        self.assertEqual(definition.group_by, ("授權公告年",))
        self.assertEqual(definition.default_order, (("授權公告年", "asc"),))
        self.assertEqual(definition.exclude_blank_columns, ("授權公告年",))
        sql, _ = build_report_sql(definition, filters=None, limit=None)
        self.assertIn('GROUP BY "授權公告年"', sql)
        self.assertNotIn('GROUP BY "publication_year"', sql)

    def test_applicant_country_matrix_groups_two_columns(self) -> None:
        """申請人 × 國家矩陣必須同時 group by 申請人與國家。"""
        sql, _ = build_report_sql(
            REPORT_DEFINITIONS["applicant_country_distribution"], filters=None, limit=None
        )
        self.assertIn('GROUP BY "applicant_display_name", "country_code"', sql)
        self.assertIn('ORDER BY "patent_count" DESC', sql)
        self.assertIn('NULLIF(BTRIM("applicant_display_name"::text), \'\') IS NOT NULL', sql)
        self.assertIn('NULLIF(BTRIM("country_code"::text), \'\') IS NOT NULL', sql)

    def test_applicant_ranking_includes_recent_assignee_summary(self) -> None:
        """申請人排名同時彙總該申請人名下具最新受讓人的件數與受讓人明細。"""
        definition = REPORT_DEFINITIONS["applicant_ranking"]
        sql, params = build_report_sql(definition, filters=None, limit=None, patent_ids=[101, 102])
        self.assertIn('"applicant_display_name" AS "applicant_display_name"', sql)
        self.assertIn('COUNT("patent_id")::int AS patent_count', sql)
        self.assertIn('AS "recent_assignee_count"', sql)
        self.assertIn('AS "recent_assignee_display_names"', sql)
        self.assertIn('NULLIF(BTRIM("recent_assignee_display_name"::text), \'\') IS NOT NULL', sql)
        self.assertIn('patent_id = ANY(%(patent_ids)s)', sql)
        self.assertEqual(params["patent_ids"], [101, 102])

    def test_applicant_ranking_excludes_self_assignee(self) -> None:
        """轉讓藍段只計「最新受讓人≠申請人」：同名（專利未離手）不算轉讓、不進明細
        （2026-07-22 使用者回饋：斯蒂爾申請、最新受讓人也是斯蒂爾，不該分顏色）。"""
        sql, _ = build_report_sql(REPORT_DEFINITIONS["applicant_ranking"], filters=None, limit=None)
        excl = 'IS DISTINCT FROM NULLIF(BTRIM("applicant_display_name"::text), \'\')'
        self.assertIn(excl, sql)
        # count 與受讓人明細兩個聚合都要帶排除條件
        # #3（2026-08-05）：新增的「共同/單獨 × 已轉讓」兩個聚合沿用同一段排除條件，
        # 出現次數由 2 增為 4。此處要守的是「凡是轉讓相關的聚合都排除自己」，
        # 不是固定次數——釘死次數會讓每次新增同類聚合都假紅。
        self.assertGreaterEqual(sql.count(excl), 2)

    def test_applicant_year_matrix_definition_and_sql_contract(self) -> None:
        # ⚠ 原以 owner_year_matrix 驗矩陣 SQL 形狀；RPT-011 刪該報表後改驗
        # applicant_year_matrix（同構，走展開 VIEW）。防護意圖不變。
        """申請人年度矩陣以 applicant × application year 聚合（展開口徑）。"""
        definition = REPORT_DEFINITIONS["applicant_year_matrix"]
        self.assertEqual(definition.label_zh, "申請人年度專利分布矩陣")
        self.assertEqual(definition.columns, ("applicant_display_name", "application_year"))
        self.assertEqual(definition.group_by, ("applicant_display_name", "application_year"))
        self.assertEqual(
            definition.default_order,
            (("patent_count", "desc"), ("applicant_display_name", "asc"), ("application_year", "asc")),
        )
        sql, params = build_report_sql(definition, filters=None, limit=None, patent_ids=[7, 9])
        self.assertIn('GROUP BY "applicant_display_name", "application_year"', sql)
        self.assertIn('ORDER BY "patent_count" DESC, "applicant_display_name" ASC, "application_year" ASC', sql)
        self.assertIn('NULLIF(BTRIM("applicant_display_name"::text), \'\') IS NOT NULL', sql)
        self.assertIn('NULLIF(BTRIM("application_year"::text), \'\') IS NOT NULL', sql)
        self.assertIn('patent_id = ANY(%(patent_ids)s)', sql)
        self.assertEqual(params["patent_ids"], [7, 9])

    def test_unknown_aggregate_function_rejected(self) -> None:
        """未知 aggregate function 必須 fail loud，避免組出不可信 SQL。"""
        definition = ReportDefinition(
            name="bad",
            report_type="aggregate",
            label="Bad",
            label_zh="錯誤報表",
            source_table="derived_layer.report_patent_base",
            columns=("country_code",),
            group_by=("country_code",),
            aggregates=(("string_agg", "title", "x"),),
        )
        with self.assertRaises(ValueError):
            build_report_sql(definition, filters=None, limit=None)

    def test_existing_reports_unaffected(self) -> None:
        """沒有 aggregates 的既有報表 SQL 不應多出 SUM 或 DISTINCT。"""
        sql, _ = build_report_sql(REPORT_DEFINITIONS["country_distribution"], filters=None, limit=None)
        self.assertNotIn("SUM(", sql)
        self.assertNotIn("DISTINCT", sql)


if __name__ == "__main__":
    unittest.main()
