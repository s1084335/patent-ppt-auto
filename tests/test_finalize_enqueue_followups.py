"""finalize 完成後的自動接續 job（2026-07-27）。

兩件事：

1. **既有缺陷**：`_enqueue_irrelevant_filter` 讀 `summary["workspace_id"]`，
   但 `FinalizationSummary` **沒有這個欄位** → 每次都靜默 return。
   實測 DB 歷來 `ai:irrelevant_filter` job **0 筆**，證實 2026-07-24 定案的
   「分群完成自動接續不相干篩選」從未真正運作。

2. **新需求**（2026-07-27 使用者定案）：finalize 完成後自動排 `ai:topic_label`，
   讓 AI 產中文主題名；分類區採「先顯示 fallback 標籤、命名完自動更新」，
   不阻擋確定性結果（沿 2026-07-17「AI 掛掉也看得到」的定案精神）。

兩者都需要 summary 帶 workspace_id／source_field，故一併處理。
"""
from __future__ import annotations

import unittest
from dataclasses import fields
from unittest import mock

from backend.app.clustering.runner import FinalizationSummary
from backend.app.worker import handlers


class FinalizationSummaryFieldTests(unittest.TestCase):
    """summary 必須帶 workspace_id／source_field，後續 enqueue 才有得讀。"""

    def test_summary_carries_workspace_and_source(self):
        names = {f.name for f in fields(FinalizationSummary)}
        self.assertIn(
            "workspace_id", names,
            "缺 workspace_id → _enqueue_irrelevant_filter/_enqueue_topic_label 靜默 return",
        )
        self.assertIn(
            "source_field", names,
            "ai:topic_label 以 workspace+source_field 為單位，缺 source_field 無法排通道別的命名 job",
        )


class EnqueueTopicLabelTests(unittest.TestCase):
    """finalize 後自動排 ai:topic_label，且失敗不得影響 finalize。"""

    def _summary(self, **over):
        data = {
            "run_id": 1, "candidate_id": 2, "candidate_type": "balanced",
            "selected_k": 10, "topic_count": 10, "assignment_count": 100,
            "status": "completed", "workspace_id": 7,
            "source_field": "wips_independent_claims",
        }
        data.update(over)
        return data

    def test_enqueues_topic_label_job(self):
        """帶 workspace_id／source_field 時應建 ai:topic_label（max_attempts=1）。"""
        with mock.patch("backend.app.db.job_repository.create_job") as create:
            handlers._enqueue_topic_label(self._summary())
        create.assert_called_once()
        args, kwargs = create.call_args
        self.assertEqual(args[0], "ai:topic_label")
        payload = args[1]
        self.assertEqual(payload["workspace_id"], 7)
        self.assertEqual(payload["source_field"], "wips_independent_claims")
        # AI CLI 任務不自動重試（重跑要花 LLM 額度），與手動端點同一口徑
        self.assertEqual(kwargs.get("max_attempts"), 1)

    def test_missing_workspace_does_not_enqueue(self):
        """缺 workspace_id 時不建 job（不得丟出例外）。"""
        with mock.patch("backend.app.db.job_repository.create_job") as create:
            handlers._enqueue_topic_label(self._summary(workspace_id=None))
        create.assert_not_called()

    def test_enqueue_failure_is_isolated(self):
        """建 job 失敗只記 log、不 raise——分群本體已落庫，命名只是加值。"""
        with mock.patch("backend.app.db.job_repository.create_job",
                        side_effect=RuntimeError("db down")):
            handlers._enqueue_topic_label(self._summary())  # 不應拋出


if __name__ == "__main__":
    unittest.main()
