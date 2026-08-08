"""P2 第 5 節：deterministic builder 消費通過驗證的 SlidePlan（tasks 5.1–5.2）。

🔴 分工紅線：CLI 決定「哪一頁講什麼、用哪張圖、用哪種版型」；
builder 決定「那種版型長什麼樣」（座標／字級／顏色全由 theme 解析）。
兩者不得互相越界——本測試同時守兩邊。

⚠ 既有固定 PAGE_LAYOUT 路徑**保留**：沒有 plan 時照舊出頁（feature flag 語意），
不是把舊路徑拆掉換新的（拆掉就沒有回頭路，且既有報告會立刻不能重產）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills"
                       / "patent-report-ppt" / "scripts"))
import build_ppt as bp


PLAN = {
    "plan_id": "plan-1",
    "slides": [
        {"slide_id": "s1", "layout_preset": "chart_hero", "purpose": "競爭格局",
         "title": "競爭格局：前二名同屬一個布局陣營",
         "chart_identities": ["applicant_ranking:default"],
         "narrative": [{"text": "曾晴 14 件居首", "evidence_ref": "e1"}]},
        {"slide_id": "s2", "layout_preset": "kp_quadrant", "purpose": "定位象限",
         "title": "Key Players 競爭定位",
         "chart_identities": ["applicant_strength_profile:default"],
         "narrative": [{"text": "扭矩 4 國 1 主題", "evidence_ref": "e2"}]},
    ],
}


class PlanToPageSpecTests(unittest.TestCase):
    def test_plan_becomes_page_specs_in_order(self):
        specs = bp.page_specs_from_plan(PLAN)
        self.assertEqual([s.kind for s in specs], ["chart_hero", "kp_quadrant"])
        self.assertEqual([s.page for s in specs], [1, 2])

    def test_titles_and_charts_carried(self):
        specs = bp.page_specs_from_plan(PLAN)
        self.assertIn("競爭格局", specs[0].title)
        self.assertEqual(specs[1].report_keys, ("applicant_strength_profile",))

    def test_unknown_preset_rejected(self):
        bad = {"plan_id": "p", "slides": [
            {"slide_id": "s1", "layout_preset": "自創版型", "purpose": "x",
             "chart_identities": [], "narrative": []}]}
        with self.assertRaises(bp.SlidePlanError):
            bp.page_specs_from_plan(bad)

    def test_geometry_from_plan_is_ignored_not_applied(self):
        """CLI 若硬塞座標，builder 一律忽略——版面只由 theme 決定。"""
        plan = {"plan_id": "p", "slides": [dict(PLAN["slides"][0], left_in=99, font_pt=99)]}
        specs = bp.page_specs_from_plan(plan)
        self.assertFalse(hasattr(specs[0], "left_in"))
        self.assertFalse(hasattr(specs[0], "font_pt"))

    def test_deterministic_rebuild(self):
        """同一份 plan 產出同一組 spec（可重現）。"""
        first = [(s.page, s.kind, s.title) for s in bp.page_specs_from_plan(PLAN)]
        second = [(s.page, s.kind, s.title) for s in bp.page_specs_from_plan(PLAN)]
        self.assertEqual(first, second)


class LegacyPathTests(unittest.TestCase):
    def test_fixed_layout_still_available(self):
        """沒有 plan 時照舊走固定 PAGE_LAYOUT——不拆掉舊路徑。"""
        self.assertTrue(bp.PAGE_LAYOUT)
        self.assertTrue(callable(bp._expand_page_layout))

    def test_plan_path_is_opt_in(self):
        import inspect

        src = inspect.getsource(bp.resolve_layout)   # 組版主入口
        self.assertIn("plan", src)
        self.assertIn("_expand_page_layout", src, "缺 plan 時要能退回既有展開路徑")


class CoverageManifestTests(unittest.TestCase):
    def test_manifest_records_plan_and_coverage(self):
        manifest = bp.plan_coverage_manifest(
            PLAN, selected_identities={"applicant_ranking:default",
                                       "applicant_strength_profile:default"})
        self.assertEqual(manifest["plan_id"], "plan-1")
        self.assertEqual(manifest["missing_selected"], [])
        self.assertEqual(manifest["slide_count"], 2)

    def test_manifest_flags_missing_selected_chart(self):
        manifest = bp.plan_coverage_manifest(
            PLAN, selected_identities={"applicant_ranking:default", "lifecycle:default"})
        self.assertEqual(manifest["missing_selected"], ["lifecycle:default"])


if __name__ == "__main__":
    unittest.main()
