"""批 3：資料點標籤不得互相擠在一起（H-5，2026-08-03 第三輪實機）。

p3 生命週期圖左下角 `2021`／`2011`、`2026`／`2017`、`2015`／`2025` 兩兩緊貼，
讀者分不出哪個標籤屬於哪個點。

⚠ 上一輪（E7）已經做了避讓，但**四個候選方位、零間距**：
① 方位太少——點擠成一團時四個位置很快用完，就退回「不標」（丟資訊）；
② 判定用的是「框有沒有相交」，相切（間距 0）不算相交，於是視覺上仍然黏在一起。

判準：兩個相鄰資料點的標籤都要放得出來，且彼此保有可辨識的間距。
"""
from __future__ import annotations

import unittest

from backend.app.reports import chart_runner as cr


class AdjacentPointLabelTests(unittest.TestCase):
    def test_two_close_points_both_get_labels(self):
        """兩個相距 10px 的點都要標得出來——放棄標示等於丟資訊。"""
        items = [(100.0, 100.0, "2011"), (110.0, 104.0, "2021")]
        obstacles = [(100.0, 100.0, 5.0), (110.0, 104.0, 5.0)]
        placed = cr.place_point_labels(items, obstacles)
        self.assertEqual(len(placed), 2)
        self.assertTrue(all(p is not None for p in placed),
                        "有標籤被放棄了——候選方位不足")

    def test_placed_labels_keep_visible_gap(self):
        """兩個標籤之間要有可辨識的間距，不能只是「沒有相交」。"""
        items = [(100.0, 100.0, "2011"), (110.0, 104.0, "2021")]
        obstacles = [(100.0, 100.0, 5.0), (110.0, 104.0, 5.0)]
        placed = cr.place_point_labels(items, obstacles)
        boxes = [cr.label_box(px, py, text)
                 for (px, py), (_x, _y, text) in zip(placed, items) if (px, py)]
        self.assertEqual(len(boxes), 2)
        a, b = boxes
        # 外擴 LABEL_MIN_GAP_PX 之後仍不得相交
        gap = cr.LABEL_MIN_GAP_PX
        grown = (a[0] - gap, a[1] - gap, a[2] + gap, a[3] + gap)
        self.assertFalse(cr.boxes_overlap(grown, b),
                         "兩個年份標籤貼在一起，讀者分不出誰是誰")

    def test_min_gap_is_named(self):
        self.assertTrue(hasattr(cr, "LABEL_MIN_GAP_PX"))
        self.assertGreater(cr.LABEL_MIN_GAP_PX, 0)


class QuadrantLegendTests(unittest.TestCase):
    """H-8：象限板圖例方塊不得壓在前綴文字上（實機 p17／p18）。

    原本 `legend_x = margin_l + 200`、`legend_x += 130` 都是寫死的，
    而前綴「色＝龍頭涉入｜數字＝件/家」約 234px、每個圖例項約 144px——
    ⚠ 寫死的位置只要文案一改就會壓上去，而且改文案的人不會想到要改那個數字。
    """

    def test_legend_starts_after_prefix_text(self):
        prefix_px = cr._text_px(cr.LEGEND_PREFIX_TEXT)
        self.assertGreater(prefix_px, 200,
                           "測試前提：前綴確實比原本寫死的 200px 寬")

    def test_legend_item_step_covers_widest_item(self):
        """每一步的推進量要大於該項文字寬度＋色塊，否則下一項會壓上來。"""
        for desc in ("龍頭涉入≥2家", "龍頭涉入1家", "無龍頭涉入"):
            step = 18 + cr._text_px(desc) + cr.LEGEND_ITEM_GAP_PX
            with self.subTest(desc=desc):
                self.assertGreater(step, 18 + cr._text_px(desc),
                                   "推進量沒有含間距，圖例會黏在一起")
                self.assertGreater(step, 130, "仍小於原本寫死的 130px，等於沒修")

    def test_prefix_is_single_source(self):
        """量寬度與畫出來要用同一個字串。"""
        import inspect

        src = inspect.getsource(cr.render_opportunity_quadrant_svg)
        self.assertIn("LEGEND_PREFIX_TEXT", src)
        # ⚠ 比對**指派式**而非裸字串：註解裡會提到舊值（說明改了什麼），
        # 搜整份原始碼會被自己的註解騙——本檔上一版就假失敗過一次。
        self.assertNotIn("legend_x = margin_l + 200", src, "還在用寫死的起點")


class QuadrantAspectTests(unittest.TestCase):
    """H-8 留白：象限板畫布要接近圖框比例，否則等比縮放後上下空掉兩成。"""

    def _svg(self, tmpdir, n_topics=3):
        from pathlib import Path

        rows = [{"topic_code": f"T{i:03d}", "label": f"主題{i}", "patent_count": 10 - i,
                 "applicant_count": 5 - (i % 3), "leading_applicant_count": i % 3,
                 "leading_applicants_involved": []} for i in range(n_topics)]
        data = {"rows": rows, "patent_count_median": 6.0, "applicant_count_median": 3.0}
        path = Path(tmpdir) / "q.svg"
        cr.render_opportunity_quadrant_svg(path, "機會四象限", data)
        return path.read_text(encoding="utf-8")

    def test_canvas_aspect_close_to_frame(self):
        import re
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            svg = self._svg(tmp)
        m = re.search(r'<svg[^>]*width="(\d+)"[^>]*height="(\d+)"', svg)
        self.assertIsNotNone(m)
        w, h = int(m.group(1)), int(m.group(2))
        aspect = w / h
        self.assertLessEqual(aspect, cr.QUADRANT_TARGET_ASPECT + 0.15,
                             f"畫布比例 {aspect:.2f} 仍比圖框扁太多，縮放後上下會留白")

    def test_target_aspect_is_named(self):
        self.assertTrue(hasattr(cr, "QUADRANT_TARGET_ASPECT"))


