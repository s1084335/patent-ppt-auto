"""照容量上限寫的要點，組版時**一條都不能被丟掉**（2026-08-04 使用者定案）。

## 問題

第五輪實機（`report_trial_20260803_164251`）manifest 有 5 條 `points_dropped`
（p2／p6／p10／p15／p16），但同一輪 `ai:narrative` 的**契約警告是 0**
——CLI 完全照 `narrative_capacity()` 給的「N 條 × M 字」寫，組版還是丟。

## 根因：容量與排版是兩套算法

| 端 | 怎麼算 |
|---|---|
| `narrative_capacity()` | `max_chars = per_line × NARRATIVE_POINT_LINES`，只算**正文** |
| `_trim_blocks()` | `needs = ceil((len(label) + 1 + len(text)) / per_line)`，**含標籤** |

⚠ 標籤沒被扣掉：正文寫滿 `per_line × 2` 字、再加上「判讀限制｜」5 個字，
就變成 3 行而不是 2 行——條數一多，總行數必然溢出，尾端整條被丟。

而被丟的都是排在後面的「意涵」「後續」，正是價值最高的幾條。

## 判準

⚠ 這支測的是**契約的誠實性**：capacity 說放得下，就必須真的放得下。
不是測「有沒有丟」，是測「照著寫會不會被丟」。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SKILL = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt" / "scripts"
sys.path.insert(0, str(_SKILL))

import build_ppt as bp  # noqa: E402


class CapacityIsHonestTests(unittest.TestCase):
    """每個有要點區的版型：照上限寫滿 → _trim_blocks 不得丟任何一條。"""

    def _worst_case_blocks(self, limits: dict[str, int], *, caveat: bool):
        """最壞情況：條數寫到上限、每條字數寫到上限、標籤取最長的。"""
        label = bp.CAVEAT_LABEL  # 「判讀限制」＝最長的標籤
        text = "字" * limits["max_chars"]
        blocks = [(label if caveat and i == 0 else "意涵", text, "ink", False)
                  for i in range(limits["max_points"])]
        return blocks

    def test_every_layout_capacity_is_achievable(self):
        theme = bp.Theme.load()
        size = theme.size("point_text_pt")
        failures: list[str] = []
        for kind in ("chart_hero", "chart_wide", "table_with_points"):
            for caveat in (False, True):
                area = bp._points_area(theme, kind, caveat=caveat)
                if area is None:
                    continue
                width_in, height_in, columns = area
                # ⚠ 要點有設段落行距，估算必須用同一份（point_line_ratio）——
                # 不傳就會拿到全域估算值，測到的是測試自己的錯。
                per_line, max_lines = bp._text_capacity(
                    theme, width_in=width_in, height_in=height_in, size_pt=size,
                    line_ratio=bp.point_line_ratio(theme))
                if caveat:
                    max_lines -= bp._lines_needed(f"{bp.CAVEAT_LABEL}｜警語", per_line)
                # ⚠ 用**程式的公式**算，不自己重算一份——測試重算一份就變成
                # 第二個定義處，改了程式測試還是綠的。
                limits = bp.points_budget(per_line, max_lines, columns)
                blocks = self._worst_case_blocks(limits, caveat=caveat)
                bp.reset_dropped_points()
                # ⚠ 多欄版型的渲染端傳的是「單欄高 × 欄數」（見
                # _render_wide_points_band／_render_table_points_band），
                # 這裡要照同一個算法，否則測到的是測試自己的錯。
                kept = bp._trim_blocks(theme, blocks, width_in=width_in,
                                       height_in=height_in * columns, size_pt=size)
                if len(kept) != len(blocks):
                    failures.append(
                        f"{kind}(caveat={caveat}): 宣稱 {limits['max_points']} 條 × "
                        f"{limits['max_chars']} 字，實際只放得下 {len(kept)} 條"
                        f"（丟 {len(blocks) - len(kept)} 條）")
        self.assertEqual(failures, [], "容量契約不誠實：" + " ｜ ".join(failures))


if __name__ == "__main__":
    unittest.main()
