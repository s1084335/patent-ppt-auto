"""前十大申請人跨頁一致（2026-08-07 使用者裁決）。

## 定案

「排名就是取前十個，你現在申請人矩陣取十個，排名卻七個，這樣絕對會有問題」。

實測不一致：主要申請人排名畫 7 列、申請人年度矩陣 10 列、狀態矩陣 9 列——
同一個「前十大」在三頁是三種數，讀者跨頁對照時第 8～10 名忽隱忽現。

## 根因與規則

排名圖（render_segmented_bar_chart）與矩陣圖（render_matrix_chart）各有
「畫布高度上限 → 靜默砍列」規則，蓋過 limit=10。裁決＝**列數優先於高度**：
有 10 名就畫 10 列，畫布長高由字級解算器補償（字在投影片上仍是 14pt/12pt，
代價是圖在版面上變窄——一致性比寬度重要）。
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner as cr


class SegmentedBarTop10Tests(unittest.TestCase):
    def test_draws_exactly_ten_rows_even_with_notes(self):
        """帶受讓人註記的兩行列不得把第 8–10 名擠掉。"""
        rows = []
        for i in range(12):
            rows.append({"applicant_display_name": f"公司{chr(65 + i)}",
                         "patent_count": 20 - i, "joint_count": 3,
                         "co_applicant_names": "甲", "recent_assignee_display_names": "乙",
                         "recent_assignee_count": 1})
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "r.svg"
            cr.render_segmented_bar_chart(
                p, "主要申請人排名", rows, "applicant_display_name",
                total_key="patent_count", limit=10)
            svg = p.read_text(encoding="utf-8")
        drawn = len(re.findall(r">公司[A-L]<", svg))
        self.assertEqual(drawn, 10, f"排名圖畫了 {drawn} 列——前十就是前十")
        self.assertIn("顯示前 10/12 名", svg)


class MatrixTop10Tests(unittest.TestCase):
    def _long_rows(self, n):
        return [{"applicant_display_name": f"公司{i:02d}", "status_bucket": "已授權",
                 "patent_count": 30 - i} for i in range(n)]

    def test_matrix_draws_exactly_row_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.svg"
            meta = cr.render_matrix_chart(
                p, "專利狀態分析", self._long_rows(25),
                row_key="applicant_display_name", col_key="status_bucket",
                row_limit=10)
        self.assertEqual(meta["rows_drawn"], 10,
                         f"矩陣畫了 {meta['rows_drawn']} 列——limit=10 必須畫滿")

    def test_applicant_country_uses_chart_row_limit(self):
        """公司×國家矩陣的顯示列數也收斂到同一個前十（資料照存 20 供網頁）。"""
        src = (Path(__file__).resolve().parents[1] / "backend" / "app" / "reports"
               / "chart_runner.py").read_text(encoding="utf-8")
        seg = src[src.index("def _build_applicant_country_section"):]
        seg = seg[:seg.index("\ndef ")]
        self.assertIn("row_limit=CHART_ROW_LIMIT", seg,
                      "公司×國家矩陣未用統一的前十顯示上限")


if __name__ == "__main__":
    unittest.main()
