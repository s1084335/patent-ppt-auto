"""M-1～M-5（2026-08-04 清值重跑監控發現）的 regression 測試。

- M-1 narratives 檔 prompt_version 失真：skill 契約範例寫死 v7、CLI 照抄。
  治本＝runner 在驗證/上傳前**蓋章**（metadata 由系統回填，不信 CLI 抄的值）。
- M-2 L4 超限：容量 97 字進 prompt 未夾全域 55、validator 卻夾——兩端不同步，
  CLI 守了 prompt 還是紅。治本＝夾限收成單一 helper 兩端共用。
- M-3 鎖八誤報：分類代碼（A63B-005）的數字被當統計數字、同 point 內重複自指。
  治本＝統計數字抽取排除代碼型 token、僅跨 point 算重複。
- M-5 manifest 誤報：family_quality_detail 是「刻意不進 PPT」（07-31 定案），
  missing_reports 卻把它列成缺料。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from backend.app.worker.ai_narrative_runner import (
    NARRATIVE_POINT_TEXT_MAX,
    PROMPT_VERSION,
    build_prompt,
    effective_max_chars,
    stamp_narrative_metadata,
    validate_narrative_contract,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt" / "scripts"))


def _doc(points, text="現況段。意涵段。後續段。"):
    return {
        "reports": {"annual_trend": {"variants": {"default": {
            "headline": "測試標題", "points": points, "text": text,
        }}}}
    }


# ⚠ M1StampTests 已隨 PPT 交付線移除（2026-08-10，remove-ppt-delivery-line）。


class M2ClampTests(unittest.TestCase):
    def test_effective_max_chars_clamps_to_global(self):
        self.assertEqual(effective_max_chars({"max_chars": 97}), NARRATIVE_POINT_TEXT_MAX)
        self.assertEqual(effective_max_chars({"max_chars": 33}), 33)
        self.assertEqual(effective_max_chars({}), NARRATIVE_POINT_TEXT_MAX)

    def test_prompt_capacity_note_is_clamped(self):
        """prompt 告知的每條字數不得超過全域上限——否則 CLI 守了 prompt 仍被驗紅。"""
        # 直接以 capacity 打樁組 prompt：97 字容量的頁在說明裡必須顯示 55。
        from unittest import mock
        with mock.patch("backend.app.worker.ai_narrative_runner.load_narrative_capacity",
                        return_value={"ipc_main_distribution:L4": {"max_points": 3, "max_chars": 97}}):
            text = build_prompt(Path("X:/nonexist"), "v")
        self.assertNotIn("≤97 字", text)
        self.assertIn("≤55 字", text)


class M3StatNumberTests(unittest.TestCase):
    def test_classification_codes_are_not_stat_numbers(self):
        """A63B-005／F03G-005 的數字片段不得觸發鎖八。"""
        doc = _doc([
            {"label": "現況", "text": "A63B-005共15件"},
            {"label": "意涵", "text": "集中於F03G-005一類"},
            {"label": "後續", "text": "建議進一步檢視A63B-005細分類"},
        ], text="A63B-005共15件。集中於F03G-005一類。建議進一步檢視A63B-005細分類。")
        warnings = validate_narrative_contract(doc)
        self.assertFalse(any("重複" in w for w in warnings), warnings)

    def test_same_point_repetition_not_self_reported(self):
        """同一 point 內同數字出現兩次不算跨段重複（不再自指 points[0]→points[0]）。"""
        doc = _doc([
            {"label": "現況", "text": "2022年15件，其中15件為發明"},
            {"label": "意涵", "text": "布局集中"},
            {"label": "後續", "text": "建議進一步檢視權利範圍"},
        ], text="2022年15件，其中15件為發明。布局集中。建議進一步檢視權利範圍。")
        warnings = validate_narrative_contract(doc)
        self.assertFalse(any("重複" in w for w in warnings), warnings)

    def test_cross_point_stat_repetition_still_warns(self):
        doc = _doc([
            {"label": "現況", "text": "2022年申請15件"},
            {"label": "意涵", "text": "15件集中於單一申請人"},
            {"label": "後續", "text": "建議進一步檢視權利範圍"},
        ], text="2022年申請15件。15件集中於單一申請人。建議進一步檢視權利範圍。")
        warnings = validate_narrative_contract(doc)
        self.assertTrue(any("重複" in w and "15" in w for w in warnings), warnings)


# ⚠ M5MissingReportsTests 已隨 PPT 交付線移除（2026-08-10，remove-ppt-delivery-line）。


if __name__ == "__main__":
    unittest.main()
