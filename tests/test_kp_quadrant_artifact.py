"""KP 競爭定位象限要真的產出圖檔（2026-08-09 使用者：先把 A 做好）。

現況：`render_kp_quadrant_chart` 寫好了、`applicant_strength_profile` 的資料
也進了 `chart_rows`（實測 10 列），但**沒有任何呼叫端產圖**。

⚠ 後果是靜默的：組版端 `_render_kp_quadrant` 走 chart_hero，拿不到圖就降級成
stat_callout——版型名稱對、renderer 在、測試也綠，投影片卻只剩一個大數字。
Key Player 深度正是兩份範例的核心之一。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.reports import chart_runner as cr

# 兩軸＋泡泡＋定位所需欄位（形狀取自 applicant_strength_rows 的輸出）。
ROWS = [
    {"applicant_display_name": "甲公司", "country_count": 4, "topic_count": 5,
     "family_count": 12, "granted_count": 8, "dead_count": 1},
    {"applicant_display_name": "乙公司", "country_count": 1, "topic_count": 1,
     "family_count": 3, "granted_count": 0, "dead_count": 2},
    {"applicant_display_name": "丙公司", "country_count": 2, "topic_count": 3,
     "family_count": 5, "granted_count": 3, "dead_count": 0},
]


class ArtifactMappingTests(unittest.TestCase):
    """圖檔要對得回 report_key，否則組版端的 ChartIndex 找不到它。"""

    def test_filename_maps_to_report_key(self):
        self.assertEqual(cr.report_names_for_artifact(cr.KP_QUADRANT_FILENAME),
                         ["applicant_strength_profile"])

    def test_web_variant_still_excluded(self):
        """⚠ 對照組：web profile 的同名圖仍不得進 PPT 素材索引。"""
        web = cr.KP_QUADRANT_FILENAME.replace(".svg", ".web.svg")
        self.assertEqual(cr.report_names_for_artifact(web), [])


class RenderingTests(unittest.TestCase):
    def test_chart_is_written(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / cr.KP_QUADRANT_FILENAME
            cr.render_kp_quadrant_chart(target, "申請人四面向", ROWS)
            self.assertTrue(target.exists())
            svg = target.read_text(encoding="utf-8")
            for name in ("甲公司", "乙公司", "丙公司"):
                self.assertIn(name, svg, "象限圖要點得出是誰")

    def test_positions_are_derived_not_narrated(self):
        """定位分類由資料推導——它是統計事實，不是 AI 給的字串。"""
        # 象限線＝中位數（由呼叫端算好傳入，分類函式不自己決定基準）。
        x_med, y_med = 2.0, 3.0
        classes = {cr.kp_position_class(row, x_med, y_med) for row in ROWS}
        self.assertTrue(classes <= {
            cr.KP_CLASS_FULL_DOMAIN, cr.KP_CLASS_SINGLE_TECH,
            cr.KP_CLASS_NICHE, cr.KP_CLASS_PRIOR_ART}, classes)
        # 乙公司：0 授權且有失效 → 前案優先（此規則為 2026-08-08 定案）
        self.assertEqual(cr.kp_position_class(ROWS[1], x_med, y_med), cr.KP_CLASS_PRIOR_ART)


class SectionWiringTests(unittest.TestCase):
    """有資料才出圖出卡；沒資料不硬湊（撐不起就不開那一頁）。"""

    def test_builder_emits_chart_and_section(self):
        with TemporaryDirectory() as tmp:
            ctx = cr.ChartContext(
                run_dir=Path(tmp), ranking_limit=10, ipc_levels=(4,), cpc_levels=(4,),
                patent_ids=None, filters=None, report_scope="company", analysis_id=None)
            cr.emit_kp_quadrant(ctx, ROWS)
            self.assertTrue((Path(tmp) / cr.KP_QUADRANT_FILENAME).is_file())
            keys = [s.get("report_key") for s in ctx.sections]
            self.assertIn("applicant_strength_profile", keys)

    def test_no_rows_no_artifact(self):
        with TemporaryDirectory() as tmp:
            ctx = cr.ChartContext(
                run_dir=Path(tmp), ranking_limit=10, ipc_levels=(4,), cpc_levels=(4,),
                patent_ids=None, filters=None, report_scope="company", analysis_id=None)
            cr.emit_kp_quadrant(ctx, [])
            self.assertFalse((Path(tmp) / cr.KP_QUADRANT_FILENAME).exists())
            self.assertEqual(ctx.sections, [])


if __name__ == "__main__":
    unittest.main()


class LabelAvoidanceTests(unittest.TestCase):
    """標籤避讓要真的避開（2026-08-09 首次實機產圖後補）。

    🔴 實物才看得到的兩個缺陷：
    1. 最小垂直間距寫死 12px，但字高約 17px——**間距比字還小，必然重疊**。
       實機「澳瑞特體育」與「山東舒優特健身科技」兩行文字直接互相覆蓋。
    2. 候選位置可以往上推出繪圖區，最高的泡泡標籤因此疊到圖表副標。

    ⚠ 這兩個都不是「避讓沒接上」（它有接），是避讓的參數與邊界錯——
    測試驗得到「有沒有呼叫」，驗不到「避開了沒有」。
    """

    import re as _re

    LABEL_PX = 17.0

    def _label_ys(self, svg_parts: list[str]) -> list[float]:
        ys = []
        for part in svg_parts:
            m = self._re.search(r'<text x="[-0-9.]+" y="([-0-9.]+)"', part)
            if m:
                ys.append(float(m.group(1)))
        return sorted(ys)

    def test_same_spot_labels_clear_each_other(self):
        """五個幾乎同座標的泡泡：任兩個標籤的垂直間距不得小於字高。"""
        points = [(300.0, 400.0, 14.0, f"公司{i}") for i in range(5)]
        ys = self._label_ys(cr.place_bubble_labels(points, self.LABEL_PX))
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        self.assertTrue(all(g >= self.LABEL_PX for g in gaps),
                        f"標籤垂直間距小於字高（會重疊）：{gaps}")

    def test_labels_stay_below_top_limit(self):
        """⚠ 往上推不得越過繪圖區上緣，否則會壓到標題／副標。"""
        points = [(300.0, 90.0, 20.0, f"公司{i}") for i in range(4)]
        ys = self._label_ys(cr.place_bubble_labels(points, self.LABEL_PX, top_limit=72.0))
        self.assertTrue(all(y >= 72.0 for y in ys), f"標籤越過上緣：{ys}")

    def test_far_apart_labels_keep_default_position(self):
        """⚠ 對照組：本來就不衝突的標籤不該被推動（避讓不是無條件重排）。"""
        points = [(100.0, 400.0, 14.0, "甲"), (600.0, 400.0, 14.0, "乙")]
        ys = self._label_ys(cr.place_bubble_labels(points, self.LABEL_PX))
        self.assertEqual(ys[0], ys[1], "互不干擾的標籤應維持同一預設高度")
