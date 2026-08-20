"""R-3／R-4（2026-08-05 第七輪實機驗收發現）的 regression 測試。

R-3 象限圖底注壓住頁尾：hero 圖框底緣 1.86+5.0=6.86in 落進頁尾帶
（footnote.top 6.78in）。**高度受限**的圖（象限板長寬比 ~1.47，比框的 1.78 高）
會撐滿框高，圖自己的底注就疊在組版頁尾上；寬度受限的圖（長條 2.1）不會。

R-4 只在單一通道的專利，申請人被丟掉：`_merge_cluster_channels` 對 `patents`
取了聯集（註解還寫明理由），對 `normalized_applicants` 卻只取第一個通道
——同型問題只修一處。實機：9 件只在功效通道的專利（**正好全是 TW 案**，
技術通道用獨立項而 TW 案沒有）申請人全失蹤 → 「提升健身體驗」1 件 0 家、
代表專利空白，且 TW 案永遠不會被選為代表專利（扣 1911 的修正因此看不到成效）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "patent-report-ppt" / "scripts"))


# ⚠ R3ImageFrameClearsFooterTests 已隨 PPT 交付線移除（2026-08-10，remove-ppt-delivery-line）。


class R4ApplicantUnionTests(unittest.TestCase):
    """跨通道合併時申請人要取聯集——只在某一通道的專利不得整批失蹤。"""

    def test_normalized_applicants_are_unioned(self):
        from backend.app.worker import handlers

        parts = {
            "wips_independent_claims": {
                "topics": [{"topic_code": "T001"}],
                "assignments": [{"topic_code": "T001", "patent_id": 1}],
                "topic_rows": [],
                "patents": {1: {"number": "A"}},
                "normalized_applicants": [{"patent_id": 1, "applicant_name": "甲"}],
                "top_applicants_ws": [{"name": "甲", "count": 1}],
            },
            "effect_summary": {
                "topics": [{"topic_code": "T001"}],
                "assignments": [{"topic_code": "T001", "patent_id": 110}],
                "topic_rows": [],
                "patents": {110: {"number": "11321229"}},
                "normalized_applicants": [{"patent_id": 110, "applicant_name": "祺驊"}],
                "top_applicants_ws": [{"name": "祺驊", "count": 1}],
            },
        }
        original = handlers._load_report_cluster_data
        # ⚠ 簽名跟隨真函式（2026-08-20 加了 report_scope）——少一個參數會
        #   TypeError 而被上層 except 吞掉，測試以假失敗的姿勢紅。
        handlers._load_report_cluster_data = (
            lambda ws, sf, report_scope="company": parts.get(sf))
        try:
            merged = handlers._merge_cluster_channels(
                3, ["wips_independent_claims", "effect_summary"])
        finally:
            handlers._load_report_cluster_data = original

        pids = {a["patent_id"] for a in merged["normalized_applicants"]}
        self.assertEqual(pids, {1, 110},
                         "只在功效通道的專利申請人被丟掉——該主題會變成 0 家、代表專利空白")
        self.assertEqual(set(merged["patents"]), {1, 110})

    def test_union_dedupes_same_pair(self):
        """同一 (patent_id, applicant) 在兩通道都出現時不得重複計數。"""
        from backend.app.worker import handlers

        same = [{"patent_id": 1, "applicant_name": "甲"}]
        parts = {
            "wips_independent_claims": {
                "topics": [{"topic_code": "T001"}], "assignments": [], "topic_rows": [],
                "patents": {}, "normalized_applicants": list(same), "top_applicants_ws": [],
            },
            "effect_summary": {
                "topics": [{"topic_code": "T001"}], "assignments": [], "topic_rows": [],
                "patents": {}, "normalized_applicants": list(same), "top_applicants_ws": [],
            },
        }
        original = handlers._load_report_cluster_data
        # ⚠ 簽名跟隨真函式（2026-08-20 加了 report_scope）——少一個參數會
        #   TypeError 而被上層 except 吞掉，測試以假失敗的姿勢紅。
        handlers._load_report_cluster_data = (
            lambda ws, sf, report_scope="company": parts.get(sf))
        try:
            merged = handlers._merge_cluster_channels(
                3, ["wips_independent_claims", "effect_summary"])
        finally:
            handlers._load_report_cluster_data = original
        self.assertEqual(len(merged["normalized_applicants"]), 1)


if __name__ == "__main__":
    unittest.main()

