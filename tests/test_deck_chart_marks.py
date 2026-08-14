"""跨度圖宣告式標記（tasks 3b.4 殘項，design §7.8b 實作要點）。

## 分工（與 §7.4 圖形文法同一個形）

條的位置與長度＝機械（資料）；**哪幾條要 highlight、世代分界線畫在哪年
＝CLI 在 content.json 宣告**（`chart_marks`），引擎照宣告渲染。
CLI 選、引擎畫——不破「CLI 不改圖」。

## 機制

`apply_chart_marks.py <work_dir>`（機械步，runner 於目視迴圈每輪執行）：
- 讀 content.json 各頁的 `chart_marks: {<chart>: {"highlight": [名稱…],
  "marker": {"year": Y, "label": "…"}}}`
- 自 pristine 備份（`charts_orig_marks/`）重套——每輪冪等，不疊加
- highlight＝列標籤加粗變色＋該列條描邊；marker＝該年畫垂直虛線＋標籤
- 🔴 宣告接不上資料（名稱不在圖上、年份不在軸上）→ **非零退出**，
  走修稿輪（與 check 閘門同一條路）——靜默略過會讓 CLI 以為標了
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"
PY = sys.executable


def _render_real_span_svg(path: Path):
    """用真引擎函式產跨度圖——測試對的是引擎實際輸出的結構，不是自造的假 SVG。"""
    from backend.app.reports.chart_runner import render_year_span_chart

    years = list(range(2018, 2025))
    values = {("曾晴", 2020): 4, ("曾晴", 2022): 5, ("曾晴", 2024): 5,
              ("孟喬", 2019): 2, ("孟喬", 2021): 3}
    render_year_span_chart(
        path, "申請人布局節奏（測試）",
        {"years": years, "values": values, "max_value": 5},
        ["曾晴", "孟喬"])


class ChartMarksTests(unittest.TestCase):
    def _work(self, marks: dict | None) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        charts = work / "charts"
        charts.mkdir()
        _render_real_span_svg(charts / "applicant_year_matrix.svg")
        from tests.test_deck_caliber_page import _minimal_content
        content = _minimal_content()
        page = {"title": "布局節奏", "takeaway": "t",
                "charts": ["applicant_year_matrix"], "lines": ["說明"], "tag": None}
        if marks is not None:
            page["chart_marks"] = {"applicant_year_matrix": marks}
        content["pages"] = [page]
        (work / "content.json").write_text(
            json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return work

    def _apply(self, work: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PY, str(SCRIPTS / "apply_chart_marks.py"), str(work)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")

    def test_highlight_and_marker_applied(self):
        work = self._work({"highlight": ["曾晴"],
                           "marker": {"year": 2021, "label": "第二代起點"}})
        proc = self._apply(work)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        svg = (work / "charts" / "applicant_year_matrix.svg").read_text(encoding="utf-8")
        self.assertIn("data-mark=\"highlight\"", svg)
        self.assertIn("第二代起點", svg)
        self.assertIn("data-mark=\"marker\"", svg)
        # 冪等：重跑一次不得疊加
        self._apply(work)
        svg2 = (work / "charts" / "applicant_year_matrix.svg").read_text(encoding="utf-8")
        self.assertEqual(svg2.count("第二代起點"), svg.count("第二代起點"))

    def test_no_marks_is_noop(self):
        work = self._work(None)
        before = (work / "charts" / "applicant_year_matrix.svg").read_bytes()
        proc = self._apply(work)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("MARKS_APPLIED", proc.stdout)
        self.assertEqual((work / "charts" / "applicant_year_matrix.svg").read_bytes(),
                         before)

    def test_unknown_highlight_name_fails_loud(self):
        """宣告的名稱不在圖上＝CLI 宣告錯——非零退出走修稿輪，不靜默略過。"""
        work = self._work({"highlight": ["不存在的公司"]})
        proc = self._apply(work)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("不存在的公司", proc.stdout)

    def test_marker_year_off_axis_fails_loud(self):
        work = self._work({"marker": {"year": 1999, "label": "x"}})
        proc = self._apply(work)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("1999", proc.stdout)


if __name__ == "__main__":
    unittest.main()
