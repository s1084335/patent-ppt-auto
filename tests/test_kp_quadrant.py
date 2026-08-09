"""KP 競爭定位象限（P2 第 5 節版型；範例＝滑雪機 V2 p7）。

形狀（範例逐項對照）：橫軸＝跨國布局深度（國數）、縱軸＝技術廣度（主題數）、
泡泡大小＝家族件數、顏色＝定位分類、附中位數象限線與圖例。

⚠ 定位分類是**由資料推導**、不是 AI 說了算：
- 前案（多失效）：授權 0 且有失效——「僅具前案價值」
- 全領域布局：兩軸皆高
- 單一技術深布局：國多但主題少
- 利基／探索：其餘

⚠ 沿用既有 `render_bubble_chart` 的骨架，只加象限線與分類配色——不重造一支
散點圖（兩支散點圖必然分岔）。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.reports import chart_runner as cr


ROWS = [
    # 兩軸皆高＋有授權 → 全領域布局
    {"applicant_display_name": "帝瑪斯", "country_count": 3, "topic_count": 3,
     "family_count": 11, "granted_count": 10, "dead_count": 1},
    # 國多主題少 → 單一技術深布局
    {"applicant_display_name": "扭矩", "country_count": 4, "topic_count": 1,
     "family_count": 1, "granted_count": 0, "dead_count": 0},
    # 0 授權且有失效 → 前案
    {"applicant_display_name": "澳瑞特", "country_count": 1, "topic_count": 1,
     "family_count": 2, "granted_count": 0, "dead_count": 2},
    # 其餘 → 利基／探索
    {"applicant_display_name": "朗美", "country_count": 1, "topic_count": 2,
     "family_count": 2, "granted_count": 2, "dead_count": 0},
]


class PositionClassTests(unittest.TestCase):
    def test_prior_art_when_no_grant_and_dead(self):
        self.assertEqual(cr.kp_position_class(ROWS[2], 2, 2), cr.KP_CLASS_PRIOR_ART)

    def test_full_domain_when_both_axes_high(self):
        self.assertEqual(cr.kp_position_class(ROWS[0], 2, 2), cr.KP_CLASS_FULL_DOMAIN)

    def test_single_tech_deep_when_wide_but_narrow(self):
        self.assertEqual(cr.kp_position_class(ROWS[1], 2, 2), cr.KP_CLASS_SINGLE_TECH)

    def test_niche_otherwise(self):
        self.assertEqual(cr.kp_position_class(ROWS[3], 2, 2), cr.KP_CLASS_NICHE)

    def test_class_is_data_derived_not_free_text(self):
        """分類必須來自資料欄位——不得吃 AI 給的字串。"""
        import inspect

        src = inspect.getsource(cr.kp_position_class)
        for field in ("granted_count", "dead_count", "country_count", "topic_count"):
            self.assertIn(field, src)


class QuadrantChartTests(unittest.TestCase):
    def _svg(self) -> str:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "kp_quadrant.svg"
            cr.render_kp_quadrant_chart(path, "Key Players 競爭定位", ROWS)
            return path.read_text(encoding="utf-8")

    def test_axes_labelled_with_meaning(self):
        svg = self._svg()
        self.assertIn("跨國布局深度", svg)
        self.assertIn("技術廣度", svg)
        self.assertIn("家族件數", svg, "泡泡大小的意義要寫出來")

    def test_every_player_plotted_and_named(self):
        svg = self._svg()
        for row in ROWS:
            self.assertIn(row["applicant_display_name"], svg)
        self.assertGreaterEqual(svg.count("<circle"), len(ROWS))

    def test_legend_lists_position_classes(self):
        svg = self._svg()
        for name in (cr.KP_CLASS_FULL_DOMAIN, cr.KP_CLASS_SINGLE_TECH,
                     cr.KP_CLASS_NICHE, cr.KP_CLASS_PRIOR_ART):
            self.assertIn(name, svg)

    def test_quadrant_lines_drawn(self):
        """中位數象限線：讀者要看得出四格的界線在哪。"""
        self.assertIn("stroke-dasharray", self._svg())

    def test_empty_rows_no_crash(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.svg"
            cr.render_kp_quadrant_chart(path, "空", [])
            self.assertTrue(path.exists())


class BuilderIntegrationTests(unittest.TestCase):
    def test_kp_quadrant_preset_is_approved(self):
        from backend.app.reports.planning_contracts import APPROVED_LAYOUT_PRESETS

        self.assertIn("kp_quadrant", APPROVED_LAYOUT_PRESETS)

    def test_builder_registers_kp_renderers(self):
        import sys
        from pathlib import Path as P

        sys.path.insert(0, str(P(__file__).resolve().parents[1] / "skills"
                                / "patent-report-ppt" / "scripts"))
        import build_ppt as bp

        for kind in ("kp_quadrant", "kp_deepdive", "kp_cards"):
            self.assertIn(kind, bp.RENDERERS, f"組版端缺 {kind} 版型")


if __name__ == "__main__":
    unittest.main()
