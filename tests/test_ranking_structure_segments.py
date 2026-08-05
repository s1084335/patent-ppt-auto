"""#3 全順位拆分：申請結構兩段色＋已轉讓斜紋疊加（2026-08-05 四修定案）。

🔴 編碼定案：`是否共同申請` 與 `是否已轉讓` 是**兩個獨立屬性**，一維互斥分段
必然丟資訊（一件共同又已轉讓時只會被算進已轉讓）。故改為兩個視覺通道：
- 顏色分段＝申請結構，左→右＝單獨申請 → 共同申請，兩段加總＝**總件數**
- 斜紋疊加＝已轉讓，畫在**各段右端**（單獨段右端 k1 件、共同段右端 k2 件）

資料來源定案：**不動 DB**，全部由 `report_patent_base` 現有欄位推導
（共同＝原始欄「申請人」含 `|`；已轉讓＝recent_assignee 非空且 ≠ 自己）。
"""
from __future__ import annotations

import re
import unittest


class AggregateFunctionTests(unittest.TestCase):
    """新聚合走既有白名單機制，不另開一條查詢路徑。"""

    def test_new_functions_registered(self):
        from backend.app.reports.report_engine import AGGREGATE_FUNCTIONS

        for name in ("count_multivalue", "count_multivalue_transferred",
                     "count_singlevalue_transferred", "string_agg_co_values"):
            self.assertIn(name, AGGREGATE_FUNCTIONS)

    def test_multivalue_uses_pipe_separator(self):
        """共同申請的判定＝原始欄含 `|`（多值），不是靠展開 VIEW。"""
        from backend.app.reports.report_engine import AGGREGATE_FUNCTIONS

        sql = AGGREGATE_FUNCTIONS["count_multivalue"]
        self.assertIn("|", sql)
        self.assertIn("FILTER", sql)

    def test_transferred_variants_exclude_self(self):
        """已轉讓沿用既有 _excl_group 口徑：受讓人＝自己不算轉讓。"""
        from backend.app.reports.report_engine import AGGREGATE_FUNCTIONS

        for name in ("count_multivalue_transferred", "count_singlevalue_transferred"):
            self.assertIn("{group_col}", AGGREGATE_FUNCTIONS[name])

    def test_co_values_skips_first_part(self):
        """共同申請人＝第 2 個以後的名稱——第 1 個就是分組鍵本人。"""
        from backend.app.reports.report_engine import AGGREGATE_FUNCTIONS

        sql = AGGREGATE_FUNCTIONS["string_agg_co_values"]
        self.assertIn("ORDINALITY", sql.upper())
        self.assertTrue(re.search(r"ord\s*>\s*1", sql), sql)


