"""局部重產 runner：只換指定頁，其餘原樣保留（openspec 6.4）。

## 這一片的邊界

`build_ppt_quality_report` 判定要不要重產（6.1／6.2，Codex）；
`validate_regeneration_response` 驗 CLI 回來的東西有沒有越界（6.3／6.5）。
本片把兩者接起來：實際跑一次重產並**合併**結果。

⚠ 轉 PNG 與重跑 quality report 不在本片——那屬 A5 驗收線。本片刻意只做
「換頁並留下可追溯的紀錄」，因為它可以獨立驗收：給定 plan 與 CLI 回應，
輸出的 slide_plan 必須只有 targets 那幾頁變了。

## 為什麼「保留未標記頁」要寫成測試而不是靠 CLI 自律

局部重產的前提是其餘內容已驗收過。若合併時整份覆蓋，等於讓沒被指定的頁
跳過驗收又進了成品——與本專案反覆出現的靜默退化同型：使用者以為只動了一頁。
"""
from __future__ import annotations

import unittest

from backend.app.reports.planning_contracts import PPT_QUALITY_RETRY_LIMIT
from backend.app.worker.regeneration_runner import (
    RegenerationError,
    run_partial_regeneration,
)

PLAN = {
    "decision": "regenerate_partial",
    "retry_limit": PPT_QUALITY_RETRY_LIMIT,
    "targets": [{"slide_id": "s2", "reason": "text_overflow_estimated"}],
    "locked": {
        "slide_ids": ["s1", "s2", "s3"],
        "chart_identities": ["application_trend:default"],
        "narrative_keys": [],
        "evidence_refs": ["e1"],
    },
}
ORIGINAL = [
    {"slide_id": "s1", "layout_preset": "cover", "narrative": [{"text": "原封面"}]},
    {"slide_id": "s2", "layout_preset": "chart_with_points",
     "narrative": [{"text": "太長的舊要點"}]},
    {"slide_id": "s3", "layout_preset": "reading_guide", "narrative": [{"text": "原判讀"}]},
]


def _cli(payload):
    """假 CLI：只回 target 頁的新內容。"""
    return {"slides": [{"slide_id": "s2", "layout_preset": "chart_with_points",
                        "narrative": [{"text": "精簡後的要點", "evidence_ref": "e1"}]}]}


class PartialRegenerationTests(unittest.TestCase):
    """只換 target 頁。"""

    def test_only_target_slide_is_replaced(self):
        result = run_partial_regeneration(PLAN, ORIGINAL, cli_runner=_cli)
        by_id = {s["slide_id"]: s for s in result["slides"]}
        self.assertEqual(by_id["s2"]["narrative"][0]["text"], "精簡後的要點")
        self.assertEqual(by_id["s1"]["narrative"][0]["text"], "原封面", "未指定頁被改動")
        self.assertEqual(by_id["s3"]["narrative"][0]["text"], "原判讀", "未指定頁被改動")

    def test_slide_order_is_preserved(self):
        """頁序不得因重產而改變——換的是內容不是位置。"""
        result = run_partial_regeneration(PLAN, ORIGINAL, cli_runner=_cli)
        self.assertEqual([s["slide_id"] for s in result["slides"]], ["s1", "s2", "s3"])

    def test_replacement_audit_is_recorded(self):
        """要留下「哪一頁被換掉、為什麼」——沒有紀錄就無法回溯這份成品怎麼來的。"""
        result = run_partial_regeneration(PLAN, ORIGINAL, cli_runner=_cli)
        audit = result["replacement_audit"]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["slide_id"], "s2")
        self.assertEqual(audit[0]["reason"], "text_overflow_estimated")
        self.assertEqual(audit[0]["attempt"], 1)

    def test_out_of_scope_response_raises(self):
        """CLI 越界 → 整份拒收，不得部分採用。

        ⚠ 部分採用等於讓越界的那一頁進成品，只是少改幾頁而已。
        """
        def bad_cli(payload):
            return {"slides": [{"slide_id": "s2"}, {"slide_id": "s3"}]}

        with self.assertRaises(RegenerationError) as ctx:
            run_partial_regeneration(PLAN, ORIGINAL, cli_runner=bad_cli)
        self.assertIn("s3", str(ctx.exception))

    def test_attempt_over_limit_raises_blocked(self):
        """超過重試上限 → 停止並標 blocked，不再自動重產。"""
        with self.assertRaises(RegenerationError) as ctx:
            run_partial_regeneration(PLAN, ORIGINAL, cli_runner=_cli,
                                     attempt=PPT_QUALITY_RETRY_LIMIT + 1)
        self.assertIn("blocked", str(ctx.exception))

    def test_non_partial_decision_is_refused(self):
        """decision 不是 regenerate_partial 就不該走這條路徑。"""
        plan = {**PLAN, "decision": "blocked_defect"}
        with self.assertRaises(RegenerationError):
            run_partial_regeneration(plan, ORIGINAL, cli_runner=_cli)

    def test_missing_target_slide_in_original_raises(self):
        """target 指向原 plan 沒有的頁 → 明確失敗，不靜默新增。"""
        plan = {**PLAN, "targets": [{"slide_id": "s9", "reason": "x"}]}
        with self.assertRaises(RegenerationError):
            run_partial_regeneration(plan, ORIGINAL, cli_runner=_cli)


if __name__ == "__main__":
    unittest.main()
