"""年度矩陣泡泡的**列向**互撞守門（2026-08-12 使用者實機指出）。

## 症狀與根因（08-03 泡泡互撞的殘留半邊）

實測 `report_trial_20260812_114401`：列距 39px、最大半徑 28px → 相鄰列同年份
的大泡泡（曾晴×帝瑪斯 2020/2022/2024 等）4 對互撞。
08-03 修互撞時把半徑上限綁了**欄寬**（`(cell_w-GAP)/2`），註解還寫著
「半徑與欄寬是同一件事的兩個落點」——卻漏了**列距**這第三個落點：
列多時 `row_h` 縮到 39px，28px 半徑照畫，縱向直接疊。

## 契約

半徑上限＝min(28, 欄向可用半徑, **列向可用半徑**)；
下限 BUBBLE_MIN_RADIUS_PX 仍優先（數字可讀性 > 相切，08-03 既有取捨——
row_h 壓到 26px 地板時允許輕微相切，但那只在列數被砍的極端情況）。
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner


def _overlapping_pairs(svg: str) -> list[tuple]:
    circles = [(float(m.group(1)), float(m.group(2)), float(m.group(3)))
               for m in re.finditer(
                   r'<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)"', svg)]
    data = [c for c in circles if c[2] > 9.5]   # 排除圖例小圓（r=9）
    hits = []
    for i, (x1, y1, r1) in enumerate(data):
        for x2, y2, r2 in data[i + 1:]:
            if (x1 - x2) ** 2 + (y1 - y2) ** 2 < (r1 + r2) ** 2 - 0.01:
                hits.append((x1, y1, r1, x2, y2, r2))
    return hits


class RowDirectionOverlapTests(unittest.TestCase):
    def _render(self, n_rows: int) -> str:
        # 相鄰公司同年份全是最大值——列向最嚴苛的情況。
        rows = [{"applicant_display_name": f"公司{i:02d}",
                 "application_year": year, "patent_count": 5}
                for i in range(n_rows) for year in (2020, 2022, 2024)]
        layout = chart_runner.year_bubble_matrix_layout(
            rows, "applicant_display_name", row_limit=20)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.svg"
            chart_runner.render_year_bubble_matrix_chart(
                p, "測試矩陣", layout, layout["top_rows"][:n_rows])
            return p.read_text(encoding="utf-8")

    def test_ten_rows_no_vertical_overlap(self):
        """10 列（滑雪機實況）：相鄰列大泡泡不得互撞。"""
        hits = _overlapping_pairs(self._render(10))
        self.assertEqual(hits, [], f"泡泡互撞 {len(hits)} 對：{hits[:3]}")

    def test_few_rows_keep_full_radius(self):
        """列少（列距寬裕）時半徑不得被順手縮小——守門只在需要時生效。"""
        svg = self._render(3)
        radii = [float(m.group(1)) for m in re.finditer(r'r="([\d.]+)"', svg)]
        self.assertGreaterEqual(max(radii), 27.9, "列距寬裕時最大泡泡應維持 28")

    def test_radius_never_below_floor(self):
        """下限不破：數字可讀性優先（08-03 既有取捨）。"""
        svg = self._render(10)
        data_radii = [float(m.group(3)) for m in re.finditer(
            r'<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)"', svg)
            if float(m.group(3)) > 9.5]
        self.assertGreaterEqual(min(data_radii), chart_runner.BUBBLE_MIN_RADIUS_PX - 0.01)


if __name__ == "__main__":
    unittest.main()
