"""要點改「三層說明」，判讀限制整個移除（2026-08-04 使用者定案）。

## 為什麼改

16pt ＋ 行距 1.65 之後，側欄頁只放得下 5 條 × 19 字——**碎成 5 個短句反而難讀**。
使用者定案改為三段有層次的敘述，同樣的資訊用更少字講完（濃縮，不是少講）。

| 層 | 講什麼 | 判準 |
|---|---|---|
| 現況 | 圖上讀得到的數據事實 | 必須帶數字 |
| 意涵 | 這些數據代表什麼、為什麼 | 不重複數字，要說「所以呢」 |
| 後續 | 據此下一步該看什麼／查什麼 | 可執行，不是空話 |

🔴 **判讀限制整個拿掉**（使用者：「判讀限制不要出現了，作用不大」）——
連獨立灰框也不要，不是移到別處。

⚠ 順帶的好處：標籤成本從 5 字（「判讀限制｜」）降到 3 字（「現況｜」），
而且要點框不再需要為警語框讓出高度（`height_with_caveat_in` 3.3 → 一律 5.0）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SKILL = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt" / "scripts"
sys.path.insert(0, str(_SKILL))

import build_ppt as bp  # noqa: E402
from backend.app.worker import ai_narrative_runner as runner  # noqa: E402


class NoCaveatAnywhereTests(unittest.TestCase):
    def test_caveat_table_is_empty(self):
        """判讀限制的對照表要清空——留著就會被畫出來。"""
        self.assertEqual(bp.CAVEATS, {}, "CAVEATS 應已清空")

    def test_label_cost_drops_to_three(self):
        """最長標籤變成「現況／意涵／後續」（2 字）＋分隔符。"""
        self.assertEqual(bp.POINT_LABEL_COST, 3)

    def test_points_area_has_no_caveat_switch(self):
        """框高不再有「有沒有警語」兩種——參數本身要移除，不是傳 False。"""
        import inspect
        params = inspect.signature(bp._points_area).parameters
        self.assertNotIn("caveat", params, "_points_area 仍留著 caveat 參數")


class ThreeLayerBudgetTests(unittest.TestCase):
    def test_every_layout_gives_exactly_three_segments(self):
        theme = bp.Theme.load()
        size = theme.size("point_text_pt")
        for kind in ("chart_hero", "chart_wide", "table_with_points"):
            area = bp._points_area(theme, kind)
            self.assertIsNotNone(area, kind)
            width_in, height_in, columns = area
            per_line, max_lines = bp._text_capacity(
                theme, width_in=width_in, height_in=height_in, size_pt=size,
                line_ratio=bp.point_line_ratio(theme))
            limits = bp.points_budget(per_line, max_lines, columns)
            self.assertEqual(limits["max_points"], 3, f"{kind} 不是三段")

    def test_three_full_segments_still_fit(self):
        """⚠ 契約誠實性照舊：宣稱放得下三段，就必須真的放得下。"""
        theme = bp.Theme.load()
        size = theme.size("point_text_pt")
        for kind in ("chart_hero", "chart_wide", "table_with_points"):
            width_in, height_in, columns = bp._points_area(theme, kind)
            per_line, max_lines = bp._text_capacity(
                theme, width_in=width_in, height_in=height_in, size_pt=size,
                line_ratio=bp.point_line_ratio(theme))
            limits = bp.points_budget(per_line, max_lines, columns)
            blocks = [(label, "字" * limits["max_chars"], "ink", False)
                      for label in ("現況", "意涵", "後續")]
            bp.reset_dropped_points()
            kept = bp._trim_blocks(theme, blocks, width_in=width_in,
                                   height_in=height_in * columns, size_pt=size)
            self.assertEqual(len(kept), 3,
                             f"{kind} 宣稱 3 段 × {limits['max_chars']} 字，實放 {len(kept)} 段")


class ContractIsThreeLayerTests(unittest.TestCase):
    def test_points_count_is_free_form_two_to_three(self):
        """🔴 2026-08-07 契約更新（推翻 08-04 固定三段）：自由條列 2–3 條，
        不再強制湊滿第三條（「下一步建議有可執行內容才寫」）。上限仍 3
        ——版面容量沒變，變的是形式。"""
        self.assertEqual(runner.NARRATIVE_POINTS_MIN, 2)
        self.assertEqual(runner.NARRATIVE_POINTS_MAX, 3)

    def test_caveat_label_not_in_allowed_labels(self):
        """「判讀限制」不再是合法標籤。"""
        self.assertNotIn("判讀限制", runner.NARRATIVE_LAYER_LABELS)
        self.assertEqual(tuple(runner.NARRATIVE_LAYER_LABELS), ("現況", "意涵", "後續"))


if __name__ == "__main__":
    unittest.main()