class OverlappingPointsMergeTests(unittest.TestCase):
    """I-4：座標相同的資料點要**合併成一個標籤**（2026-08-03）。

    🔴 前一輪（H-5）我把候選方位從 4 個加到兩圈 8 個、又加了最小間距，
    實機**完全沒改善**。量測後才發現：演算法判定「無重疊」是**對的**——
    `2021` 與 `2011` 的框相距 71px，根本沒相交。

    真正的原因是**資料點本身重疊**：實機 lifecycle 圖 14 個點中，
    **5 個完全落在同一座標**（172,360）、另 2 個落在（254,314）。
    那 5 個年份都是「1 家、1 件」——它們就是同一個點。

    ⚠ 點重疊時，標籤怎麼避讓都沒用：讀者看到兩個並排的年份，
    無法判斷哪個屬於哪個點——**因為它們屬於同一個點**。
    調間距、加方位都是在解錯的問題（我連續兩輪都在解錯的問題）。

    改為**合併**：同一座標的年份共用一個標籤（`2011、2017、2021…`）。
    這是換呈現方式，不是調參數。
    """

    def test_identical_points_share_one_label(self):
        items = [(100.0, 100.0, "2011"), (100.0, 100.0, "2017"), (100.0, 100.0, "2021")]
        merged = cr.merge_colocated_labels(items)
        self.assertEqual(len(merged), 1, "同一座標的三個年份沒有合併")
        self.assertEqual(merged[0][2], "2011、2017、2021")

    def test_distinct_points_kept_separate(self):
        items = [(100.0, 100.0, "2011"), (300.0, 200.0, "2022")]
        merged = cr.merge_colocated_labels(items)
        self.assertEqual(len(merged), 2, "不同座標被誤併了")

    def test_near_points_merge_within_tolerance(self):
        """⚠ 幾乎重疊也要併：差 1–2px 在畫面上與完全重疊沒有分別。"""
        items = [(100.0, 100.0, "2011"), (101.0, 100.5, "2017")]
        merged = cr.merge_colocated_labels(items)
        self.assertEqual(len(merged), 1)

    def test_order_is_stable(self):
        """合併後的年份要照原順序，不得因 set 而每次不同。"""
        items = [(50.0, 50.0, "2019"), (50.0, 50.0, "2013"), (50.0, 50.0, "2026")]
        self.assertEqual(cr.merge_colocated_labels(items)[0][2], "2019、2013、2026")

    def test_tolerance_is_named(self):
        self.assertTrue(hasattr(cr, "COLOCATED_TOLERANCE_PX"))


class LegendLayoutSingleSourceTests(unittest.TestCase):
    """I-6：所有圖例的起點與間距都要**算**，不得寫死（2026-08-03）。

    🔴 使用者實機確認「混到了」：`件數色階■ 低 1` —— 色塊壓在「低」字上。

    ⚠ H-8 那輪我只修了**象限板**的圖例，泡泡矩陣（p5／p15）與年度矩陣（p16）
    是另外兩支渲染函式、各自寫死 `legend_x = 82` 與 `+= 132`，沒被一起改。
    「件數色階」四個字在 x=16、實際約 72px 寬 → 右緣已到 88 > 82，必壓。

    🔴 這是同型問題第三處。本輪**一次找齊三支**，全部改走 `legend_start_x()`
    與 `legend_step()`，日後改文案不必再記得改數字。
    """

    def test_helpers_exist(self):
        self.assertTrue(hasattr(cr, "legend_start_x"))
        self.assertTrue(hasattr(cr, "legend_step"))

    def test_start_clears_the_prefix_text(self):
        """起點必須跨過前綴文字的右緣。"""
        start = cr.legend_start_x(16, "件數色階")
        self.assertGreater(start, 16 + cr._text_px("件數色階"),
                           "圖例起點沒有跨過前綴文字，色塊會壓在字上")

    def test_step_covers_widest_item(self):
        """推進量要涵蓋色塊＋文字＋間距。"""
        for label in ("低 1–2", "最高 9–11", "龍頭涉入≥2家"):
            with self.subTest(label=label):
                self.assertGreater(cr.legend_step(label, mark_width=12),
                                   12 + cr._text_px(label))

    def test_no_hardcoded_legend_x(self):
        """三支渲染函式都不得再寫死 legend_x 起點。"""
        import inspect

        for fn in (cr.render_matrix_chart, cr.render_year_bubble_matrix_chart,
                   cr.render_opportunity_quadrant_svg):
            src = inspect.getsource(fn)
            with self.subTest(fn=fn.__name__):
                self.assertNotIn("legend_x = 82", src, "仍寫死起點 82")
                self.assertNotIn("legend_x += 132", src, "仍寫死步進 132")


if __name__ == "__main__":
    unittest.main()
