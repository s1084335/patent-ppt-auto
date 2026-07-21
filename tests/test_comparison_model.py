"""案件比對純邏輯契約：理解稿結構驗證（claim_model）＋四態/all-elements/引用鏈推論（verdict）。

全部純邏輯、無 DB。涵蓋：結構驗證各非法型態、來源欄位白名單、
四態真值表、all-elements rule、引用鏈推論（含多獨立項各帶從屬項）。
"""
from __future__ import annotations

import unittest


def _valid_understanding() -> dict:
    """一份最小合法理解稿：兩獨立項各帶一從屬項（多獨立項案例）。"""
    return {
        "source_fields": ["所有權利要求"],
        "independent_claims": [
            {"claim_number": "1", "elements": [
                {"text": "一種鋸切裝置", "explanation": "裝置整體"},
                {"text": "包含底座", "explanation": "支撐結構"},
            ]},
            {"claim_number": "3", "elements": [
                {"text": "一種控制方法", "explanation": "方法類請求項"},
            ]},
        ],
        "dependent_claims": [
            {"claim_number": "2", "parent": "1", "elements": [
                {"text": "其中底座具有滑軌", "explanation": "新增限制：滑軌"},
            ]},
            {"claim_number": "4", "parent": "3", "elements": [
                {"text": "其中該方法進一步包含偵測步驟", "explanation": "新增限制：偵測"},
            ]},
        ],
        "key_terms": [{"term": "滑軌", "definition": "供滑動的導引結構"}],
    }


class UnderstandingStructureTests(unittest.TestCase):
    """理解稿結構與來源欄位驗證。"""

    def _validate(self, draft):
        from backend.app.comparison.claim_model import validate_understanding
        return validate_understanding(draft)

    def _err(self):
        from backend.app.comparison.claim_model import ClaimModelError
        return ClaimModelError

    def test_valid_passes(self):
        self._validate(_valid_understanding())  # 不應拋錯

    def test_empty_independent_claims_rejected(self):
        d = _valid_understanding()
        d["independent_claims"] = []
        with self.assertRaises(self._err()):
            self._validate(d)

    def test_element_missing_text_rejected(self):
        d = _valid_understanding()
        d["independent_claims"][0]["elements"][0] = {"explanation": "缺原文"}
        with self.assertRaises(self._err()):
            self._validate(d)

    def test_element_missing_explanation_rejected(self):
        d = _valid_understanding()
        d["independent_claims"][0]["elements"][0] = {"text": "缺解釋"}
        with self.assertRaises(self._err()):
            self._validate(d)

    def test_empty_elements_rejected(self):
        d = _valid_understanding()
        d["independent_claims"][0]["elements"] = []
        with self.assertRaises(self._err()):
            self._validate(d)

    def test_dependent_parent_missing_rejected(self):
        d = _valid_understanding()
        d["dependent_claims"][0]["parent"] = "99"  # 不存在
        with self.assertRaises(self._err()):
            self._validate(d)

    def test_dependency_cycle_rejected(self):
        d = _valid_understanding()
        # 造環：2->5, 5->2（皆從屬項）
        d["dependent_claims"] = [
            {"claim_number": "2", "parent": "5", "elements": [{"text": "a", "explanation": "b"}]},
            {"claim_number": "5", "parent": "2", "elements": [{"text": "c", "explanation": "d"}]},
        ]
        with self.assertRaises(self._err()):
            self._validate(d)

    def test_source_field_specification_rejected(self):
        d = _valid_understanding()
        d["source_fields"] = ["說明書"]  # 不得從說明書抽取
        with self.assertRaises(self._err()):
            self._validate(d)

    def test_source_field_main_claim_rejected(self):
        d = _valid_understanding()
        d["source_fields"] = ["主權項"]  # 舊 COALESCE 主權項作法已禁用
        with self.assertRaises(self._err()):
            self._validate(d)

    def test_source_fields_empty_rejected(self):
        d = _valid_understanding()
        d["source_fields"] = []
        with self.assertRaises(self._err()):
            self._validate(d)

    def test_key_terms_missing_rejected(self):
        d = _valid_understanding()
        del d["key_terms"]
        with self.assertRaises(self._err()):
            self._validate(d)


