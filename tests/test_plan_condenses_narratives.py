"""SlidePlan 必須從既有解讀濃縮，不得重新編寫（2026-08-10 使用者提問促成）。

使用者問：「怎確保 PPT 內容是用 ai:report_plan 的內容濃縮出來的，但又不能為了
濃縮而把資訊丟掉？」

查證後發現前半段成立、後半段有洞：

| 階段 | 做了什麼 | 產出去哪 |
|---|---|---|
| `ai:narrative`（跑約 20 分鐘，經 MCP 查 DB 取證） | 15 個 variants 的深度解讀，具名到申請人與機構層 | **沒人讀** |
| `ai:report_plan` | 只拿到選圖的 image_path／data_rows／population_note，**從頭再寫一次**要點 | → PPT |

⚠ 這不是「濃縮時丟資訊」，是**根本沒在濃縮**——規劃 CLI 沒看過那些查證結果，
它能寫的深度只來自聚合數字，寫不出「法蘭擋板＋電磁鐵解鎖」這種要讀過專利內容
才寫得出來的機構層描述（`content_standard.md` 第 6 節的硬標準）。

正確關係：**narrative 是素材，SlidePlan 是從素材濃縮**。本測把它釘住。

⚠ 字數限制那一層本來就沒問題：`validate_slide_plan` 對超長要點是**報錯讓整份
規劃不合格**，不是默默截斷，所以不會發生「寫了卻被吃掉」。
"""
from __future__ import annotations

import unittest

from backend.app.reports.planning_defaults import build_brief
from backend.app.worker.report_planning_runner import build_prompt

NARRATIVES = {
    "based_on_version": "v1",
    "reports": {
        "applicant_strength_profile": {
            "variants": {
                "default": {
                    "headline": "曾晴與帝瑪斯同一集團領先",
                    "points": [
                        {"text": "2024 電機自鎖：法蘭擋板＋電磁鐵解鎖，5 件同架構"},
                        {"text": "扭矩單線探索，4 件 1 族 4 國"},
                    ],
                }
            }
        }
    },
}

BUNDLE = [{
    "chart_identity": "applicant_strength_profile:default",
    "title": "Key Players 競爭定位",
    "image_path": "kp.svg",
    "data_rows": [{"applicant_display_name": "曾晴", "patent_count": 9}],
    "population_note": "",
    "version": "v1",
    "checksum": "abc",
}]


def _brief(**kwargs):
    return build_brief(snapshot_id="v1", workspace_id=3, selected_charts=BUNDLE,
                       north_star_goal="找空白區", audience="研發主管",
                       page_budget=11, **kwargs)


class PlanCondensesNarrativesTests(unittest.TestCase):
    """既有解讀要進 prompt，且規則要講明「濃縮不是重寫」。"""

    def test_brief_carries_narratives(self):
        """brief 要帶得動既有解讀——沒有它，規劃端無從濃縮。"""
        brief = _brief(narratives=NARRATIVES)
        self.assertIn("narratives", brief)

    def test_prompt_includes_existing_narratives(self):
        """深度內容必須出現在 prompt 裡，否則 CLI 看不到就只能重寫。"""
        prompt = build_prompt(_brief(narratives=NARRATIVES))
        self.assertIn("法蘭擋板", prompt,
                      "既有解讀的機構層細節要餵進規劃 prompt")
        self.assertIn("曾晴與帝瑪斯同一集團領先", prompt)

    def test_prompt_states_condense_not_rewrite(self):
        """規則要明說：從解讀濃縮、不得丟掉具名對象與機構層細節。"""
        prompt = build_prompt(_brief(narratives=NARRATIVES))
        self.assertIn("濃縮", prompt)
        self.assertIn("不是重寫", prompt)

    def test_prompt_works_without_narratives(self):
        """沒有解讀時照樣能規劃（向後相容，且解讀確實可能還沒產）。"""
        prompt = build_prompt(_brief())
        self.assertTrue(prompt)
        self.assertNotIn("法蘭擋板", prompt)


if __name__ == "__main__":
    unittest.main()
