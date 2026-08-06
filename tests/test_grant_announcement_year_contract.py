"""授權公告年欄位契約。

年度趨勢需要「申請年」對「授權公告年」交叉比較。授權公告年只能由
WIPS「授權公告日」衍生，不得混用未審查公開日或審查公告日。
"""
from __future__ import annotations

import unittest
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFRESH = PROJECT_ROOT / "backend" / "app" / "derived" / "refresh_report_patent_base.py"
MIGRATION = PROJECT_ROOT / "alembic" / "versions" / "0043_report_base_grant_announcement_year.py"


class GrantAnnouncementYearContractTests(unittest.TestCase):
    def test_migration_revision_fits_alembic_version_column(self) -> None:
        """Alembic revision 必須符合既有 version_num varchar(32) 限制。"""
        src = MIGRATION.read_text(encoding="utf-8")
        match = re.search(r'^revision\s*=\s*"([^"]+)"', src, flags=re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertLessEqual(len(match.group(1)), 32)

    def test_refresh_derives_year_from_grant_announcement_date(self) -> None:
        """授權公告年只由「授權公告日」衍生，不得混用公開日或審查公告日。

        ⚠ 2026-08-06（migration 0046）**契約的取值路徑變了，規則本身沒變**：
        原本「授權公告日」在 `patent_attributes`（一 raw_record 一列），refresh 得靠
        `grant_date_source` 這個 LATERAL 子查詢挑出最新非空的那一列；欄位搬進
        `core_layer.patents` 後一專利一列，**沒有列要選**，該子查詢連同別名一併刪除。
        故斷言由 `grant_date_source."授權公告日"` 改為 `p."授權公告日"`。
        要守的東西不變——底下兩條 assertNotIn 仍擋住混用其他日期欄。
        """
        src = REFRESH.read_text(encoding="utf-8")
        self.assertIn('"授權公告年"', src)
        self.assertIn('p."授權公告日"', src)
        self.assertNotIn('"未審查的公開日"', src)
        self.assertNotIn('"審查的公告日"', src)

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
