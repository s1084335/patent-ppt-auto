"""趨勢折線圖要在資料點上帶家族數（2026-08-17 使用者定案）。

## 為什麼

`family_count`（08-05 定案的「真爆發 vs 同族延伸判別燃料」）只活在表格裡，
圖上完全讀不到——`_build_trend_section` 的註解自己寫著「圖不改（仍是件數雙線），
四欄只進數據表」。那正是審閱意見第 6 點抓的「表格有一個維度、圖上讀不到」。

實測數字說明它的判讀力：2021 (36 件/28 族)、2022 (61/47)、2023 (60/52)
——**件數與家族數的差距逐年縮小＝近年更接近真爆發而非同族延伸**。

## 做法

不畫第三條線（三條線的圖會擠），改在**申請點旁標註家族數**——
一眼可比「這一點是幾件、其中幾族」。

⚠ 只標**有家族數且與件數不同**的年份：相同時（1 件 1 族）標了是雜訊。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner


class TrendFamilyLabelTests(unittest.TestCase):
    APP = [
        {"application_year": 2021, "patent_count": 36, "family_count": 28},
        {"application_year": 2022, "patent_count": 61, "family_count": 47},
        {"application_year": 2023, "patent_count": 1, "family_count": 1},   # 相同→不標
        {"application_year": 2024, "patent_count": 34},                      # 無家族數→不標
    ]
    PUB = [{"授權公告年": 2022, "patent_count": 30}]

    def _render(self) -> str:
        out = Path(tempfile.mkdtemp()) / "annual_trend.svg"
        chart_runner.render_line_chart(out, "趨勢", self.APP, self.PUB)
        return out.read_text(encoding="utf-8")

    def test_family_labels_rendered(self):
        """🔴 核心：家族數要出現在圖上。"""
        svg = self._render()
        self.assertIn("28 族", svg, "2021 的家族數沒標在圖上")
        self.assertIn("47 族", svg, "2022 的家族數沒標在圖上")

    def test_same_count_not_labelled(self):
        """件數＝家族數時不標——那是雜訊不是資訊。"""
        svg = self._render()
        self.assertNotIn("1 族", svg)

    def test_missing_family_not_labelled(self):
        """沒有家族數的年份不得標成 0 族（缺鍵≠0，混同會誤導）。"""
        svg = self._render()
        self.assertNotIn("0 族", svg)

    def test_chart_still_valid_svg(self):
        import xml.etree.ElementTree as ET

        ET.fromstring(self._render())   # 解析不過就是壞的 SVG


if __name__ == "__main__":
    unittest.main()
