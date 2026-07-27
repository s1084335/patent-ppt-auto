"""候選說明 instruction 的口徑契約（對齊 decisions.md 2026-07-17）。

定案（`.agents/context/decisions.md`「2026-07-17 分群主題數候選說明原則」）：
- 候選分數「只作排序輔助，**不作為前端主要顯示或說服依據**」。
- 要讓使用者理解三種方案的「**意義**」：主題切分程度、穩定性與風險。
- 「LLM 或前端說明若提到分數低，應**翻成語意原因**，例如主題一致性下降、
  小主題比例偏高、切分過細；**不要把小數分數當主內容**。」

2026-07-27 實測落差：instruction 只說「用一般使用者看得懂的方式說明取捨」，
未寫入上述禁令，AI 產出遂以分數開頭當論據（「整體分數較高」「綜合分數僅0.1」）。

另一項落差：instruction 寫死「三組候選」，但候選組數依資料量而定——
`top_level_k_values()` 對 100–199 筆只掃 k=(10,15)＝**兩組**，
說「三組」與實際不符，會誤導 LLM。
"""
from __future__ import annotations

import unittest

from backend.app.clustering import workspace_service


class CandidateInstructionContractTests(unittest.TestCase):
    """instruction 必須寫入定案的口徑要求，不能只說「看得懂」。"""

    def _instruction(self) -> str:
        """取 instruction 常數本文（不連 DB；candidate_review_payload 需 run_id）。"""
        return workspace_service.CANDIDATE_EXPLANATION_INSTRUCTION

    def test_forbids_score_as_main_content(self):
        """必須明示不要把小數分數當主內容（定案原文要求）。"""
        text = self._instruction()
        self.assertTrue(
            ("不要把" in text and "分數" in text) or "不得以分數" in text,
            "instruction 需明示『不要把小數分數當主內容』，否則 AI 會拿分數當論據",
        )

    def test_requires_semantic_reasons(self):
        """必須要求把分數翻成語意原因，並給出定案列舉的例子。"""
        text = self._instruction()
        self.assertIn("語意", text, "instruction 需要求翻成語意原因")
        # 定案列舉：主題一致性下降、小主題比例偏高、切分過細
        self.assertTrue(
            any(k in text for k in ("一致性", "小主題", "切分過細")),
            "instruction 需帶定案列舉的語意原因例子，讓 LLM 有可循的措辭",
        )

    def test_mentions_split_stability_risk(self):
        """必須要求說明切分程度／穩定性／風險三個面向（定案第 2 條）。"""
        text = self._instruction()
        for word in ("切分", "穩定", "風險"):
            self.assertIn(word, text, f"instruction 需涵蓋「{word}」面向")

    def test_does_not_hardcode_three_candidates(self):
        """不得寫死「三組」——候選組數依資料量而定（118 筆只有兩組）。"""
        self.assertNotIn(
            "三組", self._instruction(),
            "候選組數由 top_level_k_values() 依資料量決定，寫死三組與實際不符",
        )


if __name__ == "__main__":
    unittest.main()
