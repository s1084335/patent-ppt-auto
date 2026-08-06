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
    """PPT 端只取用引擎寫好的母體字串。

    ⚠ 2026-08-06 **介面契約變更**：`_population_note(report_data, report_keys)`
    → `_population_note(report_data, spec)`。不是為了方便——分群報表一個
    `report_key` 對應兩個頁面（技術／功效），光靠 report_keys **判不出這頁是哪個通道**，
    於是兩頁都印合併母體「79/55」。通道線索在 `row_filter` 與 `charts`，
    兩者都只有 spec 帶得到。舊斷言隨之改為傳 spec，驗的東西不變。
    """

    def setUp(self):
        self.bp = _load_build_ppt()

    def _spec(self, *report_keys):
        return self.bp.PageSpec(page=1, kind="chart_hero", title="t", topic="t",
                                report_keys=report_keys)

    def test_picks_note_for_the_pages_report(self):
        rd = {"population": {"ipc_main_distribution": "母體 44/55 件（排除外觀設計 11）"}}
        self.assertEqual(
            self.bp._population_note(rd, self._spec("ipc_main_distribution")),
            "母體 44/55 件（排除外觀設計 11）")

    def test_multi_report_page_takes_first_available(self):
        """⚠ 一頁掛多張時只取第一個有註記的——全印會把單行頁尾撐爆。"""
        rd = {"population": {"cpc_main_distribution": "母體 5/55 件（50 件無 CPC 分類）"}}
        note = self.bp._population_note(
            rd, self._spec("ipc_main_distribution", "cpc_main_distribution"))
        self.assertEqual(note, "母體 5/55 件（50 件無 CPC 分類）")

    def test_absent_population_is_empty_not_crash(self):
        """⚠ 舊版 report_data 沒有 population 鍵時要能照跑（過渡相容）。"""
        self.assertEqual(
            self.bp._population_note({}, self._spec("ipc_main_distribution")), "")

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


class ChannelPopulationLookupTests(unittest.TestCase):
    """🔴 regression（2026-08-06）：分通道頁面要取自己那個通道的母體。

    引擎端 0846 起把分群報表的母體改成逐通道鍵 `report_key:slug`
    （slug ∈ tech/effect，與圖檔名後綴同一份，來源 `clustering.sources`）。
    ⚠ 本檔是可攜 skill、不能 import backend，故**鍵的格式就是契約**。

    症狀（修正前）：技術頁與功效頁都印「母體 79/55 件」＝ 35+44 合併值，
    而每頁只呈現單一通道；79 > 55 又無過計數說明，讀者只會判定報表算錯。

    兩種頁面用不同線索判通道，都要涵蓋：
    - **主題分布頁**：依列值拆頁，通道在 `spec.row_filter["source_field"]`
    - **機會矩陣頁**：依圖檔拆頁，通道在圖名後綴 `opportunity_quadrant_tech.svg`
    """

    def setUp(self):
        self.bp = _load_build_ppt()

    def _report_data(self):
        return {"population": {
            "cluster_topic_table:tech": "母體 35/55 件（20 件無分群來源文本）",
            "cluster_topic_table:effect": "母體 44/55 件（11 件無分群來源文本）",
            "opportunity_quadrant:tech": "母體 35/55 件（20 件無分群來源文本）",
            "opportunity_quadrant:effect": "母體 44/55 件（11 件無分群來源文本）",
            "application_trend": "母體 55/55 件",
        }}

    def test_topic_page_uses_row_filter_channel(self):
        spec = self.bp.PageSpec(
            page=5, kind="table_with_points", title="技術主題分布", topic="技術主題分布",
            report_keys=("cluster_topic_table",),
            row_filter=(("source_field", "wips_independent_claims"),))
        note = self.bp._population_note(self._report_data(), spec)
        self.assertIn("35/55", note, f"技術頁沒取到技術通道母體：{note!r}")

        spec_effect = self.bp._spec_with(
            spec, title="功效主題分布",
            row_filter=(("source_field", "effect_summary"),))
        self.assertIn("44/55", self.bp._population_note(self._report_data(), spec_effect))

    def test_opportunity_page_uses_chart_suffix(self):
        spec = self.bp.PageSpec(
            page=7, kind="chart_hero", title="機會評估", topic="機會評估",
            report_keys=("opportunity_quadrant",),
            charts=("opportunity_quadrant_tech.svg",))
        self.assertIn("35/55", self.bp._population_note(self._report_data(), spec))

        spec_effect = self.bp._spec_with(spec, charts=("opportunity_quadrant_effect.svg",))
        self.assertIn("44/55", self.bp._population_note(self._report_data(), spec_effect))

    def test_plain_page_still_uses_report_key(self):
        """非分通道頁維持原行為，不因這次改動退化。"""
        spec = self.bp.PageSpec(
            page=2, kind="chart_hero", title="申請趨勢", topic="申請趨勢",
            report_keys=("application_trend",))
        self.assertIn("55/55", self.bp._population_note(self._report_data(), spec))

    def test_unknown_channel_prints_nothing(self):
        """🔴 判不出通道就不印——寧可沒有，也不要印錯的母體。"""
        spec = self.bp.PageSpec(
            page=5, kind="table", title="主題", topic="主題",
            report_keys=("cluster_topic_table",))
        self.assertEqual(self.bp._population_note(self._report_data(), spec), "")
