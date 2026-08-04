"""顯示用專利號的單一定義處（2026-08-04 治本：TW 扣 1911 機制在報表端沒發揮）。

病根：六層 COALESCE 顯示鏈（授權公告號→審查的公告號→公開號(轉換後)→公開號→
申請號(轉換後)→申請號）被複製了四份（patent_queries／workspace_queries／
clustering.runner／clustering.workspace_service），第五個消費端
cluster_data_loader 漏抄——只取原值公開號，TW 案顯示西元前綴、
M 開頭授權案直接空白。

治本＝鏈只在 `transforms/patent_numbers.py` 定義一次，其他人 import；
本檔是防再分岔的一致性鎖。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.transforms.patent_numbers import (
    DISPLAY_NUMBER_PRIORITY,
    display_number_sql,
)

BACKEND = Path(__file__).resolve().parents[1] / "backend" / "app"


class DisplayChainDefinitionTests(unittest.TestCase):
    def test_priority_order_locked(self):
        """順序是定案：公告號先於公開號、轉換後先於原值。"""
        self.assertEqual(DISPLAY_NUMBER_PRIORITY, (
            "授權公告號", "審查的公告號",
            "未審查的公開號(轉換後)", "未審查的公開號",
            "申請號(轉換後)", "申請號",
        ))

    def test_sql_prefers_transformed_before_raw(self):
        sql = display_number_sql("b")
        self.assertIn('b."未審查的公開號(轉換後)"', sql)
        self.assertLess(sql.index("未審查的公開號(轉換後)"), sql.index('"未審查的公開號"'),
                        "轉換後必須排在原值前——反了 TW 案又會顯示西元前綴")
        self.assertTrue(sql.strip().startswith("COALESCE("))


class ConsumersUseSingleSourceTests(unittest.TestCase):
    """任何 .py 不得自帶 NULLIF 顯示鏈——鏈只能活在 transforms。"""

    CHAIN_MARK = 'NULLIF(BTRIM(p."未審查的公開號(轉換後)")'

    def test_no_verbatim_chain_outside_transforms(self):
        offenders = []
        for path in BACKEND.rglob("*.py"):
            if path.name == "patent_numbers.py":
                continue
            if self.CHAIN_MARK in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(BACKEND)))
        self.assertEqual(offenders, [],
                         f"這些檔案自帶顯示鏈副本，改用 display_number_sql：{offenders}")

    def test_known_consumers_import_helper(self):
        for rel in ("app_layer/patent_queries.py", "app_layer/workspace_queries.py",
                    "clustering/runner.py", "clustering/workspace_service.py",
                    "reports/cluster_data_loader.py"):
            src = (BACKEND / rel).read_text(encoding="utf-8")
            self.assertIn("display_number_sql", src, f"{rel} 沒用單一定義處")

    def test_cluster_data_loader_dropped_raw_only_column(self):
        """🔴 regression：代表專利不得再單取原值公開號（TW 顯示西元、授權案空白）。"""
        src = (BACKEND / "reports/cluster_data_loader.py").read_text(encoding="utf-8")
        self.assertNotIn('b."未審查的公開號" AS patent_number', src)


class DerivedListsStayAlignedTests(unittest.TestCase):
    def test_comparison_columns_derived_from_priority(self):
        """比對目標的欄位清單必須是同一條 priority 的子序列（語意＝只認轉換後與公告號）。"""
        from backend.app.comparison.target_source import PATENT_NUMBER_COLUMNS

        it = iter(DISPLAY_NUMBER_PRIORITY)
        self.assertTrue(all(col in it for col in PATENT_NUMBER_COLUMNS),
                        "PATENT_NUMBER_COLUMNS 與 DISPLAY_NUMBER_PRIORITY 順序分岔")

    def test_clustering_model_priority_is_subsequence(self):
        from backend.app.clustering.model import PATENT_NUMBER_PRIORITY

        cols = [c for _k, c in PATENT_NUMBER_PRIORITY]
        it = iter(DISPLAY_NUMBER_PRIORITY)
        self.assertTrue(all(col in it for col in cols),
                        "clustering 端 priority 與顯示鏈順序分岔")


class FieldLevelDisplayTests(unittest.TestCase):
    def test_detail_fields_prefer_transformed(self):
        """瀏覽詳情的申請號／公開號欄也要轉換後優先（tracker 定案：申請號扣 1911）。"""
        src = (BACKEND / "app_layer/patent_queries.py").read_text(encoding="utf-8")
        self.assertIn('"申請號(轉換後)"', src.split('"application_number"')[1][:200])
        self.assertIn('"未審查的公開號(轉換後)"', src.split('"publication_number"')[1][:200])


if __name__ == "__main__":
    unittest.main()
