"""濃縮五規則的兩個自動鎖（2026-08-04 使用者定案「不走硬性字數路線」）。

質性規則（刪句測試、圖面資訊不轉述、動詞收斂）進 prompt 與 skill；
可程式判定的兩條做成 contract 鎖：
- 鎖八·同頁數字不重複：意涵／後續複述現況的數字＝同一份資訊佔兩份版面。
- 鎖九·填充詞禁詞：進行、相關、方面、值得注意的是、此外、同時、整體而言
  ——這些詞刪掉後句意不變，出現即代表還有濃縮空間。
"""
from __future__ import annotations

import unittest

from backend.app.worker.ai_narrative_runner import (
    NARRATIVE_FILLER_WORDS,
    validate_narrative_contract,
)


def _wrap(points, text="現況段。意涵段。後續段。"):
    return {
        "reports": {
            "annual_trend": {
                "variants": {
                    "default": {
                        "headline": "測試標題",
                        "points": points,
                        "text": text,
                    }
                }
            }
        }
    }


class DuplicateNumberLockTests(unittest.TestCase):
    def test_number_repeated_across_points_warns(self):
        """意涵複述現況的 15 件＝同一份資訊佔兩份版面 → 要警告。"""
        doc = _wrap([
            {"label": "現況", "text": "2022年申請15件"},
            {"label": "意涵", "text": "15件集中於單一申請人"},
            {"label": "後續", "text": "建議進一步檢視權利範圍"},
        ], text="2022年申請15件。15件集中於單一申請人。建議進一步檢視權利範圍。")
        warnings = validate_narrative_contract(doc)
        self.assertTrue(any("重複" in w and "15" in w for w in warnings), warnings)

    def test_distinct_numbers_do_not_warn(self):
        doc = _wrap([
            {"label": "現況", "text": "2022年申請15件"},
            {"label": "意涵", "text": "布局集中於少數申請人"},
            {"label": "後續", "text": "建議進一步檢視權利範圍"},
        ], text="2022年申請15件。布局集中於少數申請人。建議進一步檢視權利範圍。")
        warnings = validate_narrative_contract(doc)
        self.assertFalse(any("重複" in w for w in warnings), warnings)


class FillerWordLockTests(unittest.TestCase):
    def test_filler_word_warns(self):
        doc = _wrap([
            {"label": "現況", "text": "整體而言2022年申請15件"},
            {"label": "意涵", "text": "布局集中"},
            {"label": "後續", "text": "建議進一步檢視權利範圍"},
        ], text="整體而言2022年申請15件。布局集中。建議進一步檢視權利範圍。")
        warnings = validate_narrative_contract(doc)
        self.assertTrue(any("填充詞" in w and "整體而言" in w for w in warnings), warnings)

    def test_filler_list_is_locked(self):
        """禁詞清單是使用者定案的七個——增刪要先過使用者。"""
        self.assertEqual(
            set(NARRATIVE_FILLER_WORDS),
            {"進行", "相關", "方面", "值得注意的是", "此外", "同時", "整體而言"})

    def test_clean_points_do_not_warn(self):
        doc = _wrap([
            {"label": "現況", "text": "2022年申請15件"},
            {"label": "意涵", "text": "布局集中於少數申請人"},
            {"label": "後續", "text": "建議進一步檢視權利範圍"},
        ], text="2022年申請15件。布局集中於少數申請人。建議進一步檢視權利範圍。")
        warnings = validate_narrative_contract(doc)
        self.assertFalse(any("填充詞" in w for w in warnings), warnings)


if __name__ == "__main__":
    unittest.main()
