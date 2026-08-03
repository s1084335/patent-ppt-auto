"""年度矩陣的泡泡不得撞到相鄰欄（2026-08-03 補齊連續年度後的迴歸）。

## 怎麼發現的

橫軸補成連續年度後欄數由 14 增為 16，欄距 43px → 38px，
但泡泡半徑上限**寫死 9+19=28**、不隨欄寬調整：

| 版本 | 欄數 | 欄距 | 半徑 | 相鄰重疊 |
|---|---|---|---|---|
| 改前 | 14 | 43px | 9–28 | 1 處，最深 0.5px |
| 改後 | 16 | 38px | 9–28 | **4 處，最深 5.5px** |

⚠ 半徑上限與欄寬是**同一件事的兩個落點**——改了欄數就必須同步改半徑，
而它寫死在公式裡，於是靜默撞上。上限必須由 `cell_w` **推導**，不是各寫各的。
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner as cr


def _overlaps(svg: str) -> tuple[int, float]:
    """回傳（相鄰泡泡重疊處數, 最深重疊 px）。"""
    circles = [(float(x), float(y), float(r)) for x, y, r in
               re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)"', svg)]
    rows: dict[int, list[tuple[float, float]]] = {}
    for x, y, r in circles:
        rows.setdefault(round(y), []).append((x, r))
    count = 0
    worst = 0.0
    for items in rows.values():
        items.sort()
        for (x1, r1), (x2, r2) in zip(items, items[1:]):
            gap = (r1 + r2) - (x2 - x1)
            if gap > 0:
                count += 1
                worst = max(worst, gap)
    return count, worst


class BubbleFitsColumnTests(unittest.TestCase):
    def _render(self, *, years: int, rows_n: int = 8) -> str:
        """做一份「每格都有值、且值差距大」的資料——最容易撞的情形。"""
        rows = [{"applicant_display_name": f"公司{i:02d}",
                 "application_year": 2011 + y,
                 "patent_count": (1 if (i + y) % 3 else 30)}
                for i in range(rows_n) for y in range(years)]
        layout = cr.year_bubble_matrix_layout(rows, "applicant_display_name")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.svg"
            cr.render_year_bubble_matrix_chart(path, "申請人年度專利分布矩陣",
                                               layout, layout["top_rows"])
            return path.read_text(encoding="utf-8")

    def test_no_overlap_at_full_window(self):
        """16 欄（窗口上限）時仍不得重疊——這是最窄的情形。"""
        count, worst = _overlaps(self._render(years=cr.CHART_YEAR_WINDOW))
        self.assertEqual(count, 0, f"{count} 處相鄰泡泡重疊，最深 {worst:.1f}px")

    def test_no_overlap_when_few_years(self):
        """欄寬寬鬆時也不能因為放大而撞上。"""
        count, worst = _overlaps(self._render(years=5))
        self.assertEqual(count, 0, f"{count} 處相鄰泡泡重疊，最深 {worst:.1f}px")

    def test_largest_bubble_still_holds_two_digits(self):
        """⚠ 縮半徑不能縮到裝不下格內數字（18px 字、兩位數約需半徑 14）。"""
        svg = self._render(years=cr.CHART_YEAR_WINDOW)
        radii = [float(r) for r in re.findall(r'<circle[^>]*r="([\d.]+)"', svg)]
        self.assertGreaterEqual(max(radii), 14,
                                f"最大泡泡只剩 {max(radii):.1f}px，格內數字會滿出來")


if __name__ == "__main__":
    unittest.main()
