"""可重用內容元件（P1 tasks 2.3–2.4）：Key Player profiles ＋ 讀圖須知。

⚠ 範圍：本輪只做**資料元件**（deterministic 計算，供報表與日後
goal-driven SlidePlan 共同消費）；頁面編排屬 P2，不在此。

Key Player 定案（2026-08-05）：
- 取前 10 大申請人（本案第 11 名起皆 1 件，正好切在件數 ≥2）
- 軌跡判準＝**不同申請年 ≥3 個**（不是最晚年−最早年 ≥3：軌跡要的是時點數）
- 共同申請要點出，且拆「共同」與「各自獨立」
"""
from __future__ import annotations

import unittest

from backend.app.reports import content_blocks as cb


# (申請人, 專利id, 申請年)；曾晴與帝瑪斯共同申請 2 件（同 patent_id 兩列）。
ROWS = [
    {"applicant_display_name": "曾晴", "patent_id": 1, "application_year": 2020},
    {"applicant_display_name": "廈門帝瑪斯健康科技", "patent_id": 1, "application_year": 2020},
    {"applicant_display_name": "曾晴", "patent_id": 2, "application_year": 2022},
    {"applicant_display_name": "廈門帝瑪斯健康科技", "patent_id": 2, "application_year": 2022},
    {"applicant_display_name": "曾晴", "patent_id": 3, "application_year": 2024},
    {"applicant_display_name": "祺驊", "patent_id": 4, "application_year": 2022},
    {"applicant_display_name": "祺驊", "patent_id": 5, "application_year": 2022},
    {"applicant_display_name": "孟喬", "patent_id": 6, "application_year": 2013},
    {"applicant_display_name": "孟喬", "patent_id": 7, "application_year": 2015},
    {"applicant_display_name": "孟喬", "patent_id": 8, "application_year": 2016},
]


class KeyPlayerTests(unittest.TestCase):
    def _by_name(self):
        return {p["applicant"]: p for p in cb.key_player_profiles(ROWS)}

    def test_ranked_by_patent_count(self):
        profiles = cb.key_player_profiles(ROWS)
        # 曾晴與孟喬同為 3 件 → 同件數以名稱排序（碼位序：孟 < 曾）。
        self.assertEqual([p["applicant"] for p in profiles][:2], ["孟喬", "曾晴"])
        self.assertEqual(profiles[0]["patent_count"], 3)

    def test_trajectory_needs_three_distinct_years(self):
        """判準是時點數，不是跨度——祺驊 2 件同年＝無軌跡。"""
        by = self._by_name()
        self.assertTrue(by["曾晴"]["has_trajectory"])
        self.assertEqual(by["曾晴"]["years"], [2020, 2022, 2024])
        self.assertTrue(by["孟喬"]["has_trajectory"])
        self.assertFalse(by["祺驊"]["has_trajectory"])
        self.assertEqual(by["祺驊"]["years"], [2022])

    def test_joint_and_solo_split(self):
        """共同申請要點出，且拆共同與各自獨立（2026-08-05 定案）。"""
        by = self._by_name()
        zeng = by["曾晴"]
        self.assertEqual(zeng["joint_count"], 2)
        self.assertEqual(zeng["solo_count"], 1)
        self.assertEqual(zeng["joint_with"], [{"applicant": "廈門帝瑪斯健康科技", "count": 2}])
        self.assertEqual(by["祺驊"]["joint_count"], 0)
        self.assertEqual(by["祺驊"]["joint_with"], [])

    def test_limit_to_top_ten(self):
        rows = [{"applicant_display_name": f"A{i}", "patent_id": 100 + i,
                 "application_year": 2020} for i in range(15)]
        self.assertEqual(len(cb.key_player_profiles(rows)), 10)

    def test_grouping_by_trajectory(self):
        groups = cb.key_player_groups(ROWS)
        self.assertEqual([p["applicant"] for p in groups["trajectory"]], ["孟喬", "曾晴"])
        self.assertEqual([p["applicant"] for p in groups["technical"]],
                         ["廈門帝瑪斯健康科技", "祺驊"])

    def test_empty_rows_no_crash(self):
        self.assertEqual(cb.key_player_profiles([]), [])
        self.assertEqual(cb.key_player_groups([]), {"trajectory": [], "technical": []})


class ReaderGuideTests(unittest.TestCase):
    def test_guide_covers_units_and_caveats(self):
        blocks = cb.reader_guide_blocks()
        text = " ".join(b["title"] + b["body"] for b in blocks)
        for token in ("件", "群", "同族", "共同申請"):
            self.assertIn(token, text, f"讀圖須知缺「{token}」說明")

    def test_guide_is_data_independent(self):
        """讀圖須知是固定說明，不吃 rows——避免與各頁註記重複維護。"""
        import inspect

        self.assertEqual(len(inspect.signature(cb.reader_guide_blocks).parameters), 0)


if __name__ == "__main__":
    unittest.main()
