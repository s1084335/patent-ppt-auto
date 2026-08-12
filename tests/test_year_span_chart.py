"""申請人年度分布改「跨度圖」的幾何契約（2026-08-12 使用者定案）。

## 為什麼從泡泡矩陣改成跨度圖

實測 `report_trial_20260812_133901`：申請人年度矩陣 10 列、跨度 0–5 年、
**平均只佔全軸 11%**（4 列是單點），泡泡散在 140 格裡只有 25 格有值——
八成版面是空的，而且「誰早誰晚、誰只打一槍、有無世代斷層」要讀者自己在腦中連。
改成跨度條後，這些是一眼可辨的形狀。

⚠ 判準是「跨度本身有沒有資訊」，不是稀疏度：主題演進同樣稀疏，但跨度平均佔
全軸 56%（含一條滿軸），畫成跨度條會糊成等長——故**主題演進維持泡泡**，
兩張圖各自定型（design 7.8b）。

## 🔴 跨度條不得失真

純甘特條會把「2020、2022、2024 三年有件」畫成「2020→2024 連續投入」。
本專案資料的填格率僅約 11%，這個失真會**系統性地**把斷續投入說成持續布局。
故契約要求：條表達跨度、**條上以點標出實際有件的年份**，兩者並存。
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner


def _layout(rows: list[dict]) -> dict:
    return chart_runner.year_bubble_matrix_layout(
        rows, "applicant_display_name", row_limit=20)


def _render(rows: list[dict], row_names: list[str] | None = None) -> str:
    layout = _layout(rows)
    names = row_names if row_names is not None else layout["top_rows"]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "span.svg"
        chart_runner.render_year_span_chart(path, "申請人年度專利分布", layout, names)
        return path.read_text(encoding="utf-8")


def _bars(svg: str) -> list[tuple[float, float]]:
    """跨度條（帶 data-role="span-bar"）的 (x, width)。"""
    return [(float(m.group(1)), float(m.group(2))) for m in re.finditer(
        r'<rect data-role="span-bar" x="([\d.]+)"[^>]*width="([\d.]+)"', svg)]


class SpanGeometryTests(unittest.TestCase):
    """條的起訖必須對得上該列實際的最早／最晚年。"""

    _ROWS = [
        # 三年有件但不連續——跨度 2019→2024
        {"applicant_display_name": "甲公司", "application_year": 2019, "patent_count": 1},
        {"applicant_display_name": "甲公司", "application_year": 2022, "patent_count": 3},
        {"applicant_display_name": "甲公司", "application_year": 2024, "patent_count": 5},
        # 早期玩家——跨度 2013→2016
        {"applicant_display_name": "乙公司", "application_year": 2013, "patent_count": 2},
        {"applicant_display_name": "乙公司", "application_year": 2016, "patent_count": 1},
        # 單點——只有 2022 一年
        {"applicant_display_name": "丙公司", "application_year": 2022, "patent_count": 5},
    ]

    def test_one_bar_per_row(self):
        svg = _render(self._ROWS)
        self.assertEqual(len(_bars(svg)), 3, "每列恰一條跨度條")

    def test_longer_span_draws_wider_bar(self):
        """甲（2019–2024，5 年）的條必須明顯寬於乙（2013–2016，3 年）。"""
        svg = _render(self._ROWS, ["甲公司", "乙公司", "丙公司"])
        widths = [w for _, w in _bars(svg)]
        self.assertGreater(widths[0], widths[1], "跨度大的條要比較寬")

    def test_single_year_row_is_a_marker_not_a_line(self):
        """⚠ 單點列不能畫成 0 寬（看不見），要有可辨識的方塊。"""
        svg = _render(self._ROWS, ["甲公司", "乙公司", "丙公司"])
        single = _bars(svg)[2]
        self.assertGreater(single[1], 4.0, "單一年份仍須畫得出來")
        self.assertLess(single[1], _bars(svg)[1][1], "但不得比真正的跨度條寬")

    def test_active_years_marked_on_bar(self):
        """🔴 條上要標出實際有件的年份，否則「2019/2022/2024」會被讀成連續投入。"""
        svg = _render(self._ROWS, ["甲公司"])
        dots = re.findall(r'<circle data-role="active-year"', svg)
        self.assertEqual(len(dots), 3, "甲公司三個有件年份都要標點")

    def test_total_labelled_at_bar_end(self):
        """跨度圖丟失逐年件數，總件數必須留在條末（否則量級資訊全失）。"""
        svg = _render(self._ROWS, ["甲公司"])
        self.assertRegex(svg, r'data-role="span-total"[^>]*>9<', "甲公司總件數 9 應標在條末")


class SpanCapacityTests(unittest.TestCase):
    """一張圖放 20 列——原本 Top10 與第 11–20 名要兩張，改版後併成一張。"""

    def _many(self, n: int) -> list[dict]:
        return [{"applicant_display_name": f"公司{i:02d}",
                 "application_year": 2015 + (i % 8), "patent_count": 20 - i}
                for i in range(n)]

    def test_twenty_rows_fit_in_one_canvas(self):
        svg = _render(self._many(20))
        height = float(re.search(r'height="([\d.]+)"', svg).group(1))
        self.assertLessEqual(height, chart_runner.CHART_CANVAS_MAX_HEIGHT,
                             "20 列必須放進單一畫布（不再拆 _more 第二張）")
        self.assertEqual(len(_bars(svg)), 20)

    def test_rows_sorted_by_total_desc(self):
        """列序沿用泡泡矩陣：依總件數由大到小（讀者期待第一列是最大玩家）。"""
        svg = _render(self._many(6))
        labels = re.findall(r'<text data-role="row-label"[^>]*>([^<]+)</text>', svg)
        self.assertEqual(labels[0], "公司00", "總件數最大者應在第一列")


class SpanArtifactTests(unittest.TestCase):
    """檔案面：不再產第二張 `_more`。"""

    def test_more_artifact_unregistered(self):
        self.assertNotIn("applicant_year_matrix_more.svg", chart_runner.CHART_FILE_REPORTS,
                         "第 11–20 名已併入單張，對照表不應留下孤兒登記")


if __name__ == "__main__":
    unittest.main()
