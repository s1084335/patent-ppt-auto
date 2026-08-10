"""局部重產：CLI 只能改指定 target，其餘一律鎖住（openspec 6.3／6.5）。

## 分工

`build_ppt_quality_report`（Codex，6.1／6.2）依 manifest warnings 判定 decision 並
產出 `RegenerationPlan`——它回答「要不要重產、重產哪幾頁、哪些不准動」。

本檔負責**執行面**：CLI 依 plan 回傳重產內容之後，驗證它沒有越界。⚠ 沒有這一層，
scope lock 只是一份宣告——CLI 大可回傳一份全新的 SlidePlan，而我們照單全收。

## 為什麼越界必須擋而不是靜默採用

局部重產的前提是「其餘內容已經驗收過」。CLI 若順手改了沒被指定的頁，那些頁就
**跳過了驗收**卻出現在成品裡——使用者以為只重產了第 5 頁，實際上第 3 頁的數字
也被改過。這與本輪反覆出現的「靜默退化」同型：失敗時看起來像正常行為。

## 重試上限（6.5）

同一 target 連續失敗達上限即停止，標 `blocked_content_defect` 或
`blocked_layout_defect`，不再自動重產。⚠ 判準沿用 Codex 定的
`PPT_QUALITY_RETRY_LIMIT`，本檔不另訂一個數字——那會是第二個落點。
"""
from __future__ import annotations

import unittest

from backend.app.reports.planning_contracts import (
    PPT_QUALITY_RETRY_LIMIT,
    validate_regeneration_response,
)

LOCK = {
    "slide_ids": ["s1", "s2", "s3"],
    "chart_identities": ["application_trend:default", "applicant_ranking:default"],
    "narrative_keys": ["n1", "n2"],
    "evidence_refs": ["e1", "e2"],
}
PLAN = {
    "decision": "regenerate_partial",
    "retry_limit": PPT_QUALITY_RETRY_LIMIT,
    "targets": [{"slide_id": "s2", "reason": "text_overflow_estimated"}],
    "locked": LOCK,
}


def _resp(*slides):
    return {"slides": list(slides)}


class ScopeLockTests(unittest.TestCase):
    """只准動 targets 指名的頁。"""

    def test_target_only_response_passes(self):
        """只回傳 target 頁 → 通過。"""
        self.assertEqual(
            validate_regeneration_response(
                PLAN, _resp({"slide_id": "s2", "narrative": [{"text": "改好的要點"}]})),
            [],
        )

    def test_untargeted_slide_is_rejected(self):
        """回傳了沒被指定的頁 → 擋。那一頁已驗收過，不該被順手改掉。"""
        errors = validate_regeneration_response(
            PLAN, _resp({"slide_id": "s2"}, {"slide_id": "s3"}))
        self.assertTrue(errors)
        self.assertIn("s3", errors[0])

    def test_unknown_slide_id_is_rejected(self):
        """回傳鎖定清單之外的 slide_id → 擋（等於自行加頁）。"""
        errors = validate_regeneration_response(PLAN, _resp({"slide_id": "s9"}))
        self.assertTrue(errors)
        self.assertIn("s9", errors[0])

    def test_changing_chart_identity_is_rejected(self):
        """改圖＝改掉使用者選定的內容，不在重產授權範圍內。"""
        errors = validate_regeneration_response(
            PLAN, _resp({"slide_id": "s2", "chart_identities": ["某張沒選的圖:default"]}))
        self.assertTrue(errors)
        self.assertIn("圖", errors[0])

    def test_keeping_locked_chart_identity_passes(self):
        """沿用原本就鎖定的圖 → 通過（重產不代表要換圖）。"""
        self.assertEqual(
            validate_regeneration_response(
                PLAN,
                _resp({"slide_id": "s2",
                       "chart_identities": ["applicant_ranking:default"]})),
            [],
        )

    def test_unknown_evidence_ref_is_rejected(self):
        """引用鎖定清單外的 evidence → 擋：重產不得引進未經驗證的來源。"""
        errors = validate_regeneration_response(
            PLAN,
            _resp({"slide_id": "s2", "narrative": [{"text": "x", "evidence_ref": "e99"}]}))
        self.assertTrue(errors)
        self.assertIn("e99", errors[0])

    def test_empty_response_is_rejected(self):
        """一頁都沒回＝沒有重產，不得當成成功。"""
        errors = validate_regeneration_response(PLAN, _resp())
        self.assertTrue(errors)


class RetryLimitTests(unittest.TestCase):
    """6.5：連續失敗達上限即停止自動重產。"""

    def test_attempt_within_limit_is_allowed(self):
        errors = validate_regeneration_response(
            PLAN, _resp({"slide_id": "s2"}), attempt=PPT_QUALITY_RETRY_LIMIT)
        self.assertEqual(errors, [])

    def test_attempt_over_limit_is_blocked(self):
        """超過上限 → 擋，並要指出是內容缺陷還是版面缺陷。

        ⚠ 不再自動重產是刻意的：連續失敗多半代表根因判斷錯了，繼續重跑只是
        燒 token（同全域規則「同一問題最多修五輪」的精神）。
        """
        errors = validate_regeneration_response(
            PLAN, _resp({"slide_id": "s2"}), attempt=PPT_QUALITY_RETRY_LIMIT + 1)
        self.assertTrue(errors)
        self.assertIn("blocked", errors[0])


if __name__ == "__main__":
    unittest.main()
