"""Market evidence 候選流程的契約測試。

Claude CLI 只負責外部市場資料研究與整理候選 evidence；正式入庫必須經過
使用者確認。這組測試鎖住 anti-hallucination guard 與候選資料格式，避免
CLI 任務 brief 變成不可讀或讓候選資料直接進正式表。
"""
from __future__ import annotations

import unittest

from backend.app.market.evidence_model import MarketEvidenceError
from backend.app.market.evidence_workflow import (
    ACCEPTANCE_OUTPUT_TYPE,
    build_market_research_task,
    normalize_candidate,
    validate_candidate,
)


def _candidate(**overrides):
    """建立一筆符合候選格式的 market evidence。"""
    payload = {
        "source_name": "US Department of Energy",
        "source_url": "https://www.energy.gov/example-market-note",
        "published_on": "2025-03-01",
        "publisher": "US Department of Energy",
        "reliability": "industry_gov_corp",
        "summary": "公開資料指出商用設備需求增加。",
        "evidence_excerpt": (
            "The report states that demand increased across commercial users in 2025."
        ),
        "value": {"year": 2025, "market_definition": "commercial equipment", "market_size": 12.5},
    }
    candidate = {
        "kind": "market_size",
        "scope": "robot mower",
        "target": "US",
        "payload_json": payload,
        "source_url": "https://www.energy.gov/example-market-note",
        "summary": "公開資料指出商用設備需求增加。",
    }
    candidate.update(overrides)
    return candidate


class MarketEvidenceWorkflowTests(unittest.TestCase):
    """驗證 Claude CLI market evidence 候選流程。"""

    def test_build_market_research_task_returns_guarded_claude_cli_brief(self):
        """任務 brief 必須可讀，且明確禁止候選資料直接寫正式表。"""
        task = build_market_research_task(
            scope="robot mower",
            targets=["US", "EU"],
            kinds=["market_size", "pain_point"],
            report_version="v1",
        )

        self.assertEqual(task["status"], "needs_external_research")
        self.assertEqual(task["output_type"], ACCEPTANCE_OUTPUT_TYPE)
        self.assertEqual(task["scope"], "robot mower")
        self.assertEqual(task["targets"], ["US", "EU"])
        self.assertIn("evidence_excerpt", task["candidate_schema"]["payload_json_required"])
        self.assertTrue(any("不得直接呼叫 save_market_evidence" in rule for rule in task["acceptance_rules"]))
        self.assertTrue(any("source_url" in rule for rule in task["anti_hallucination_rules"]))
        self.assertTrue(any("使用者確認" in rule for rule in task["acceptance_rules"]))

    def test_validate_candidate_requires_source_excerpt(self):
        """缺少可追溯摘錄時，不得接受為候選 evidence。"""
        candidate = _candidate()
        candidate["payload_json"].pop("evidence_excerpt")

        with self.assertRaisesRegex(MarketEvidenceError, "evidence_excerpt"):
            validate_candidate(candidate)

    def test_validate_candidate_rejects_mismatched_source_url(self):
        """外層 source_url 與 payload_json.source_url 必須一致。"""
        candidate = _candidate(source_url="https://www.energy.gov/other")

        with self.assertRaisesRegex(MarketEvidenceError, "source_url"):
            validate_candidate(candidate)

    def test_normalize_candidate_maps_target_to_existing_market_store_shape(self):
        """候選 evidence 會整理成 MarketStore.save_evidence 可接受的欄位形狀。"""
        normalized = normalize_candidate(_candidate())

        self.assertEqual(normalized["kind"], "market_size")
        self.assertEqual(normalized["scope"], "robot mower")
        self.assertEqual(normalized["target"], "US")
        self.assertEqual(normalized["source_url"], "https://www.energy.gov/example-market-note")


if __name__ == "__main__":
    unittest.main()
