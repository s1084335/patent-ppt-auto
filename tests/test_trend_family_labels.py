"""趨勢折線圖**不放**家族數（2026-08-17 晚，使用者實機看過後定案反轉）。

## 沿革（同一天內兩次定案，兩次都是使用者實機決定）

1. 早上：`family_count` 只活在表格、圖上讀不到，於是在申請點旁標註家族數。
2. 晚上：實機看過後「申請與公告趨勢的圖，家族數先拿掉」——兩條線再加點上
   數字，資訊密度過高。

⚠ 這支測試沒有刪，而是**反轉判準**並保留沿革：直接刪掉會讓下一個人（包含我）
看不出「圖上沒有家族數」是定案還是漏做，很可能又加回去。08-05 的「真爆發 vs
同族延伸」判別需求沒有消失，只是不由這張圖承擔。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner


class TrendChartHasNoFamilyLabelTests(unittest.TestCase):
    APP = [
        {"application_year": 2021, "patent_count": 36, "family_count": 28},
        {"application_year": 2022, "patent_count": 61, "family_count": 47},
        {"application_year": 2023, "patent_count": 1, "family_count": 1},
        {"application_year": 2024, "patent_count": 34},
    ]
    PUB = [{"授權公告年": 2022, "patent_count": 30}]

    def _render(self) -> str:
        out = Path(tempfile.mkdtemp()) / "annual_trend.svg"
        chart_runner.render_line_chart(out, "趨勢", self.APP, self.PUB)
        return out.read_text(encoding="utf-8")

    def test_no_family_annotation_on_chart(self):
        """🔴 核心：即使資料帶了 family_count，圖上也不得出現家族數。"""
        svg = self._render()
        self.assertNotIn("族", svg, "趨勢圖仍有家族數標註／圖例")
        for n in ("28", "47"):
            self.assertNotIn(f">{n} ", svg)

    def test_two_series_still_drawn(self):
        """⚠ 拿掉標註不得動到兩條線本體——申請年與授權公告年都要在。"""
        svg = self._render()
        self.assertIn("申請年", svg)
        self.assertIn("授權公告年", svg)
        self.assertEqual(svg.count("<polyline"), 2, "應仍是兩條折線")

    def test_chart_still_valid_svg(self):
        import xml.etree.ElementTree as ET

        ET.fromstring(self._render())   # 解析不過就是壞的 SVG


if __name__ == "__main__":
    unittest.main()
