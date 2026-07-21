"""理解稿 payload 契約與入庫守門（純邏輯＋Fake store，無 DB）。

payload 須含全部 claims、unknown 標記、來源欄標記；守門：validate 通過且 claim 集合一致才准存。
"""
from __future__ import annotations

import unittest


CLAIMS = "1. A device comprising a base. | 2. The device of claim 1, wherein the base is metal. | 3-9. (canceled)"


def _payload():
    from backend.app.comparison.understanding_payload import build_understanding_payload
    return build_understanding_payload(
        {"patent_number": "US-TEST", "claim_text": CLAIMS, "source_fields": ["所有權利要求"]})


def _valid_draft():
    # 與 payload 一致：claim 1（獨立）、2（從屬→1）、3-9（unknown 保留）
    return {
        "source_fields": ["所有權利要求"],
        "independent_claims": [{"claim_number": "1",
                                "single_element_reason": "claim 極短，僅一個結構限定",
                                "elements": [{"text": "A device comprising a base",
                                              "explanation": "裝置含一底座"}]}],
        "dependent_claims": [{"claim_number": "2", "parent": "1",
                              "elements": [{"text": "the base is metal",
                                            "explanation": "新增限制：底座為金屬"}]}],
        "unknown_claims": [{"claim_number": "3-9", "text": "3-9. (canceled)",
                            "note": "需人工釐清：canceled 範圍"}],
        "key_terms": [{"term": "base", "definition": "底座"}],
    }


class FakeStore:
    def __init__(self):
        self.saved = []

    def save_understanding(self, run_id, draft):
        self.saved.append((run_id, draft))
        return len(self.saved)


class PayloadTests(unittest.TestCase):
    def test_payload_contains_all_claims_and_unknown_and_source(self):
        p = _payload()
        self.assertEqual(p["source_fields"], ["所有權利要求"])
        self.assertEqual(p["prompt_version"], "claim_understanding_v2")
        nums = {c["claim_number"] for c in p["claims_raw"]}
        self.assertEqual(nums, {"1", "2", "3-9"})  # 全部條目，含 canceled 範圍
        unk = [c for c in p["claims_raw"] if c["type"] == "unknown"]
        self.assertEqual(unk[0]["claim_number"], "3-9")
        self.assertIn("3-9", {c["claim_number"] for c in p["skeleton"]["unknown_claims"]})


class GuardTests(unittest.TestCase):
    def test_valid_draft_saved(self):
        from backend.app.comparison.understanding_payload import save_understanding_draft
        store = FakeStore()
        v = save_understanding_draft(store, 1, _payload(), _valid_draft(), ai_model="claude-opus-4-8")
        self.assertEqual(v, 1)
        _, stored = store.saved[0]
        self.assertEqual(stored["ai_model"], "claude-opus-4-8")
        self.assertEqual(stored["prompt_version"], "claim_understanding_v2")

    def test_invalid_structure_rejected(self):
        from backend.app.comparison.understanding_payload import save_understanding_draft
        from backend.app.comparison.claim_model import ClaimModelError
        bad = _valid_draft()
        bad["independent_claims"][0]["elements"] = []  # 空 elements 違反 claim_model
        with self.assertRaises(ClaimModelError):
            save_understanding_draft(FakeStore(), 1, _payload(), bad, ai_model="m")

    def test_claim_set_mismatch_rejected(self):
        from backend.app.comparison.understanding_payload import save_understanding_draft, DraftGuardError
        store = FakeStore()
        extra = _valid_draft()
        # AI 擅自新增一條不存在的 claim
        extra["dependent_claims"].append({"claim_number": "99", "parent": "1",
                                          "elements": [{"text": "x", "explanation": "y"}]})
        with self.assertRaises(DraftGuardError):
            save_understanding_draft(store, 1, _payload(), extra, ai_model="m")
        self.assertEqual(store.saved, [])  # 拒存，未落任何列

    def test_independent_under_3_elements_without_reason_rejected(self):
        # 拆解不足守門：獨立項 <3 要素且無 single_element_reason → 拒存並指出哪一項
        from backend.app.comparison.understanding_payload import save_understanding_draft, DraftGuardError
        d = _valid_draft()
        d["independent_claims"][0].pop("single_element_reason", None)  # 移除理由
        with self.assertRaises(DraftGuardError):
            save_understanding_draft(FakeStore(), 1, _payload(), d, ai_model="m")

    def test_element_text_not_substring_rejected(self):
        # substring 守門：element.text 非 claim 原文片段 → 拒存
        from backend.app.comparison.understanding_payload import save_understanding_draft, DraftGuardError
        d = _valid_draft()
        d["dependent_claims"][0]["elements"] = [{"text": "the base is plastic",  # 原文是 metal
                                                 "explanation": "改寫，非原文"}]
        with self.assertRaises(DraftGuardError):
            save_understanding_draft(FakeStore(), 1, _payload(), d, ai_model="m")

    def test_source_field_whitelist_enforced(self):
        from backend.app.comparison.understanding_payload import save_understanding_draft
        from backend.app.comparison.claim_model import ClaimModelError
        bad = _valid_draft()
        bad["source_fields"] = ["說明書"]  # 非權利要求來源
        with self.assertRaises(ClaimModelError):
            save_understanding_draft(FakeStore(), 1, _payload(), bad, ai_model="m")


if __name__ == "__main__":
    unittest.main()
