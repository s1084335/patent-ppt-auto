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


class RightsStrengthTests(unittest.TestCase):
    """Q10 權利強度四面向（2026-08-05 定案）：並列不合成分數。

    面向＝布局量（件／族／國）、法律穩定性（授權／失效）、專利種類三分。
    🔴 合成分數已否決：權重是主觀選擇，「權利強度 82 分」會被當成客觀指標，
    且會壓掉「4 件 1 家族卻布局 4 國」這種形狀資訊。
    ⚠ 不做請求項數（範例刻意不放，易被誤讀為專利品質）。
    """

    ROWS = [
        {"applicant_display_name": "帝瑪斯", "patent_id": 1, "country_code": "CN",
         "family_id": "F1", "legal_status": "授权", "patent_type": "P", "document_kind": "A"},
        {"applicant_display_name": "帝瑪斯", "patent_id": 2, "country_code": "TW",
         "family_id": "F1", "legal_status": "已核准", "patent_type": "U", "document_kind": "U"},
        {"applicant_display_name": "帝瑪斯", "patent_id": 3, "country_code": "US",
         "family_id": "F2", "legal_status": "到期(Expiration of the term)",
         "patent_type": "P", "document_kind": "S"},
        {"applicant_display_name": "扭矩", "patent_id": 4, "country_code": "US",
         "family_id": "F3", "legal_status": "审查中", "patent_type": "P", "document_kind": "A"},
        {"applicant_display_name": "扭矩", "patent_id": 5, "country_code": "EP",
         "family_id": "F3", "legal_status": "审查中", "patent_type": "P", "document_kind": "A"},
    ]

    def _by_name(self):
        return {p["applicant"]: p for p in cb.rights_strength_profiles(self.ROWS)}

    def test_layout_dimensions(self):
        """件／族／國三個數要分開——族少國多正是「地域防禦廣」的形狀。"""
        by = self._by_name()
        self.assertEqual((by["帝瑪斯"]["patent_count"], by["帝瑪斯"]["family_count"],
                          by["帝瑪斯"]["country_count"]), (3, 2, 3))
        self.assertEqual((by["扭矩"]["patent_count"], by["扭矩"]["family_count"],
                          by["扭矩"]["country_count"]), (2, 1, 2))

    def test_legal_dimension_uses_status_buckets(self):
        """授權／失效走狀態桶唯一定義處，不自行比對字面。"""
        by = self._by_name()
        self.assertEqual(by["帝瑪斯"]["granted_count"], 2)
        self.assertEqual(by["帝瑪斯"]["dead_count"], 1)
        self.assertEqual(by["扭矩"]["granted_count"], 0)
        self.assertEqual(by["扭矩"]["pending_count"], 2)

    def test_kind_dimension_three_way(self):
        by = self._by_name()
        self.assertEqual(by["帝瑪斯"]["kind_counts"], {"發明": 1, "新型": 1, "設計": 1})

    def test_no_composite_score(self):
        """🔴 合成分數已否決——profile 不得出現任何總分欄。"""
        for profile in cb.rights_strength_profiles(self.ROWS):
            for banned in ("score", "strength_score", "total_score", "rank_score"):
                self.assertNotIn(banned, profile)

    def test_empty_rows_no_crash(self):
        self.assertEqual(cb.rights_strength_profiles([]), [])


