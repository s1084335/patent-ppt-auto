"""#3／#3b 系統化測試案例（等價類劃分／邊界值分析／決策表）。

依 2026-08-05 驗證方法要求補齊：既有測試驗的是「正常流程能不能動」，
本檔驗的是**輸入空間有沒有被切乾淨**——等價類各取代表值、邊界值逐一踩、
多條件組合逐格驗證。核心被驗對象是三個純函式（無 I/O，可完全決定性驗證）：

- `ranking_segments`：件數 → 兩段長度與各段斜紋長度
- `ranking_note`：列下註記字串組裝

（`topic_version_warnings` 的決策表已隨 PPT 交付線移除，2026-08-10。）
"""
from __future__ import annotations

import unittest

from backend.app.reports.chart_runner import ranking_note, ranking_segments


class SegmentsEquivalencePartitionTests(unittest.TestCase):
    """等價類劃分：以 joint 佔 total 的比例切成四類，各取代表值。

    類別 A 無共同（joint=0）／B 部分共同（0<joint<total）／C 全共同（joint=total）／
    D 非法（joint>total，資料異常）。四類的期望行為不同，必須各驗一次。
    """

    def test_class_a_no_joint(self):
        seg = ranking_segments({"patent_count": 10, "joint_count": 0})
        self.assertEqual((seg["solo"], seg["joint"]), (10, 0))

    def test_class_b_partial_joint(self):
        seg = ranking_segments({"patent_count": 10, "joint_count": 4})
        self.assertEqual((seg["solo"], seg["joint"]), (6, 4))

    def test_class_c_all_joint(self):
        seg = ranking_segments({"patent_count": 10, "joint_count": 10})
        self.assertEqual((seg["solo"], seg["joint"]), (0, 10))

    def test_class_d_joint_exceeds_total_is_clamped(self):
        """非法輸入不得產生負長度——負寬度會讓 SVG 直接畫不出來。"""
        seg = ranking_segments({"patent_count": 10, "joint_count": 99})
        self.assertEqual((seg["solo"], seg["joint"]), (0, 10))
        self.assertGreaterEqual(seg["solo"], 0)


class SegmentsBoundaryValueTests(unittest.TestCase):
    """邊界值分析：0／1／total-1／total／total+1 逐點驗證。"""

    def test_total_zero(self):
        seg = ranking_segments({"patent_count": 0, "joint_count": 0})
        self.assertEqual((seg["solo"], seg["joint"]), (0, 0))

    def test_joint_one_below_total(self):
        seg = ranking_segments({"patent_count": 5, "joint_count": 4})
        self.assertEqual((seg["solo"], seg["joint"]), (1, 4))

    def test_joint_equals_total(self):
        seg = ranking_segments({"patent_count": 5, "joint_count": 5})
        self.assertEqual((seg["solo"], seg["joint"]), (0, 5))

    def test_joint_one_above_total(self):
        seg = ranking_segments({"patent_count": 5, "joint_count": 6})
        self.assertEqual((seg["solo"], seg["joint"]), (0, 5))

    def test_hatch_at_segment_edges(self):
        """斜紋在 0／等於段長／超過段長三個邊界的行為。"""
        row = {"patent_count": 6, "joint_count": 2}
        self.assertEqual(ranking_segments({**row, "solo_transferred_count": 0})["solo_hatch"], 0)
        self.assertEqual(ranking_segments({**row, "solo_transferred_count": 4})["solo_hatch"], 4)
        self.assertEqual(ranking_segments({**row, "solo_transferred_count": 5})["solo_hatch"], 4)
        self.assertEqual(ranking_segments({**row, "joint_transferred_count": 3})["joint_hatch"], 2)

    def test_negative_input_floored(self):
        """負數（不該出現，但 DB/JSON 仍可能給）一律夾到 0，不得畫反向長條。"""
        seg = ranking_segments({"patent_count": 5, "joint_count": -3,
                                "solo_transferred_count": -1})
        self.assertEqual(seg["joint"], 0)
        self.assertEqual(seg["solo_hatch"], 0)

    def test_none_and_missing_treated_as_zero(self):
        """None 與缺鍵是同一個等價類（舊報表兩種都會出現）。"""
        a = ranking_segments({"patent_count": 4, "joint_count": None})
        b = ranking_segments({"patent_count": 4})
        self.assertEqual(a, b)


class NoteDecisionTableTests(unittest.TestCase):
    """決策表：三個條件 × 期望輸出。

    C1 有共同者名單且 joint>0｜C2 有受讓人名單且 count>0｜C3 with_assignee 開關
    ——8 種組合逐格驗證（C3=False 時 C2 不影響結果，仍各驗一次以防漏接）。
    """

    CO = {"co_applicant_names": "甲", "joint_count": 2}
    AS = {"recent_assignee_display_names": "乙", "recent_assignee_count": 3}

    def test_row_1_both_on_with_assignee(self):
        note = ranking_note({**self.CO, **self.AS})
        self.assertEqual(note, "共同申請：甲 2件｜最新受讓人：乙 3件")

    def test_row_2_only_co(self):
        self.assertEqual(ranking_note(self.CO), "共同申請：甲 2件")

    def test_row_3_only_assignee(self):
        self.assertEqual(ranking_note(self.AS), "最新受讓人：乙 3件")

    def test_row_4_neither(self):
        self.assertEqual(ranking_note({}), "")

    def test_row_5_both_on_without_assignee(self):
        self.assertEqual(ranking_note({**self.CO, **self.AS}, with_assignee=False),
                         "共同申請：甲 2件")

    def test_row_6_only_assignee_without_flag(self):
        self.assertEqual(ranking_note(self.AS, with_assignee=False), "")

    def test_row_7_names_present_but_count_zero(self):
        """有名字、件數 0＝資料不一致，寧可不印也不要印出「甲 0件」。"""
        self.assertEqual(ranking_note({"co_applicant_names": "甲", "joint_count": 0}), "")
        self.assertEqual(
            ranking_note({"recent_assignee_display_names": "乙", "recent_assignee_count": 0}), "")

    def test_row_8_count_present_but_names_blank(self):
        self.assertEqual(ranking_note({"co_applicant_names": "", "joint_count": 2}), "")

    def test_owner_variant_uses_co_owner_names(self):
        """專利權人圖的欄名不同（co_owner_names），同一支函式要吃得到。"""
        note = ranking_note({"co_owner_names": "丙", "joint_count": 4},
                            co_label="共同持有人", with_assignee=False)
        self.assertEqual(note, "共同持有人：丙 4件")

    def test_multi_names_separator_normalised(self):
        """SQL 用 `; ` 串接，畫面統一用頓號——兩種分隔混在同一張圖上很雜。"""
        note = ranking_note({"co_applicant_names": "甲; 乙", "joint_count": 2},
                            with_assignee=False)
        self.assertEqual(note, "共同申請：甲、乙 2件")


if __name__ == "__main__":
    unittest.main()
