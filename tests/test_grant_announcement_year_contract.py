"""授權公告年欄位契約。

年度趨勢需要「申請年」對「授權公告年」交叉比較。授權公告年只能由
WIPS「授權公告日」衍生，不得混用未審查公開日或審查公告日。
"""
from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFRESH = PROJECT_ROOT / "backend" / "app" / "derived" / "refresh_report_patent_base.py"
MIGRATION = PROJECT_ROOT / "alembic" / "versions" / "0043_report_base_grant_announcement_year.py"


class GrantAnnouncementYearContractTests(unittest.TestCase):
    def test_refresh_derives_year_from_grant_announcement_date(self) -> None:
        src = REFRESH.read_text(encoding="utf-8")
        self.assertIn('"授權公告年"', src)
        self.assertIn('"授權公告日"', src)
        self.assertIn('grant_date_source."授權公告日"', src)
        self.assertNotIn('grant_date_source."未審查的公開日"', src)
        self.assertNotIn('grant_date_source."審查的公告日"', src)

    def test_migration_adds_chinese_column_to_report_base(self) -> None:
        src = MIGRATION.read_text(encoding="utf-8")
        self.assertIn('sa.Column("授權公告年"', src)
        self.assertIn("legacy_0021", src)
        self.assertIn("derived_layer.report_patent_base", src)
        self.assertIn("idx_report_patent_base_grant_announcement_year", src)

    def test_migration_recreates_dependent_views_safely(self) -> None:
        src = MIGRATION.read_text(encoding="utf-8")
        applicant_drop = src.index("DROP VIEW IF EXISTS derived_layer.report_patent_applicant_expanded")
        base_drop = src.index("DROP VIEW IF EXISTS derived_layer.report_patent_base")
        self.assertLess(applicant_drop, base_drop)
        self.assertIn("_APPLICANT_EXPANDED_VIEW_DOWNGRADE", src)
        self.assertIn('b."授權公告年"', src)


if __name__ == "__main__":
    unittest.main()
