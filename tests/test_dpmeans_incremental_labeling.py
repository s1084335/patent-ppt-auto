"""DP-Means 增量長出新主題後要排 ai:topic_label（tasks 2.4 Red，CLU-004）。

## 為什麼不是「增量完就排」

`ai_topic_label_runner` 會重寫所有非 `manual` 的主題名。人工命名有 guard 保護
不會被覆蓋，但**既有 AI 命名會被重跑**——每次增量都排等於每次都重新命名整個
workspace，白花 LLM 額度（max_attempts=1，重跑就是真的再花一次）。

所以判準是「**這批有沒有長出新主題**」，不是「有沒有跑增量」。KMeans 固定 k，
永遠不會長出新主題，這條路徑對它是完全靜默的。
"""
from __future__ import annotations

import unittest
from unittest import mock

from backend.app.clustering.workspace_service import IncrementalSummary
from backend.app.worker import handlers


def _summary(new_topic_codes):
    return IncrementalSummary(
        run_id=7, workspace_id=3, source_field="wips_independent_claims",
        new_document_count=5, assignment_count=5, artifact_version=2,
        pca_updated=False, status="completed", new_topic_codes=new_topic_codes)


class IncrementalLabelEnqueueTests(unittest.TestCase):
    def test_new_topics_trigger_label_job(self):
        with mock.patch.object(handlers, "_enqueue_topic_label") as enqueue:
            handlers._enqueue_topic_label_for_new_topics(_summary(["T006"]))
        enqueue.assert_called_once()

    def test_no_new_topics_does_not_enqueue(self):
        """⚠ 沒有新主題就不排——否則每次增量都重跑整個 workspace 的 AI 命名。"""
        with mock.patch.object(handlers, "_enqueue_topic_label") as enqueue:
            handlers._enqueue_topic_label_for_new_topics(_summary([]))
        enqueue.assert_not_called()

    def test_kmeans_summary_without_field_is_silent(self):
        """⚠ 對照組：舊引擎的 summary 沒有新主題，這條路徑不得改變它的行為。"""
        with mock.patch.object(handlers, "_enqueue_topic_label") as enqueue:
            handlers._enqueue_topic_label_for_new_topics(_summary(None))
        enqueue.assert_not_called()

    def test_enqueue_failure_does_not_break_incremental(self):
        """⚠ 命名是加值：排不進去也不得讓已完成的增量分群變成失敗。"""
        with mock.patch.object(handlers, "_enqueue_topic_label",
                               side_effect=RuntimeError("queue down")):
            handlers._enqueue_topic_label_for_new_topics(_summary(["T006"]))


class IncrementalSummaryShapeTests(unittest.TestCase):
    def test_defaults_to_empty(self):
        summary = IncrementalSummary(
            run_id=1, workspace_id=1, source_field="x", new_document_count=0,
            assignment_count=0, artifact_version=1, pca_updated=False,
            status="completed")
        self.assertEqual(summary.new_topic_codes, [])


if __name__ == "__main__":
    unittest.main()
