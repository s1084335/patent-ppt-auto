"""家族數落點收斂（tasks §3）。

## 問題

`application_trend`／`publication_trend` 的資料列同時帶 `patent_count` 與
`family_count`。**同一頁上兩個數字都叫「數量」**，讀者無從判斷該看哪一個，
而且兩者的口徑不同（件數 vs 同族合併後的家族數）。

§2 已把家族數收斂到**封面**由引擎統一供給（`cover_stats.family_count`），
趨勢表再列一次就是同一份知識的第二個顯示落點——⚠ 兩處數字若因口徑差異而不同
（實測受理局頁 46 vs 封面 48 vs 家族報表 40），讀者只會覺得報表在自相矛盾。

## 收斂＝隱藏顯示，不是刪資料

⚠ `chart_rows` 必須**保留** `family_count`：CLI 取證要用，而且刪掉之後
「同族合併後的數字」就再也算不回來。這裡用既有的
`DATA_TABLE_EXCLUDED_COLUMNS` 機制——資料在、只是不佔版面。

## ⚠ 不動 KP 表與 KP 象限

那兩處的家族數是 **per-applicant 維度**（某申請人有幾個家族），與趨勢表的
「某年有幾個家族」不是同一件事，收斂它會刪掉真正的資訊。
"""
from __future__ import annotations

import unittest

from backend.app.reports.chart_runner import DATA_TABLE_EXCLUDED_COLUMNS
from backend.app.reports.report_definitions import REPORT_DEFINITIONS

#: ⚠ 實查修正（2026-08-19）：規格 §3.1 寫「`annual_trend`／`publication_trend`」，
#: 但實際上**只有 `application_trend` 產出 `family_count`**（`publication_trend`
#: 的 `aggregates` 是空的）。對一個不存在的欄位登記排除，那條規則永遠不會生效
#: 而且不會有任何東西報錯——比沒登記更糟，因為它看起來像已經處理了。
FAMILY_COUNT_REPORTS = ("application_trend",)


class NoStaleExclusionTests(unittest.TestCase):
    """⚠ 登記一個不存在的欄位＝那條規則永遠不生效，且看起來像已處理。"""

    def test_publication_trend_has_no_family_count_to_hide(self):
        self.assertEqual(
            [a[2] for a in REPORT_DEFINITIONS["publication_trend"].aggregates], [],
            "publication_trend 開始產 family_count 了——若要顯示收斂，"
            "要把它加進 FAMILY_COUNT_REPORTS，不是預先登記排除")


class TrendTablesHideFamilyCountTests(unittest.TestCase):
    def test_trend_reports_exclude_family_count_from_display(self):
        for name in FAMILY_COUNT_REPORTS:
            with self.subTest(report=name):
                excluded = DATA_TABLE_EXCLUDED_COLUMNS.get(name, ())
                self.assertIn(
                    "family_count", excluded,
                    f"{name} 的數據表仍顯示 family_count——"
                    "與封面的家族數是同一份知識的第二個顯示落點")

    def test_data_is_still_produced(self):
        """🔴 §3.2：隱藏顯示，**不刪資料**。

        ⚠ 刪掉之後「同族合併後的數字」再也算不回來，而 CLI 取證要用它。
        """
        for name in FAMILY_COUNT_REPORTS:
            with self.subTest(report=name):
                definition = REPORT_DEFINITIONS[name]
                agg_names = [a[2] for a in definition.aggregates]
                self.assertIn(
                    "family_count", agg_names,
                    f"{name} 不再產出 family_count——收斂的是顯示不是資料")


class KpDimensionsUntouchedTests(unittest.TestCase):
    """⚠ §3.3：KP 的家族數是 per-applicant 維度，收斂它會刪掉真資訊。"""

    def test_kp_reports_keep_family_count_visible(self):
        for name in ("applicant_strength_profile", "applicant_ranking"):
            if name not in REPORT_DEFINITIONS:
                continue
            with self.subTest(report=name):
                excluded = DATA_TABLE_EXCLUDED_COLUMNS.get(name, ())
                self.assertNotIn(
                    "family_count", excluded,
                    f"{name} 的家族數被連坐隱藏了——那是 per-applicant 維度，"
                    "與趨勢表的「某年幾個家族」不是同一件事")


class ExclusionIsScopedTests(unittest.TestCase):
    """⚠ 排除是**逐報表**的，不得變成全域規則。"""

    def test_exclusion_map_is_per_report(self):
        self.assertIsInstance(DATA_TABLE_EXCLUDED_COLUMNS, dict)
        for name, cols in DATA_TABLE_EXCLUDED_COLUMNS.items():
            with self.subTest(report=name):
                self.assertIn(
                    name, REPORT_DEFINITIONS,
                    f"排除表登記了不存在的報表「{name}」——那條規則永遠不會生效，"
                    "而且不會有任何東西報錯")


if __name__ == "__main__":
    unittest.main()