class ElementFourStateTests(unittest.TestCase):
    """四態 enum 與 all-elements rule 真值表。"""

    def _eval(self, statuses):
        from backend.app.comparison.verdict import evaluate_claim
        return evaluate_claim(statuses)

    def test_reject_invalid_status(self):
        from backend.app.comparison.verdict import parse_element_status, VerdictError
        with self.assertRaises(VerdictError):
            parse_element_status("maybe")

    def test_any_not_met_gives_not_established(self):
        from backend.app.comparison.verdict import ClaimStatus
        self.assertEqual(self._eval(["met", "not_met", "met"]), ClaimStatus.NOT_ESTABLISHED)

    def test_all_met_gives_possibly_established(self):
        from backend.app.comparison.verdict import ClaimStatus
        self.assertEqual(self._eval(["met", "met"]), ClaimStatus.POSSIBLY_ESTABLISHED)

    def test_arguably_or_insufficient_gives_needs_review(self):
        from backend.app.comparison.verdict import ClaimStatus
        self.assertEqual(self._eval(["met", "arguably_met"]), ClaimStatus.NEEDS_REVIEW)
        self.assertEqual(self._eval(["met", "insufficient_info"]), ClaimStatus.NEEDS_REVIEW)

    def test_empty_elements_rejected(self):
        from backend.app.comparison.verdict import VerdictError
        with self.assertRaises(VerdictError):
            self._eval([])


class CitationChainInferenceTests(unittest.TestCase):
    """引用鏈推論：獨立項 not_established 推論分支從屬項，否則只比對新增限制。"""

    def _infer(self, claims):
        from backend.app.comparison.verdict import infer_verdicts
        return infer_verdicts(claims)

    def test_independent_not_established_infers_dependent(self):
        from backend.app.comparison.verdict import ClaimStatus
        res = self._infer([
            {"claim_number": "1", "type": "independent", "element_statuses": ["met", "not_met"]},
            {"claim_number": "2", "type": "dependent", "parent": "1", "element_statuses": ["met"]},
        ])
        self.assertEqual(res["1"]["status"], ClaimStatus.NOT_ESTABLISHED)
        self.assertEqual(res["2"]["status"], ClaimStatus.NOT_ESTABLISHED)
        self.assertTrue(res["2"]["inferred"])  # 推論而非實比對

    def test_independent_possibly_dependent_new_limits_met(self):
        from backend.app.comparison.verdict import ClaimStatus
        res = self._infer([
            {"claim_number": "1", "type": "independent", "element_statuses": ["met", "met"]},
            {"claim_number": "2", "type": "dependent", "parent": "1", "element_statuses": ["met"]},
        ])
        self.assertEqual(res["2"]["status"], ClaimStatus.POSSIBLY_ESTABLISHED)
        self.assertFalse(res["2"]["inferred"])

    def test_independent_possibly_dependent_new_limit_not_met(self):
        from backend.app.comparison.verdict import ClaimStatus
        res = self._infer([
            {"claim_number": "1", "type": "independent", "element_statuses": ["met"]},
            {"claim_number": "2", "type": "dependent", "parent": "1", "element_statuses": ["not_met"]},
        ])
        self.assertEqual(res["2"]["status"], ClaimStatus.NOT_ESTABLISHED)
        self.assertFalse(res["2"]["inferred"])  # 由自身新增限制判定，非推論

    def test_independent_needs_review_caps_dependent(self):
        from backend.app.comparison.verdict import ClaimStatus
        res = self._infer([
            {"claim_number": "1", "type": "independent", "element_statuses": ["met", "arguably_met"]},
            {"claim_number": "2", "type": "dependent", "parent": "1", "element_statuses": ["met"]},
        ])
        self.assertEqual(res["1"]["status"], ClaimStatus.NEEDS_REVIEW)
        self.assertEqual(res["2"]["status"], ClaimStatus.NEEDS_REVIEW)  # 父項不確定上限

    def test_multi_independent_branches_independent(self):
        from backend.app.comparison.verdict import ClaimStatus
        # 兩獨立項各帶從屬項，分支互不影響
        res = self._infer([
            {"claim_number": "1", "type": "independent", "element_statuses": ["not_met"]},
            {"claim_number": "2", "type": "dependent", "parent": "1", "element_statuses": ["met"]},
            {"claim_number": "3", "type": "independent", "element_statuses": ["met"]},
            {"claim_number": "4", "type": "dependent", "parent": "3", "element_statuses": ["met"]},
        ])
        self.assertEqual(res["2"]["status"], ClaimStatus.NOT_ESTABLISHED)
        self.assertTrue(res["2"]["inferred"])
        self.assertEqual(res["4"]["status"], ClaimStatus.POSSIBLY_ESTABLISHED)
        self.assertFalse(res["4"]["inferred"])


if __name__ == "__main__":
    unittest.main()