class ReportDefinitionTests(unittest.TestCase):
    def test_applicant_ranking_has_structure_aggregates(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        # ⚠ 聚合允許可選第四元素（第二來源欄），故以索引取 alias，不用固定長度解包。
        aliases = {entry[2] for entry in REPORT_DEFINITIONS["applicant_ranking"].aggregates}
        for alias in ("joint_count", "joint_transferred_count",
                      "solo_transferred_count", "co_applicant_names"):
            self.assertIn(alias, aliases)

    def test_owner_ranking_has_joint_holding(self):
        """專利權人圖只要單獨／共同持有，**不放受讓人**（定案）。"""
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        aliases = {entry[2] for entry in REPORT_DEFINITIONS["owner_ranking"].aggregates}
        self.assertIn("joint_count", aliases)
        self.assertIn("co_owner_names", aliases)
        self.assertFalse([a for a in aliases if "assignee" in a or "transferred" in a],
                         "專利權人圖不得帶受讓人欄")

    def test_source_table_unchanged(self):
        """⚠ 不得換成展開 VIEW：那會讓件數重複計數（60→74），違反加總＝總件數。"""
        from backend.app.reports.report_definitions import (
            REPORT_DEFINITIONS,
            REPORT_SOURCE_TABLE,
        )

        for name in ("applicant_ranking", "owner_ranking"):
            self.assertEqual(REPORT_DEFINITIONS[name].source_table, REPORT_SOURCE_TABLE)


class SegmentMathTests(unittest.TestCase):
    """兩段加總＝總件數；斜紋不得超過所在段。"""

    def test_solo_derived_and_sums_to_total(self):
        from backend.app.reports.chart_runner import ranking_segments

        seg = ranking_segments({"patent_count": 13, "joint_count": 5,
                                "solo_transferred_count": 1, "joint_transferred_count": 4})
        self.assertEqual(seg["solo"], 8)
        self.assertEqual(seg["joint"], 5)
        self.assertEqual(seg["solo"] + seg["joint"], 13)

    def test_hatch_clamped_to_segment(self):
        """資料異常時斜紋也不得畫超過所在段（畫超過就變成假資訊）。"""
        from backend.app.reports.chart_runner import ranking_segments

        seg = ranking_segments({"patent_count": 5, "joint_count": 2,
                                "solo_transferred_count": 99, "joint_transferred_count": 99})
        self.assertLessEqual(seg["solo_hatch"], seg["solo"])
        self.assertLessEqual(seg["joint_hatch"], seg["joint"])

    def test_missing_keys_degrade_to_all_solo(self):
        """舊資料沒有這些欄位時退化成單段，不得爆掉。"""
        from backend.app.reports.chart_runner import ranking_segments

        seg = ranking_segments({"patent_count": 7})
        self.assertEqual((seg["solo"], seg["joint"]), (7, 0))
        self.assertEqual((seg["solo_hatch"], seg["joint_hatch"]), (0, 0))


class NoteCompositionTests(unittest.TestCase):
    def test_both_notes_joined_and_not_truncated(self):
        from backend.app.reports.chart_runner import ranking_note

        note = ranking_note({
            "co_applicant_names": "祺驊", "joint_count": 4,
            "recent_assignee_display_names": "億軒", "recent_assignee_count": 4,
        })
        self.assertIn("共同申請：祺驊 4件", note)
        self.assertIn("最新受讓人：億軒 4件", note)
        self.assertIn("｜", note)
        self.assertNotIn("…", note)

    def test_owner_note_has_no_assignee(self):
        from backend.app.reports.chart_runner import ranking_note

        note = ranking_note({"co_applicant_names": "甲", "joint_count": 2},
                            co_label="共同持有人", with_assignee=False)
        self.assertIn("共同持有人：甲 2件", note)
        self.assertNotIn("受讓", note)


class CategoricalColorTests(unittest.TestCase):
    """🔴 分段是類別編碼，不得沿用數值色階（2026-08-05 轉圖當場抓到）。

    沿用 `ranking_bar_color` 會讓同一個「單獨申請」在每列都是不同顏色
    ——圖例說一個色、圖上五種色，讀者無從對應。
    """

    def _svg(self, rows, **kwargs):
        import tempfile
        from pathlib import Path

        from backend.app.reports import chart_runner as cr

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.svg"
            cr.render_segmented_bar_chart(path, "t", rows, "k", total_key="patent_count", **kwargs)
            return path.read_text(encoding="utf-8")

    def test_solo_color_same_across_rows(self):
        import re

        svg = self._svg([
            {"k": "大", "patent_count": 20, "joint_count": 0},
            {"k": "小", "patent_count": 2, "joint_count": 0},
        ])
        colors = set(re.findall(r'class="bar-total"[^>]*fill="([^"]+)"', svg))
        self.assertEqual(len(colors), 1, f"單獨段在不同列變色了：{colors}")

    def test_legend_hatch_swatch_is_light(self):
        """圖例的已轉讓色塊底色要用淺階——深底配深斜紋等於看不見。"""
        from backend.app.reports import chart_runner as cr

        svg = self._svg([{"k": "A", "patent_count": 3, "joint_count": 1,
                          "solo_transferred_count": 1}])
        flat = svg.replace(chr(10), "")
        self.assertIn(f'fill="{cr.STRUCTURE_SOLO_COLOR}"/><rect x="236"', flat)
        self.assertEqual(cr.STRUCTURE_SOLO_COLOR, cr.RANKING_BAR_SCALE[-1])


class RendererContractTests(unittest.TestCase):
    def test_no_paren_segment_mark(self):
        """舊「5（2）」青括號寫法必須移除（與段色打架，定案）。"""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "backend" / "app" / "reports"
               / "chart_runner.py").read_text(encoding="utf-8")
        self.assertNotIn('（{segment}）', src)

    def test_legend_labels_configurable(self):
        import inspect

        from backend.app.reports import chart_runner

        sig = inspect.signature(chart_runner.render_segmented_bar_chart)
        self.assertIn("structure_labels", sig.parameters)
        self.assertIn("hatch_label", sig.parameters)


if __name__ == "__main__":
    unittest.main()
