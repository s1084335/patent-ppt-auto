"""0039 契約測試：report_patent_base 加 abstract 欄（2026-07-28）。

文獻備註第三級來源。實跑 refresh 時 raise：
    UndefinedColumn: column "abstract" of relation "report_patent_base" does not exist

core_layer.patents 早有 abstract（60/60，外觀設計那 11 筆最長 530 字），
但 derived 寬表沒有此欄。與同日 0038 一樣：實體表在 legacy_0021、derived 是 VIEW，
兩者都要處理，否則報表端讀不到。
"""
from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = PROJECT_ROOT / "alembic" / "versions" / "0039_report_base_abstract.py"


class MigrationExistsTests(unittest.TestCase):
    def test_migration_file_exists(self):
        self.assertTrue(MIGRATION.exists(), "缺 0039 migration——refresh 會 UndefinedColumn")


class MigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not MIGRATION.exists():
            raise unittest.SkipTest("migration 尚未建立")
        cls.src = MIGRATION.read_text(encoding="utf-8")

    def test_revision_chain(self):
        self.assertRegex(self.src, r'revision\s*=\s*["\']0039_report_base_abstract["\']')
        self.assertRegex(self.src, r'down_revision\s*=\s*["\']0038_family_status_counts["\']')

    def test_targets_physical_table(self):
        """derived_layer.report_patent_base 是 VIEW，實體表在 legacy_0021。"""
        self.assertIn("legacy_0021", self.src)

    def test_upgrade_adds_abstract(self):
        self.assertIn("abstract", self.src.split("def downgrade")[0])

    def test_downgrade_drops_abstract(self):
        self.assertIn("abstract", self.src.split("def downgrade")[1])

    def test_view_recreated(self):
        """加欄後 VIEW 必須重建，否則報表端與備註 runner 都讀不到。"""
        self.assertIn("derived_layer.report_patent_base", self.src)
        self.assertRegex(self.src, r"(?i)create\s+or\s+replace\s+view|create\s+view")


if __name__ == "__main__":
    unittest.main()
