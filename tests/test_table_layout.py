"""表格通用版型（tasks §7c）：參數化欄數與欄寬。

## 為什麼是「抽出來」不是「新做一個」

表格繪製**早就會了**——`slide_conclusions` 的四欄表、`CONCL_COLS` 欄寬都調好了，
只是**綁死在那一頁**。使用者 2026-08-18 指出「表格要加入版型庫，要讓 CLI 知道能用」。

2026-08-18 使用者裁決：**參數化欄數與欄寬**（不是沿用固定四欄）——
逐家時序表、主題×年矩陣這類需求五欄以上，固定四欄等於馬上要再改一次版型。

## 欄寬紀律

沿用 `CONCL_COLS` 的內距規則：欄寬總和必須 ≤ `CW - 2×0.26`。
⚠ 超寬不會報錯，只會**在轉圖後才發現被切掉**——那時已經產完一份簡報。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "html-report-to-deck"
sys.path.insert(0, str(SKILL / "scripts"))


class ColumnWidthTests(unittest.TestCase):
    def test_helper_exists(self):
        import deck_layout

        self.assertTrue(
            hasattr(deck_layout, "table_col_widths"),
            "沒有欄寬計算函式——每張表各自寫死欄寬就是第二份、第三份定義")

    def test_widths_fit_the_usable_width(self):
        """🔴 總和不得超過可用寬——超寬只會在轉圖後才發現。"""
        import deck_layout

        usable = deck_layout.CW - 2 * 0.26
        for n in range(2, 9):
            with self.subTest(cols=n):
                widths = deck_layout.table_col_widths(n)
                self.assertEqual(len(widths), n)
                self.assertLessEqual(
                    round(sum(widths), 6), round(usable, 6),
                    f"{n} 欄的欄寬總和 {sum(widths):.3f} 超過可用寬 {usable:.3f}")

    def test_explicit_weights_are_honoured(self):
        """呼叫端可指定相對權重（例如首欄窄、內容欄寬）。"""
        import deck_layout

        widths = deck_layout.table_col_widths(3, weights=(1, 3, 1))
        self.assertAlmostEqual(widths[1] / widths[0], 3.0, places=3)

    def test_rejects_impossible_column_counts(self):
        """欄數過多會讓每欄窄到放不下字——當場炸，不要默默畫出無法閱讀的表。"""
        import deck_layout

        with self.assertRaises(ValueError):
            deck_layout.table_col_widths(0)
        with self.assertRaises(ValueError):
            deck_layout.table_col_widths(99)


class ConclusionsReusesTheGenericTableTests(unittest.TestCase):
    """§7c.3：不留第二份畫法。"""

    def test_conclusions_uses_the_shared_width_helper(self):
        import inspect

        import deck_layout

        src = inspect.getsource(deck_layout.slide_conclusions)
        self.assertIn(
            "table_col_widths", src,
            "結論頁還在用自己那份寫死的 CONCL_COLS——"
            "兩份欄寬定義會各自演進，而且不會報錯")


class RegistryAndSyncTests(unittest.TestCase):
    """table 要進版型庫，且三處同步（§7a 的閘門會擋，這裡明寫期待）。"""

    def test_table_is_in_the_registry(self):
        import deck_layout

        self.assertIn(
            "table", deck_layout.LAYOUTS,
            "table 沒進版型庫——CLI 不知道能用，結果就是永遠不用")

    def test_table_has_a_renderer(self):
        import deck_layout

        self.assertTrue(
            hasattr(deck_layout, "slide_table"),
            "宣告了 table 版型卻沒有畫法")


if __name__ == "__main__":
    unittest.main()
