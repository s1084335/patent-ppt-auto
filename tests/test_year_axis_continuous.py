"""年度矩陣的橫軸必須是**連續年度**（2026-08-03 使用者實機指出）。

## 怎麼發現的

使用者看到申請人年度矩陣橫軸是 `2011 2013 2015 2016 ...`，問
「你把我的 2012、2014 犧牲掉幹嘛？」

查證：2012 與 2014 **全庫本來就沒有任何專利**（連年度趨勢圖都是 None），
不是被 `CHART_YEAR_WINDOW` 砍掉的——14 個年份 < 15，那行根本沒生效。

## 但這是真的問題

橫軸取的是 `sorted(有資料的年份)`，**空年直接消失**：

- 讀者會以為 2011 的下一年就是 2013 —— **時間軸的間距是騙人的**
- 「連續三年都有布局」與「隔年才有一次」在圖上長得一樣
- ⚠ 程式註解原本寫「圖上仍是連續區間」，**那句話是錯的**

對趨勢判讀來說這很要緊，所以補齊連續年度、空年留白欄。

⚠ `CHART_YEAR_WINDOW` 一併由 15 改 **16**：15 是我拍的、不是量出來的；
實測 949px 畫布下 16 欄每欄 57px，泡泡與數字都放得下。
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner as cr


class ContinuousYearAxisTests(unittest.TestCase):
    def _rows(self, years):
        return [{"applicant_display_name": "A公司", "application_year": y, "patent_count": 1}
                for y in years]

    def _render(self, years):
        layout = cr.year_bubble_matrix_layout(self._rows(years), "applicant_display_name")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.svg"
            cr.render_year_bubble_matrix_chart(
                path, "申請人年度專利分布矩陣", layout, layout["top_rows"])
            return path.read_text(encoding="utf-8")

    @staticmethod
    def _axis_years(svg: str) -> list[int]:
        return [int(y) for y in re.findall(r'text-anchor="middle"[^>]*>(\d{4})</text>', svg)]

    def test_gap_years_are_filled(self):
        """資料缺 2012／2014 時，軸上仍要有那兩欄（留白）。"""
        axis = self._axis_years(self._render([2011, 2013, 2015, 2016]))
        self.assertEqual(axis, [2011, 2012, 2013, 2014, 2015, 2016],
                         f"軸不連續：{axis}——讀者會誤讀時間間距")

    def test_window_is_16(self):
        self.assertEqual(cr.CHART_YEAR_WINDOW, 16)

    def test_window_still_caps_long_span(self):
        """⚠ 補齊之後年份可能變多，窗口上限仍要生效（否則畫布會被撐爆）。"""
        axis = self._axis_years(self._render([1995, 2026]))
        self.assertEqual(len(axis), cr.CHART_YEAR_WINDOW,
                         f"補齊後沒有套用窗口上限：{len(axis)} 欄")
        self.assertEqual(axis[-1], 2026, "砍的應該是最舊的年份")

    def test_single_year_still_works(self):
        axis = self._axis_years(self._render([2020]))
        self.assertEqual(axis, [2020])


if __name__ == "__main__":
    unittest.main()
