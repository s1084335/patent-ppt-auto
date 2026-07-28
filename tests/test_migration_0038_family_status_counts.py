"""0038 契約測試：report_family_country 加狀態未知／審查中兩欄（2026-07-28）。

背景見 `test_family_unknown_status_included.py`——使用者定案「有同族 ID 的都要能納入
分析」，計算層已改為把 unknown／pending 記進第三／四槽並輸出到 FamilyCountryRow，
但 DB 表沒有對應欄位，寫入端會直接 raise（欄位不存在）。

本檔只驗 migration 的**契約**（不連 DB）：欄位名、型別、預設值、downgrade 對稱。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (PROJECT_ROOT / "alembic" / "versions"
             / "0038_family_status_counts.py")

NEW_COLUMNS = ("unknown_status_count", "pending_status_count")


class MigrationExistsTests(unittest.TestCase):
    def test_migration_file_exists(self):
        self.assertTrue(
            MIGRATION.exists(),
            "缺 0038 migration——計算層已輸出兩欄，DB 沒有欄位會寫入失敗")


class MigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not MIGRATION.exists():
            raise unittest.SkipTest("migration 尚未建立")
        cls.src = MIGRATION.read_text(encoding="utf-8")

    def test_revision_chain(self):
        """接在目前 head 0037 之後。"""
        self.assertRegex(self.src, r'revision\s*=\s*["\']0038_family_status_counts["\']')
        self.assertRegex(self.src, r'down_revision\s*=\s*["\']0037_exclusion_restore_topic["\']')

    def test_upgrade_adds_both_columns(self):
        up = self.src.split("def downgrade")[0]
        for col in NEW_COLUMNS:
            with self.subTest(col=col):
                self.assertIn(col, up, f"upgrade 沒加 {col}")

    def test_columns_have_zero_default(self):
        """既有列必須有值——NOT NULL 無預設會讓 migration 在有資料時失敗。"""
        up = self.src.split("def downgrade")[0]
        self.assertIn("server_default", up,
                      "沒給 server_default：既有 32 列會違反 NOT NULL")

    def test_downgrade_drops_both_columns(self):
        down = self.src.split("def downgrade")[1]
        for col in NEW_COLUMNS:
            with self.subTest(col=col):
                self.assertIn(col, down, f"downgrade 沒移除 {col}——升降級不對稱")

    def test_targets_physical_table(self):
        """0021 後 derived_layer.report_family_country 是 VIEW，實體表在 legacy_0021。

        對 VIEW 加欄會失敗；且 VIEW 需一併重建才看得到新欄。
        """
        self.assertIn("legacy_0021", self.src,
                      "要改的是實體表 legacy_0021.report_family_country，不是 derived VIEW")

    def test_view_recreated(self):
        """實體表加欄後，derived VIEW 必須重建，否則報表端讀不到新欄。"""
        self.assertIn("derived_layer.report_family_country", self.src)
        self.assertRegex(self.src, r"(?i)create\s+or\s+replace\s+view|drop\s+view")


if __name__ == "__main__":
    unittest.main()
