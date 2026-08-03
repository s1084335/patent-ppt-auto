"""年度矩陣的年份標籤不得互相黏住（2026-08-03 使用者實機截圖）。

## 症狀

橫軸印成 `201120122013201420152016…` 一整串，數字之間沒有空隙，看不出斷點。

## 根因

補齊連續年度後欄距 43px → 38px，但年份標籤仍印四位數、字級仍是
`CHART_LABEL_PX`（18px）。四位數字寬 ≈ 4 × 18 × 0.62 = **44.6px > 38px**
——⚠ 欄距與標籤寬是同一件事的兩個落點，改了欄數卻沒改標籤，於是靜默黏住。

## 修法

年份改印兩位數（`'11`／`'12`），寬度 ≈ 28px，38px 欄距放得下。
世紀寫在軸說明裡，不是每一欄重複四個字。
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner as cr


class YearLabelSpacingTests(unittest.TestCase):
    def _svg(self, years: int) -> str:
        rows = [{"applicant_display_name": f"公司{i:02d}",
                 "application_year": 2011 + y, "patent_count": i + 1}
                for i in range(6) for y in range(years)]
        layout = cr.year_bubble_matrix_layout(rows, "applicant_display_name")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.svg"
            cr.render_year_bubble_matrix_chart(path, "申請人年度專利分布矩陣",
                                               layout, layout["top_rows"])
            return path.read_text(encoding="utf-8")

    @staticmethod
    def _axis_labels(svg: str) -> list[tuple[float, str, float]]:
        """回傳（中心 x, 文字, 字級）——只取橫軸那排（置中對齊、內容是年份）。"""
        out = []
        for m in re.finditer(
                r'<text x="([\d.]+)" y="[\d.]+" font-size="([\d.]+)" '
                r'text-anchor="middle"[^>]*>([^<]+)</text>', svg):
            text = m.group(3)
            if re.fullmatch(r"'?\d{2,4}", text):
                out.append((float(m.group(1)), text, float(m.group(2))))
        return sorted(out)

    def test_labels_do_not_touch_at_full_window(self):
        """16 欄（最窄）時相鄰標籤之間仍要留得下空隙。"""
        labels = self._axis_labels(self._svg(cr.CHART_YEAR_WINDOW))
        self.assertEqual(len(labels), cr.CHART_YEAR_WINDOW, f"抓到 {len(labels)} 個標籤")
        bad = []
        for (x1, t1, s1), (x2, t2, s2) in zip(labels, labels[1:]):
            half1 = cr._display_width(t1) * s1 / 2
            half2 = cr._display_width(t2) * s2 / 2
            gap = (x2 - x1) - half1 - half2
            if gap < cr.LABEL_MIN_GAP_PX:
                bad.append(f"{t1}|{t2} 間距 {gap:.1f}px")
        self.assertEqual(bad, [], f"年份標籤黏住：{bad[:5]}")

    def test_label_still_identifies_the_year(self):
        """⚠ 縮短不能縮到看不出是哪一年——尾兩碼必須對得上實際年份。"""
        labels = self._axis_labels(self._svg(6))
        self.assertTrue(labels, "沒抓到年份標籤")
        first = labels[0][1]
        self.assertTrue(first.endswith("11"), f"第一欄應為 2011，實得 {first}")


if __name__ == "__main__":
    unittest.main()
