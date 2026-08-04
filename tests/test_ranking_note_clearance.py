"""排名圖的「最新受讓人」註記不得壓在長條上（2026-08-03 使用者實機截圖）。

## 症狀

主要申請人排名圖，孟喬那列的灰字「最新受讓人：億軒」壓在藍色長條的下緣上，
字被長條蓋掉一截。

## 根因

註記 baseline 寫死 `y + 20 + NOTE_LINE_OFFSET_PX`（offset=12），
但那個 12 是**憑感覺挑的**，沒有把長條高度與註記字高算進去：

- 長條佔 `y+5` ~ `y+23`（高 18）
- 註記 baseline `y+32`，15px 字上緣 ≈ `y+32 - 11.25 = y+20.75`
- **`y+20.75 < y+23`** → 壓進去 2.25px

⚠ 長條幾何與註記位置是同一件事的兩個落點。offset 必須由「長條下緣 ＋ 字高 ＋
間距」**推導**，寫死就會在改了長條高或字級時靜默撞上（I-8 已經為了這個數字調過兩輪）。
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner as cr


class NoteClearsBarTests(unittest.TestCase):
    def _svg(self) -> str:
        rows = [
            {"applicant_display_name": "甲公司", "patent_count": 13,
             "recent_assignee_count": 0},
            {"applicant_display_name": "乙公司", "patent_count": 5,
             "recent_assignee_count": 2,
             "recent_assignee_display_names": "億軒"},
            {"applicant_display_name": "丙公司", "patent_count": 5,
             "recent_assignee_count": 0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.svg"
            cr.render_segmented_bar_chart(
                path, "主要申請人排名", rows, "applicant_display_name",
                "patent_count", "recent_assignee_count", "有最新受讓人")
            return path.read_text(encoding="utf-8")

    def test_note_baseline_clears_its_own_bar(self):
        """註記的字身上緣要在自己那列長條的下緣之下。"""
        svg = self._svg()
        bars = [(float(y), float(h)) for y, h in
                re.findall(r'<rect class="bar-total"[^>]*y="([\d.]+)"[^>]*height="([\d.]+)"', svg)]
        notes = [(float(y), float(s)) for y, s in
                 re.findall(r'<text x="[\d.]+" y="([\d.]+)" font-size="([\d.]+)"[^>]*>最新受讓人：[^<]*</text>', svg)]
        self.assertTrue(notes, "沒抓到受讓人註記——測資或選擇器不對")
        for note_y, size in notes:
            # 字身上緣 ≈ baseline - 0.75 × 字級（一般西文/中文 ascender 比例）
            top = note_y - size * 0.75
            above = [(by, bh) for by, bh in bars if by + bh <= note_y]
            self.assertTrue(above, "註記上方找不到長條")
            bar_bottom = max(by + bh for by, bh in above)
            self.assertGreaterEqual(
                top, bar_bottom,
                f"註記壓進長條 {bar_bottom - top:.1f}px（長條下緣 {bar_bottom}、字上緣 {top:.1f}）")

    def test_note_does_not_reach_next_row(self):
        """⚠ 往下推不能推到下一列的長條上——那是 I-8 原本的毛病，方向相反。"""
        svg = self._svg()
        bars = sorted(float(y) for y in
                      re.findall(r'<rect class="bar-total"[^>]*y="([\d.]+)"', svg))
        notes = [(float(y), float(s)) for y, s in
                 re.findall(r'<text x="[\d.]+" y="([\d.]+)" font-size="([\d.]+)"[^>]*>最新受讓人：[^<]*</text>', svg)]
        for note_y, size in notes:
            bottom = note_y + size * 0.25
            below = [by for by in bars if by > note_y]
            if below:
                self.assertLess(bottom, min(below),
                                f"註記下緣 {bottom:.1f} 侵入下一列長條 {min(below)}")


if __name__ == "__main__":
    unittest.main()
