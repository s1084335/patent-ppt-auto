"""頁尾母體註記的組版側契約（A3，2026-08-06）。

判準（動工前寫下）：
1. 母體字串**排最前**——`_fit_text` 截斷砍尾巴，排頭才砍不到
2. 母體**完整出現**在最終文字中，被截斷即失敗
3. 引擎算、PPT 消費——`build_ppt` 不得自己算母體（它是可攜 skill，不能 import backend）
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_PPT = PROJECT_ROOT / "skills" / "patent-report-ppt" / "scripts" / "build_ppt.py"


def _load_build_ppt():
    if "build_ppt_for_footnote" in sys.modules:
        return sys.modules["build_ppt_for_footnote"]
    spec = importlib.util.spec_from_file_location("build_ppt_for_footnote", BUILD_PPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_ppt_for_footnote"] = module
    spec.loader.exec_module(module)
    return module


class PopulationNotePickupTests(unittest.TestCase):
    """PPT 端只取用引擎寫好的母體字串。"""

    def setUp(self):
        self.bp = _load_build_ppt()

    def test_picks_note_for_the_pages_report(self):
        rd = {"population": {"ipc_main_distribution": "母體 44/55 件（排除外觀設計 11）"}}
        self.assertEqual(
            self.bp._population_note(rd, ("ipc_main_distribution",)),
            "母體 44/55 件（排除外觀設計 11）")

    def test_multi_report_page_takes_first_available(self):
        """⚠ 一頁掛多張時只取第一個有註記的——全印會把單行頁尾撐爆。"""
        rd = {"population": {"cpc_main_distribution": "母體 5/55 件（50 件無 CPC 分類）"}}
        note = self.bp._population_note(rd, ("ipc_main_distribution", "cpc_main_distribution"))
        self.assertEqual(note, "母體 5/55 件（50 件無 CPC 分類）")

    def test_absent_population_is_empty_not_crash(self):
        """⚠ 舊版 report_data 沒有 population 鍵時要能照跑（過渡相容）。"""
        self.assertEqual(self.bp._population_note({}, ("ipc_main_distribution",)), "")

    def test_build_ppt_does_not_compute_population_itself(self):
        """🔴 可攜 skill 不得 import backend，也不得自己算母體。

        母體是引擎的職責（`backend/app/reports/population.py`）；這裡自己算一份
        就是同一份知識兩個落點，兩邊必然分岔。
        """
        source = BUILD_PPT.read_text(encoding="utf-8")
        self.assertNotIn("from backend", source, "可攜 skill 不得 import backend")
        self.assertNotIn("POPULATION_REASONS", source, "母體原因表不得複製到 PPT 端")


class FootnoteOrderingTests(unittest.TestCase):
    """母體必須排最前，且完整出現。"""

    def setUp(self):
        self.bp = _load_build_ppt()

    def _render(self, note, sources_label="IPC 主分類分布", extra=""):
        """呼叫真的 `_render_footnote`，攔下送進版面的字串。"""
        captured: list[str] = []
        theme = self.bp.Theme.load()

        original_add_text = self.bp._add_text

        def _spy(slide, thm, text, **kwargs):
            captured.append(text)

        class _Slide:
            shapes = None

        spec = self.bp.PageSpec(page=1, kind="chart_hero", title="t", topic="t",
                                report_keys=("ipc_main_distribution",))
        ctx = {
            "report_data": {
                "population": {"ipc_main_distribution": note} if note else {},
                "reports": {"ipc_main_distribution": {"label_zh": sources_label}},
            },
            "period": "2011-2026",
        }
        self.bp._add_text = _spy
        try:
            self.bp._render_footnote(_Slide(), theme, spec, ctx, extra=extra)
        finally:
            self.bp._add_text = original_add_text
        return captured[0] if captured else ""

    def test_population_is_first_segment(self):
        note = "母體 44/55 件（排除外觀設計 11）"
        text = self._render(note)
        self.assertTrue(text.startswith("母體 44/55 件"),
                        f"母體沒排最前，截斷時會最先被砍：{text!r}")

    def test_population_survives_fit_text(self):
        """🔴 母體必須**完整**出現——被 `_fit_text` 截掉即失敗。"""
        note = "母體 44/55 件（排除外觀設計 11）"
        text = self._render(note)
        self.assertIn(note, text, f"母體字串被截斷了：{text!r}")

    def test_population_survives_even_with_long_source_name(self):
        """⚠ `sources` 是變數（最長報表名 11 字、一頁可掛兩張）——母體仍不得被砍。"""
        text = self._render("母體 44/55 件（排除外觀設計 11）",
                            sources_label="申請人年度專利分布矩陣、專利權人年度布局矩陣")
        self.assertIn("母體 44/55 件", text, f"長來源名把母體擠掉了：{text!r}")

    def test_condensed_labels(self):
        """濃縮：`資料來源：`→`來源：`、`統計期間：`→`期間`。"""
        text = self._render("母體 55/55 件")
        self.assertNotIn("資料來源", text)
        self.assertNotIn("統計期間", text)

    def test_extra_note_is_appended_after_period(self):
        """判讀限制等額外註記仍保留，但排在母體／來源／期間之後。"""
        text = self._render("母體 55/55 件", extra="含共同申請")
        self.assertTrue(text.endswith("含共同申請"), text)


if __name__ == "__main__":
    unittest.main()
