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


class DefaultLimitSingleSourceTests(unittest.TestCase):
    """🔴 **預設值**也要收斂到同一個常數（2026-08-10 使用者再次指出）。

    ## 為什麼上面那些測試沒擋住

    本檔上方的測試全都**明確傳入 `limit=10`**，驗的是「傳了 10 就要畫 10 列」。
    但實際跑報表時多數呼叫點**不傳**——吃的是函式簽名的預設值，而那些預設值
    寫死 20，三個月來一直是 20。

    使用者 2026-08-10 實測看到：「各種排名都是 10 個吧，沒有那種一下子七個
    一下子 11 個」——申請人排名 20 根、年度矩陣 10 根、主題演進 13 根。

    ## 根因：同一份知識四個落點

    定案（`chart_runner` 常數區註解，2026-08-04）本來就寫「網頁端前 20、
    **簡報端（圖）前 10**」，`CHART_ROW_LIMIT = 10` 也在，但：

    - `render_bar_chart(..., limit: int = 20)` 等五支出圖函式 ← 寫死 20
    - `run_chart_trial(ranking_limit: int = 20)` ← 寫死 20
    - CLI `--ranking-limit` `default=20` ← **實際生效的那個**
    - 註解「排名全域規則＝前 20 名」 ← 與常數直接打架

    `10` 是後來改的，改的人只動常數沒動其餘三處。
    ⚠ 常數原本定義在檔案第 2965 行、出圖函式在第 632 行——**定義在使用之後，
    就只能各自寫死數字**。已把常數移到出圖函式之前。

    ⚠ 母體不足不補：limit 是天花板不是配額（CPC 只有 4 種 subclass 就是 4 個）。
    """

    CHART_FUNCS = ("render_bar_chart", "render_paired_bar_chart",
                   "render_segmented_bar_chart", "render_grouped_bar_chart",
                   "render_matrix_chart")

    def test_chart_defaults_come_from_the_constant(self):
        """每支出圖函式的預設列數都必須取自 CHART_ROW_LIMIT。"""
        import inspect

        for fname in self.CHART_FUNCS:
            func = getattr(cr, fname, None)
            if func is None:
                continue
            for pname in ("limit", "row_limit"):
                param = inspect.signature(func).parameters.get(pname)
                if param is None or param.default is inspect.Parameter.empty:
                    continue
                self.assertEqual(
                    param.default, cr.CHART_ROW_LIMIT,
                    f"{fname}({pname}=) 預設 {param.default}，"
                    f"與 CHART_ROW_LIMIT={cr.CHART_ROW_LIMIT} 不一致——第二個落點")

    def test_run_chart_trial_default_matches(self):
        import inspect

        default = inspect.signature(cr.run_chart_trial).parameters["ranking_limit"].default
        self.assertEqual(default, cr.CHART_ROW_LIMIT)

    def test_cli_ranking_limit_default_matches(self):
        """🔴 CLI 的 `--ranking-limit`——**它才是實際跑報表時生效的那個**。

        parser 建在 `main()` 裡沒有獨立 build_parser，只能掃原始碼。
        ⚠ 不得因為「不好測」就跳過：申請人排名畫出 20 根的直接原因就是這一行。
        ⚠ 只看 add_argument 那行：檔內註解也提到 `--ranking-limit 20`（在講這段歷史）。
        """
        source = Path(cr.__file__).read_text(encoding="utf-8")
        line = next((ln for ln in source.splitlines()
                     if "--ranking-limit" in ln and "add_argument" in ln), None)
        self.assertIsNotNone(line, "找不到 --ranking-limit 參數宣告")
        self.assertIn("default=CHART_ROW_LIMIT", line.replace(" ", ""),
                      f"CLI 預設沒讀常數，寫死了數字：{line.strip()}")

    def test_no_hardcoded_twenty_left(self):
        """守住「別再長回來」——六處寫死的 20 是分批加進來的，每處單看都合理。"""
        source = Path(cr.__file__).read_text(encoding="utf-8")
        offenders = [
            ln.strip() for ln in source.splitlines()
            if ("limit: int = 20" in ln or "limit=20" in ln.replace(" ", ""))
            and not ln.strip().startswith("#")
        ]
        self.assertEqual(offenders, [], f"仍有寫死的 20：{offenders}")


class DefaultTruncationBehaviourTests(unittest.TestCase):
    """行為面：**不傳 limit** 時也要截到前十；母體不足不補。"""

    def _labels_in(self, svg: Path) -> int:
        text = svg.read_text(encoding="utf-8", errors="ignore")
        return sum(1 for i in range(30) if f"公司{i:02d}" in text)

    def _rows(self, n):
        return [{"name": f"公司{i:02d}", "patent_count": 100 - i} for i in range(n)]

    def test_over_limit_truncated_without_explicit_arg(self):
        """🔴 母體 25 筆、**不傳 limit** → 圖上只有 CHART_ROW_LIMIT 個。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bar.svg"
            cr.render_bar_chart(p, "測試", self._rows(25), "name")
            self.assertEqual(self._labels_in(p), cr.CHART_ROW_LIMIT)

    def test_under_limit_not_padded(self):
        """母體 4 筆 → 就是 4 個（使用者：「母體不到的當然不強制」）。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bar.svg"
            cr.render_bar_chart(p, "測試", self._rows(4), "name")
            self.assertEqual(self._labels_in(p), 4)

    def test_exactly_at_limit(self):
        """邊界：母體恰好等於上限 → 全印，不多不少。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bar.svg"
            cr.render_bar_chart(p, "測試", self._rows(cr.CHART_ROW_LIMIT), "name")
            self.assertEqual(self._labels_in(p), cr.CHART_ROW_LIMIT)


if __name__ == "__main__":
    unittest.main()
