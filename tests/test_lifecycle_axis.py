"""J-5（2026-08-04）：生命週期圖的軸與年份標籤。

## 兩個症狀（第五輪實機 p3）

1. **橫軸畫到 10，但資料最大只有 7 家**——右側 1/3 全空。
   根因鏈：x_max 先乘 1.15 餘裕（7→8.05），nice_ticks 為了頂過 8.05 只能選
   2.5 步進，取整後印出 0/2/5/8/10——**連等差都不是**。
   件數與家數都是整數，刻度不該出現 2.5 這種要取整的步進。
2. **合併年份標籤「2011、2017、2021、2026」壓在 x 軸線上**，與刻度數字重疊。
   避讓演算法只把資料點當障礙，軸線與刻度區不在障礙清單裡。
"""
import re
import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner as cr


class NiceTicksIntegerTests(unittest.TestCase):
    def test_ticks_are_equally_spaced_integers(self):
        """整數資料的刻度必須等差——0/2/5/8/10 讓讀者無法心算比例。"""
        for span in (7, 8.05, 9, 13, 17.25, 40):
            with self.subTest(span=span):
                ticks = cr.nice_ticks(span)
                diffs = {b - a for a, b in zip(ticks, ticks[1:])}
                self.assertEqual(len(diffs), 1, f'span={span} 刻度不等差：{ticks}')

    def test_axis_does_not_overshoot(self):
        """資料最大 7 時，軸頂不得超過 8——多一格就是留白 1/3 的來源。"""
        self.assertLessEqual(cr.nice_ticks(7)[-1], 8, cr.nice_ticks(7))


class LifecycleAxisTests(unittest.TestCase):
    def _svg(self):
        rows = [{'application_year': 2011 + i,
                 'applicant_count': [1,2,1,2,3,2,4,5,3,7,5,4,5,1][i],
                 'patent_count': [1,2,1,4,5,1,5,7,3,15,4,11,4,1][i]} for i in range(14)]
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / 'lc.svg'
            cr.render_lifecycle_chart(p, '專利生命週期', rows)
            return p.read_text(encoding='utf-8')

    def test_x_axis_stops_near_data_max(self):
        svg = self._svg()
        xt = [int(m) for m in re.findall(
            r'text-anchor="middle" font-size="[0-9.]+" fill="[^"]+">(\d+)</text>', svg)]
        self.assertTrue(xt, '抓不到 x 刻度')
        self.assertLessEqual(max(xt), 8, f'資料最大 7 家，軸卻畫到 {max(xt)}')

    def test_year_labels_stay_above_the_axis(self):
        """年份標籤（含合併標籤）不得壓到軸線／刻度區。"""
        svg = self._svg()
        top, bottom_pad = 64, 84
        h = int(re.search(r'height="(\d+)"', svg).group(1))
        plot_bottom = h - bottom_pad
        bad = [(y, t) for y, t in
               [(float(m.group(1)), m.group(2)) for m in re.finditer(
                   r'<text x="[0-9.]+" y="([0-9.]+)" font-size="[0-9.]+" fill="[^"]+">((?:19|20)\d{2}[^<]*)</text>', svg)]
               if y > plot_bottom]
        self.assertEqual(bad, [], f'年份標籤壓到軸區：{bad}')


if __name__ == '__main__':
    unittest.main()
