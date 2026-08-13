from __future__ import annotations

import unittest
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / (
    "0050_company_group_normalization.py"
)


class CompanyGroupMigrationContractTests(unittest.TestCase):
    """確認集團層正規化 migration 的 schema、狀態與讀取 view 契約。"""

    @classmethod
    def setUpClass(cls):
        assert MIGRATION.exists(), f"缺少 migration 檔案：{MIGRATION.name}"
        cls.src = MIGRATION.read_text(encoding="utf-8")
        split = cls.src.index("def downgrade")
        cls.up = cls.src[:split]
        cls.down = cls.src[split:]

    def test_chain_links_to_current_head(self):
        self.assertRegex(
            self.src,
            r'down_revision\s*=\s*["\']0049_sse_event_metadata["\']',
        )

    def test_group_tables_and_review_states_exist(self):
        for table in ("company_groups", "company_group_members"):
            self.assertIn(table, self.up)
        for status in ("suggested", "confirmed", "rejected"):
            self.assertIn(status, self.up)
        for source_type in ("manual", "cli_ai"):
            self.assertIn(source_type, self.up)

    def test_confirmed_member_uniqueness_and_lookup_indexes_exist(self):
        self.assertIn("uq_company_group_members_confirmed_company", self.up)
        self.assertIn("idx_company_groups_review_status", self.up)
        self.assertIn("idx_company_group_members_group_id", self.up)
        self.assertIn("idx_company_group_members_review_status", self.up)
        unique_index_start = self.up.index("uq_company_group_members_confirmed_company")
        unique_index_end = self.up.index("WHERE review_status = 'confirmed'", unique_index_start)
        unique_index_sql = self.up[unique_index_start:unique_index_end]
        self.assertIn("company_display_name", unique_index_sql)
        self.assertNotIn("company_code", unique_index_sql)

    def test_report_group_views_are_created_with_fallback_names(self):
        for view_name in (
            "derived_layer.report_patent_base_with_groups",
            "derived_layer.report_patent_applicant_expanded_with_groups",
        ):
            self.assertIn(view_name, self.up)
        for column in (
            "applicant_group_display_name",
            "current_assignee_group_display_name",
            "recent_assignee_group_display_name",
        ):
            self.assertIn(column, self.up)
        self.assertIn("COALESCE", self.up)
        self.assertIn("review_status = 'confirmed'", self.up)

    def test_downgrade_drops_views_before_tables(self):
        first_view_drop = self.down.index("report_patent_base_with_groups")
        first_table_drop = self.down.index("company_group_members")
        self.assertLess(first_view_drop, first_table_drop)


if __name__ == "__main__":
    unittest.main()
