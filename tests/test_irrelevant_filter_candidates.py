"""不相干篩選候選挑選契約（2026-07-27 補斷鏈）。

補的斷鏈：`_run_ai_irrelevant_filter_job` 呼叫 runner 時**完全沒傳 candidates**，
而 runner 也不會自己去挑——`cand_list` 恆為空 → 走「無可判讀」early return →
**回報 succeeded 但 candidates:0 / judged:0 / stored:0**（實機 job 96 只跑 4.6 秒）。
註解宣稱「候選由 runner 內部依 c-TF-IDF 最低 N 筆取得」，但那段程式不存在
（`rank_ctfidf_least_representative_documents` 在 model.py:644，零呼叫端）。

## 為何用 distance_to_centroid 而非重載 artifact 跑 c-TF-IDF
`topic_assignments.distance_to_centroid` 在 finalize 時已算好落庫；距離**大**＝離主題
中心遠＝最不像該主題，與 c-TF-IDF 反向排序的目的一致。既有
`backfill_representative_patents` 正是用同一份距離取「最近的 N 筆」——這裡只是把排序
方向反過來取「最遠的 N 筆」，同源、同資料、零額外成本。

重載 BERTopic artifact 才能跑 c-TF-IDF，對一個「挑候選」的動作太重（artifact 動輒數十 MB，
且 Companion 在使用者本機、artifact 在容器裡——跨機取檔又是另一條斷鏈）。

🔴 沿用既有紅線：只回傳 (patent_id, note)，**不外流距離分數與 keywords**。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class SelectIrrelevantCandidatesTests(unittest.TestCase):
    """workspace_service.select_irrelevant_candidates：每主題取距離最遠的 N 筆。"""

    def test_picks_farthest_per_topic(self):
        """每主題依 distance_to_centroid 由大到小取樣（最不像該主題者優先）。"""
        from backend.app.clustering import workspace_service as ws

        # 兩個主題各 6 筆，距離刻意亂序；取樣數由 irrelevant_sample_size 決定。
        assignments = [
            (101, "T001", 0.1), (102, "T001", 0.9), (103, "T001", 0.5),
            (104, "T001", 0.3), (105, "T001", 0.8), (106, "T001", 0.2),
            (201, "T002", 0.7), (202, "T002", 0.1), (203, "T002", 0.6),
            (204, "T002", 0.2), (205, "T002", 0.4), (206, "T002", 0.3),
        ]
        topics = [
            {"topic_code": "T001", "label": "主題一", "status": "active", "topic_kind": "model"},
            {"topic_code": "T002", "label": "主題二", "status": "active", "topic_kind": "model"},
        ]
        picked = ws._rank_farthest_by_topic(assignments, topics)

        # T001 距離最大者依序 102(0.9)、105(0.8)、103(0.5)…
        self.assertEqual(picked["T001"][0], 102)
        self.assertEqual(picked["T001"][1], 105)
        # T002 距離最大者 201(0.7)、203(0.6)…
        self.assertEqual(picked["T002"][0], 201)
        self.assertEqual(picked["T002"][1], 203)

    def test_never_takes_whole_topic(self):
        """小主題安全閥：至少留一筆在主題內，永不取整題（沿 irrelevant_sample_size）。"""
        from backend.app.clustering import workspace_service as ws

        assignments = [(1, "T001", 0.5), (2, "T001", 0.9)]
        topics = [{"topic_code": "T001", "label": "小主題", "status": "active",
                   "topic_kind": "model"}]
        picked = ws._rank_farthest_by_topic(assignments, topics)
        self.assertLessEqual(len(picked.get("T001", [])), 1,
                             "2 筆的主題最多取 1 筆，不得取整題")

    def test_skips_inactive_and_non_model_topics(self):
        """停用主題與非 model 主題不取候選。"""
        from backend.app.clustering import workspace_service as ws

        assignments = [(i, "T009", 0.1 * i) for i in range(1, 8)]
        topics = [{"topic_code": "T009", "label": "已停用", "status": "merged",
                   "topic_kind": "model"}]
        picked = ws._rank_farthest_by_topic(assignments, topics)
        self.assertEqual(picked, {}, "停用主題不得產生候選")

    def test_returns_no_distance_scores(self):
        """🔴 只回 patent_id，不回距離分數（分數不外流）。"""
        from backend.app.clustering import workspace_service as ws

        assignments = [(i, "T001", 0.1 * i) for i in range(1, 9)]
        topics = [{"topic_code": "T001", "label": "主題", "status": "active",
                   "topic_kind": "model"}]
        picked = ws._rank_farthest_by_topic(assignments, topics)
        for value in picked["T001"]:
            self.assertIsInstance(value, int, "只能回 patent_id 整數，不得夾帶分數")


class BridgePassesCandidatesTests(unittest.TestCase):
    """ai_bridge 必須把候選傳給 runner——這是本次補的斷鏈本身。"""

    def test_bridge_passes_candidates_to_runner(self):
        """_run_ai_irrelevant_filter_job 呼叫 runner 時必須帶 candidates。"""
        from backend.app.worker import ai_bridge

        captured: dict = {}

        def _fake_run(**kwargs):
            captured.update(kwargs)
            return {"workspace_id": 1, "candidates": 2, "judged": 2,
                    "undecidable": 0, "stored": 2, "results": [],
                    "prompt_version": "v1", "cli_kind": "claude"}

        class _Ctx:
            def heartbeat(self, *a, **k):
                return None

        fake_groups = [
            {"topic_code": "T001", "topic_label": "主題一", "patent_ids": [11, 12]},
        ]
        with mock.patch.object(
                ai_bridge, "select_irrelevant_candidates", return_value=fake_groups), \
                mock.patch("backend.app.worker.ai_irrelevant_filter_runner.run_irrelevant_filter",
                           side_effect=_fake_run):
            ai_bridge._run_ai_irrelevant_filter_job({"workspace_id": 1}, _Ctx())

        self.assertIn("candidates", captured,
                      "bridge 未傳 candidates → runner 恆走空清單 early return")
        self.assertEqual(
            [pid for pid, _note in captured["candidates"]], [11, 12])
        self.assertEqual(captured.get("topic_label"), "主題一",
                         "topic_label 須一併帶下去（2026-07-24 第 1 題定案）")

    def test_bridge_skips_ai_when_no_candidates(self):
        """完全沒有候選時不呼叫 CLI（不空燒 token），回報 candidates:0。"""
        from backend.app.worker import ai_bridge

        called = {"n": 0}

        def _fake_run(**kwargs):
            called["n"] += 1
            return {}

        class _Ctx:
            def heartbeat(self, *a, **k):
                return None

        with mock.patch.object(ai_bridge, "select_irrelevant_candidates", return_value=[]), \
                mock.patch("backend.app.worker.ai_irrelevant_filter_runner.run_irrelevant_filter",
                           side_effect=_fake_run):
            result = ai_bridge._run_ai_irrelevant_filter_job({"workspace_id": 1}, _Ctx())

        self.assertEqual(called["n"], 0, "無候選不得呼叫 CLI")
        self.assertEqual(result.get("candidates"), 0)


if __name__ == "__main__":
    unittest.main()
