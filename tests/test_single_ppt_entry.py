"""PPT 單一入口＋預設策略（EXP-018，2026-08-09 使用者定案）。

「ppt 入口要統一一個，使用者有需求就以需求為重心，沒需求也要能跑出符合
我給你的兩個範例的專業程度」。

⚠ 未填目標 **不等於** 退回固定頁序——那是規劃失敗時的保底。未填目標時
系統用預設策略規劃，品質不打折。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.app.reports import planning_defaults as pd


class DefaultBriefTests(unittest.TestCase):
    def test_default_goal_when_user_gives_none(self):
        brief = pd.build_brief(north_star_goal="", selected_charts=[{"chart_identity": "a:default"}],
                               snapshot_id="v1", workspace_id=3)
        self.assertTrue(brief["north_star_goal"].strip(), "未填目標時要有預設目標，不得留空")
        self.assertTrue(brief["used_default_goal"])

    def test_user_goal_wins(self):
        brief = pd.build_brief(north_star_goal="找出可切入的技術空白",
                               selected_charts=[{"chart_identity": "a:default"}],
                               snapshot_id="v1", workspace_id=3)
        self.assertEqual(brief["north_star_goal"], "找出可切入的技術空白")
        self.assertFalse(brief["used_default_goal"])

    def test_default_goal_states_reference_quality_bar(self):
        """預設目標要指向範例的專業度，否則 CLI 不知道標準在哪。"""
        goal = pd.DEFAULT_NORTH_STAR_GOAL
        for token in ("布局", "競爭", "切入"):
            self.assertIn(token, goal, f"預設目標缺「{token}」面向")

    def test_default_directions_cover_reference_storyline(self):
        """預設敘事鏈＝兩份範例的共同 DNA（結論先行→證據→KP→判讀說明）。"""
        directions = pd.DEFAULT_DIRECTIONS
        joined = " ".join(directions)
        for token in ("結論", "Key Player", "判讀"):
            self.assertIn(token, joined, f"預設敘事鏈缺「{token}」")


class PromptQualityBarTests(unittest.TestCase):
    def test_prompt_carries_quality_bar_when_default(self):
        """走預設策略時，prompt 必須把品質標準寫進去（不是只給空目標）。"""
        from backend.app.worker.report_planning_runner import build_prompt

        brief = pd.build_brief(north_star_goal="", snapshot_id="v1", workspace_id=3,
                               selected_charts=[{
                                   "chart_identity": "applicant_ranking:default",
                                   "title": "主要申請人排名", "image_path": "/x.svg",
                                   "data_rows": [{"a": 1}], "population_note": "母體 55/55",
                                   "version": "v1", "checksum": "c"}])
        prompt = build_prompt(brief)
        self.assertIn("結論", prompt)
        self.assertIn("Key Player", prompt)
        self.assertIn(pd.DEFAULT_NORTH_STAR_GOAL[:8], prompt)


class FrontendSingleEntryTests(unittest.TestCase):
    HTML = (Path(__file__).resolve().parents[1] / "backend" / "app" / "static"
            / "index.html").read_text(encoding="utf-8")

    def test_all_ppt_buttons_share_one_action(self):
        """單一入口＝所有「產生 PPT」按鈕走**同一個動作**。

        ⚠ 按鈕可以有兩顆（工具列一顆、空狀態提示一顆），但不得是兩條路徑
        （例如一顆走固定架構、一顆走依目標規劃）——後者是把選路徑推給使用者。
        """
        import re

        handlers = set(re.findall(r'onclick="(\w+)\(\)">產生 PPT<', self.HTML))
        self.assertEqual(handlers, {"requestExportPpt"},
                         f"產生 PPT 有多個不同動作：{handlers}")

    def test_goal_input_is_optional_and_inline(self):
        """目標輸入在主流程上（非收合面板），且標示為選填。"""
        self.assertIn('id="ppt-goal-input"', self.HTML)
        self.assertIn("選填", self.HTML)

    def test_no_separate_planning_panel(self):
        """不得再有獨立的「目標驅動規劃」收合面板（兩個入口＝把選路徑推給使用者）。"""
        self.assertNotIn('id="goal-plan-panel"', self.HTML)


class FallbackDisclosureTests(unittest.TestCase):
    def test_fallback_is_disclosed_not_silent(self):
        """規劃失敗才走固定頁序，且必須標示——不得靜默降級。"""
        from backend.app.reports import planning_defaults as p

        result = p.describe_fallback("CLI 回覆非 JSON")
        self.assertIn("未依規劃", result["note"])
        self.assertIn("CLI 回覆非 JSON", result["reason"])


if __name__ == "__main__":
    unittest.main()
