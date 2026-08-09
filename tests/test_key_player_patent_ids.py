"""Key Player profile 要帶該家全部專利 id（2.3 第 3 項）。

## 為什麼要有這個欄位

2026-08-10 使用者定案：代表專利**每家全取**，且**不得只給標題**——要有技術內容
摘要，讓人看得出這件專利在做什麼（同 2026-08-07 定案⑥「解讀深度下沉到專利
內容層」）。

摘要由 CLI 自行透過 MCP `query_database` 讀 `patents."文獻備註"` 產生，
**不在資料層預先算**。所以資料層的責任只有一個：告訴 CLI「這家有哪些專利」。

⚠ 清單順序必須固定：同一批資料兩次產出要給 CLI 同一份清單，否則 prompt 變動
會讓 AI 產出無謂地不一致。
"""
from __future__ import annotations

import unittest

from backend.app.reports.content_blocks import key_player_profiles


ROWS = [
    {"applicant_display_name": "A公司", "patent_id": 3, "application_year": 2020},
    {"applicant_display_name": "A公司", "patent_id": 1, "application_year": 2021},
    {"applicant_display_name": "B公司", "patent_id": 2, "application_year": 2022},
    # 共同申請：同一件掛兩家
    {"applicant_display_name": "A公司", "patent_id": 9, "application_year": 2023},
    {"applicant_display_name": "B公司", "patent_id": 9, "application_year": 2023},
]


class PatentIdsTests(unittest.TestCase):
    def _by_name(self):
        return {p["applicant"]: p for p in key_player_profiles(ROWS)}

    def test_every_profile_carries_its_patent_ids(self):
        profiles = self._by_name()
        self.assertEqual(profiles["A公司"]["patent_ids"], [1, 3, 9])
        self.assertEqual(profiles["B公司"]["patent_ids"], [2, 9])

    def test_ids_are_sorted_for_reproducibility(self):
        """⚠ 順序固定——同一批資料兩次產出要給 CLI 同一份清單。"""
        for profile in self._by_name().values():
            self.assertEqual(profile["patent_ids"], sorted(profile["patent_ids"]))

    def test_count_matches_patent_count(self):
        """⚠ 清單長度必須等於件數，否則 CLI 會漏查或多查。"""
        for profile in self._by_name().values():
            self.assertEqual(len(profile["patent_ids"]), profile["patent_count"])

    def test_joint_patent_appears_for_both(self):
        """共同申請的專利兩家都要有——⚠ 不合併、不擇一（沿既有共同申請定案）。"""
        profiles = self._by_name()
        self.assertIn(9, profiles["A公司"]["patent_ids"])
        self.assertIn(9, profiles["B公司"]["patent_ids"])


if __name__ == "__main__":
    unittest.main()
