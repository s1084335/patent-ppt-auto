"""topic_merge／topic_unmerge 可被 worker 領取的守門測試（2026-07-27 實機發現）。

實機症狀：使用者按「合併兩主題」→ job 建起來了（`topic_merge #97`）→ **永遠 queued**，
`current_stage` 為 None（從沒被領走）。兩個主題原封不動，但「合併歷史」照樣列出該筆
並提供「解除合併」鈕——畫面說合併好了，實際什麼都沒發生。

根因：`job_repository.JOB_TYPES` **不含** topic_merge／topic_unmerge，而
`runner.DEFAULT_WORKER_JOB_TYPES = JOB_TYPES - AI_JOB_TYPES` 由它推導 → 沒有 worker 領。
兩者由 `PostgresTopicRepository` 直接寫佇列（不經 create_job），所以建得起來、卻沒人處理。

⚠ 這與 `ai:irrelevant_filter`（白名單有、runner 沒註冊）是**同一型斷鏈**：
一端註冊了、另一端沒有，且**靜默**——不拋錯、不進 log，只是永遠不動。
本測試以「有 handler 就必須有人領」的雙向對照鎖住，防止再犯。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class TopicMergeClaimableTests(unittest.TestCase):
    def test_merge_types_are_claimable(self):
        """topic_merge／topic_unmerge 必須在一般 worker 的領取清單內。"""
        from backend.app.worker.runner import DEFAULT_WORKER_JOB_TYPES

        for job_type in ("topic_merge", "topic_unmerge"):
            with self.subTest(job_type=job_type):
                self.assertIn(
                    job_type, DEFAULT_WORKER_JOB_TYPES,
                    f"{job_type} 沒有任何 worker 會領 → 永遠卡 queued（實機 job 97）")

    def test_every_handler_is_claimable(self):
        """雙向守門：每個註冊了 handler 的 job_type，都必須有 worker 會領。

        這條是通則，不只針對 topic_merge——防止「寫了 handler 卻沒人領」的整類斷鏈。
        AI job 由 ai_bridge 領（不在一般 worker 清單），故排除。
        """
        from backend.app.db.job_repository import AI_JOB_TYPES
        from backend.app.worker.handlers import HANDLERS
        from backend.app.worker.runner import DEFAULT_WORKER_JOB_TYPES

        orphans = sorted(
            job_type for job_type in HANDLERS
            if job_type not in AI_JOB_TYPES
            and job_type not in DEFAULT_WORKER_JOB_TYPES
        )
        self.assertEqual(
            orphans, [],
            f"這些 job_type 有 handler 但沒有 worker 會領，建了就永遠 queued：{orphans}")

    def test_merge_types_in_job_types_whitelist(self):
        """JOB_TYPES 是合法工作類型的唯一來源，兩者須在其中。"""
        from backend.app.db.job_repository import JOB_TYPES

        self.assertIn("topic_merge", JOB_TYPES)
        self.assertIn("topic_unmerge", JOB_TYPES)


if __name__ == "__main__":
    unittest.main()