class TechBreadthTests(unittest.TestCase):
    """技術廣度（問題 10 原始需求四項之一，2026-08-07 補做）。

    ⚠ 原始需求寫的是「布局強度＋技術廣度＋法律穩定性＋權利範圍」，
    先前實作漏了技術廣度（權利範圍另於 08-05 定案否決）。
    廣度＝該申請人涉入幾個技術主題／幾個 IPC subclass——件數再多都集中
    在一個主題，壁壘與跨三個主題完全不同。
    """

    ROWS = [
        {"applicant_display_name": "帝瑪斯", "patent_id": 1, "country_code": "CN",
         "family_id": "F1", "legal_status": "授权", "patent_type": "P",
         "document_kind": "A", "topic_key": "T001", "ipc_subclass": "A63B"},
        {"applicant_display_name": "帝瑪斯", "patent_id": 2, "country_code": "TW",
         "family_id": "F1", "legal_status": "授权", "patent_type": "U",
         "document_kind": "U", "topic_key": "T002", "ipc_subclass": "A63B"},
        {"applicant_display_name": "帝瑪斯", "patent_id": 3, "country_code": "US",
         "family_id": "F2", "legal_status": "授权", "patent_type": "P",
         "document_kind": "A", "topic_key": "T002", "ipc_subclass": "F03G"},
        {"applicant_display_name": "祺驊", "patent_id": 4, "country_code": "TW",
         "family_id": "F3", "legal_status": "审查中", "patent_type": "P",
         "document_kind": "A", "topic_key": "T001", "ipc_subclass": "A63B"},
        {"applicant_display_name": "祺驊", "patent_id": 5, "country_code": "TW",
         "family_id": "F4", "legal_status": "审查中", "patent_type": "P",
         "document_kind": "A", "topic_key": "T001", "ipc_subclass": "A63B"},
    ]

    def _by_name(self):
        return {p["applicant"]: p for p in cb.rights_strength_profiles(self.ROWS)}

    def test_topic_and_ipc_breadth(self):
        by = self._by_name()
        self.assertEqual(by["帝瑪斯"]["topic_count"], 2)
        self.assertEqual(by["帝瑪斯"]["ipc_subclass_count"], 2)
        # 5 件集中單一主題單一類＝廣度 1，與件數多寡無關。
        self.assertEqual(by["祺驊"]["topic_count"], 1)
        self.assertEqual(by["祺驊"]["ipc_subclass_count"], 1)

    def test_missing_topic_or_ipc_counts_zero_not_crash(self):
        rows = [{"applicant_display_name": "X", "patent_id": 9}]
        p = cb.rights_strength_profiles(rows)[0]
        self.assertEqual((p["topic_count"], p["ipc_subclass_count"]), (0, 0))

    def test_key_player_profiles_carry_strength(self):
        """四面向要掛在 Key Player 上（使用者定案：用在 10 個競爭者那裡；
        申請人排名頁不動）。"""
        profiles = cb.key_player_profiles(self.ROWS)
        deem = next(p for p in profiles if p["applicant"] == "帝瑪斯")
        self.assertEqual(deem["family_count"], 2)
        self.assertEqual(deem["country_count"], 3)
        self.assertEqual(deem["topic_count"], 2)
        self.assertEqual(deem["granted_count"], 3)
        self.assertEqual(deem["kind_counts"], {"發明": 2, "新型": 1})


class RankingDrivenSelectionTests(unittest.TestCase):
    """🔴 2026-08-07 使用者定案：Key Player 的前 10 大**根據排名頁去取**，
    不得在此另用件數切一次——否則排名頁換口徑或人工調整時兩邊分岔
    （同一份知識兩個落點）。"""

    ROWS = [
        {"applicant_display_name": "A", "patent_id": 1, "application_year": 2020},
        {"applicant_display_name": "A", "patent_id": 2, "application_year": 2022},
        {"applicant_display_name": "A", "patent_id": 3, "application_year": 2024},
        {"applicant_display_name": "B", "patent_id": 4, "application_year": 2021},
        {"applicant_display_name": "C", "patent_id": 5, "application_year": 2021},
    ]

    def test_follows_ranking_order_and_membership(self):
        """名單與順序都以排名頁為準——B 件數少於 A 仍排前面。"""
        profiles = cb.key_player_profiles(self.ROWS, ranking=["B", "A"])
        self.assertEqual([p["applicant"] for p in profiles], ["B", "A"])

    def test_names_outside_ranking_excluded(self):
        profiles = cb.key_player_profiles(self.ROWS, ranking=["A"])
        self.assertEqual([p["applicant"] for p in profiles], ["A"])

    def test_ranking_name_without_rows_skipped_not_fabricated(self):
        """排名頁有、本批資料沒有的名字：略過，不得捏造空 profile。"""
        profiles = cb.key_player_profiles(self.ROWS, ranking=["A", "不存在公司"])
        self.assertEqual([p["applicant"] for p in profiles], ["A"])

    def test_no_ranking_falls_back_to_count(self):
        """沒給名單（例如尚未產排名頁）才退回件數排序，並仍守前 10。"""
        profiles = cb.key_player_profiles(self.ROWS)
        self.assertEqual(profiles[0]["applicant"], "A")

    def test_groups_accept_ranking(self):
        groups = cb.key_player_groups(self.ROWS, ranking=["B", "A"])
        self.assertEqual([p["applicant"] for p in groups["trajectory"]], ["A"])
        self.assertEqual([p["applicant"] for p in groups["technical"]], ["B"])

