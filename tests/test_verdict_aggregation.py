"""案件比對 · verdict 彙總接線測試（Red 先行）。

驗證 build_claims_for_inference() 能把 element_analysis 的四態與 understanding 的引用鏈
（獨立/從屬/parent）合併成 infer_verdicts 可吃的輸入，並讓整條彙總得到正確 claim 級判定。
"""
from __future__ import annotations

import unittest

from backend.app.comparison.verdict_aggregation import (
    aggregate_verdict,
    build_claims_for_inference,
)


# understanding：claim 1 獨立、claim 2 從屬於 1
UNDERSTANDING = {
    "independent_claims": [
        {"claim_number": "1", "elements": [{"text": "a frame rod"},
                                           {"text": "a controller"}]},
    ],
    "dependent_claims": [
        {"claim_number": "2", "parent": "1", "elements": [{"text": "the rod is metal"}]},
    ],
    "unknown_claims": [{"claim_number": "3", "text": "3. (canceled)"}],
}


class BuildClaimsForInferenceTests(unittest.TestCase):

    def test_merges_status_with_reference_chain(self):
        """element_analysis 四態 + understanding 引用鏈 → infer_verdicts 輸入格式。"""
        element_analysis = {"claims": [
            {"claim_number": "1", "elements": [
                {"element_id": "1a", "status": "met"},
                {"element_id": "1b", "status": "met"}]},
            {"claim_number": "2", "elements": [
                {"element_id": "2a", "status": "met"}]},
        ]}
        claims = build_claims_for_inference(UNDERSTANDING, element_analysis)
        by_num = {c["claim_number"]: c for c in claims}
        # claim 1 獨立、帶兩要素四態
        self.assertEqual(by_num["1"]["type"], "independent")
        self.assertEqual(by_num["1"]["element_statuses"], ["met", "met"])
        # claim 2 從屬、parent=1
        self.assertEqual(by_num["2"]["type"], "dependent")
        self.assertEqual(by_num["2"]["parent"], "1")
        # unknown（canceled）不進比對
        self.assertNotIn("3", by_num)

    def test_unknown_claims_excluded(self):
        """canceled/unknown claim 不納入 verdict 彙總。"""
        claims = build_claims_for_inference(UNDERSTANDING, {"claims": []})
        self.assertNotIn("3", {c["claim_number"] for c in claims})


class AggregateVerdictTests(unittest.TestCase):

    def test_dependent_not_established_when_parent_fails(self):
        """獨立項 not_met → 從屬項推論 not_established（引用鏈）。"""
        element_analysis = {"claims": [
            {"claim_number": "1", "elements": [
                {"element_id": "1a", "status": "not_met"},
                {"element_id": "1b", "status": "met"}]},
            {"claim_number": "2", "elements": [
                {"element_id": "2a", "status": "met"}]},
        ]}
        verdict = aggregate_verdict(UNDERSTANDING, element_analysis)
        by_num = {c["claim_number"]: c for c in verdict["claims"]}
        self.assertEqual(by_num["1"]["status"], "not_established")
        # 父項不成立 → 從屬項推論不成立、且標 inferred
        self.assertEqual(by_num["2"]["status"], "not_established")
        self.assertTrue(by_num["2"]["inferred"])

    def test_all_met_possibly_established(self):
        """全要素 met → possibly_established。"""
        element_analysis = {"claims": [
            {"claim_number": "1", "elements": [
                {"element_id": "1a", "status": "met"},
                {"element_id": "1b", "status": "met"}]},
            {"claim_number": "2", "elements": [
                {"element_id": "2a", "status": "met"}]},
        ]}
        verdict = aggregate_verdict(UNDERSTANDING, element_analysis)
        by_num = {c["claim_number"]: c for c in verdict["claims"]}
        self.assertEqual(by_num["1"]["status"], "possibly_established")
        self.assertEqual(by_num["2"]["status"], "possibly_established")


if __name__ == "__main__":
    unittest.main()
