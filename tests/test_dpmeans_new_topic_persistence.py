"""DP-Means 新主題不得被「改派最近 active 主題」吞掉（tasks 2.4 Red）。

## 這是本 change 最容易失敗、也最不會報錯的一點

現行增量指派（`_persist_incremental_assignments`）對**未知 model topic ID**
的處理是「改派 centroid 最近的 active 主題」。那條 fallback 是 2026-07-27 為
KMeans 寫的，前提是「MiniBatchKMeans 不產生新 ID，未知 ID 只可能來自使用者
合併／停用主題」——這個前提在 DP-Means 下**不再成立**。

⚠ 若原封不動沿用：DP-Means 新開的群編號就是未知 ID，會被靜默併進最近的舊
主題。結果是「跑完沒有錯誤、新主題一個都沒有」，而這正是本 change 要解決的
問題本身。所以新主題與未知 ID 必須**在指派前就分開**。

本檔只測純函式映射，不碰 DB——DB 那層的責任是照著這份計畫寫入。
"""
from __future__ import annotations

import unittest

from backend.app.clustering import engine


class NewTopicSeparationTests(unittest.TestCase):
    """新主題 → 建新 topic_code；未知舊 ID → 才走 fallback。"""

    MODEL_TO_CODE = {0: "T001", 1: "T002"}

    def test_new_topic_gets_new_code_not_fallback(self):
        plan = engine.plan_topic_keys(
            predicted_topics=[0, 2], new_topic_indexes=[2],
            model_to_code=self.MODEL_TO_CODE, existing_codes=["T001", "T002"])
        self.assertEqual(plan.topic_keys[0], "T001")
        self.assertNotIn(plan.topic_keys[1], ("T001", "T002"),
                         "⚠ 新主題被併進舊主題＝本 change 白做")
        self.assertEqual([t.model_topic_id for t in plan.new_topics], [2])

    def test_unknown_old_id_still_needs_fallback(self):
        """⚠ 對照組：不是新主題的未知 ID（使用者合併／停用造成）仍要走 fallback。

        兩者都是「不在 model_to_code 裡」，差別只在有沒有出現在
        `new_topic_indexes`。分不開就會二選一地錯。
        """
        plan = engine.plan_topic_keys(
            predicted_topics=[7], new_topic_indexes=[],
            model_to_code=self.MODEL_TO_CODE, existing_codes=["T001", "T002"])
        self.assertIsNone(plan.topic_keys[0], "未知舊 ID 交給呼叫端做 centroid fallback")
        self.assertEqual(plan.new_topics, [])

    def test_new_codes_do_not_collide_with_existing(self):
        plan = engine.plan_topic_keys(
            predicted_topics=[5, 6], new_topic_indexes=[5, 6],
            model_to_code={}, existing_codes=["T001", "T002", "T003"])
        codes = [t.topic_code for t in plan.new_topics]
        self.assertEqual(len(set(codes)), 2, "兩個新主題不得撞同一個 code")
        self.assertFalse(set(codes) & {"T001", "T002", "T003"})

    def test_same_new_topic_twice_creates_one_code(self):
        """同一批有兩份文件落進同一個新主題 → 只建一個主題。"""
        plan = engine.plan_topic_keys(
            predicted_topics=[3, 3], new_topic_indexes=[3],
            model_to_code=self.MODEL_TO_CODE, existing_codes=["T001"])
        self.assertEqual(len(plan.new_topics), 1)
        self.assertEqual(plan.topic_keys[0], plan.topic_keys[1])

    def test_new_topics_are_marked_for_labeling(self):
        """CLU-004：新主題要排 ai:topic_label；既有主題的人工命名不得被覆蓋。"""
        plan = engine.plan_topic_keys(
            predicted_topics=[0, 4], new_topic_indexes=[4],
            model_to_code=self.MODEL_TO_CODE, existing_codes=["T001", "T002"])
        self.assertEqual(plan.topic_codes_needing_label,
                         [t.topic_code for t in plan.new_topics])
        self.assertNotIn("T001", plan.topic_codes_needing_label)

    def test_kmeans_batch_plans_no_new_topics(self):
        """⚠ 對照組：舊引擎沒有新主題，這條路徑不得改變它的行為。"""
        plan = engine.plan_topic_keys(
            predicted_topics=[0, 1, 0], new_topic_indexes=[],
            model_to_code=self.MODEL_TO_CODE, existing_codes=["T001", "T002"])
        self.assertEqual(plan.topic_keys, ["T001", "T002", "T001"])
        self.assertEqual(plan.new_topics, [])
        self.assertEqual(plan.topic_codes_needing_label, [])

    def test_empty_batch(self):
        plan = engine.plan_topic_keys(
            predicted_topics=[], new_topic_indexes=[],
            model_to_code=self.MODEL_TO_CODE, existing_codes=["T001"])
        self.assertEqual(plan.topic_keys, [])
        self.assertEqual(plan.new_topics, [])


class TopicCodeGenerationTests(unittest.TestCase):
    """新 topic_code 要延續既有命名，不能自創另一套格式。"""

    def test_code_follows_existing_format(self):
        plan = engine.plan_topic_keys(
            predicted_topics=[9], new_topic_indexes=[9],
            model_to_code={}, existing_codes=["T001", "T002", "T003"])
        self.assertEqual(plan.new_topics[0].topic_code, "T004")

    def test_code_starts_at_one_when_no_existing(self):
        plan = engine.plan_topic_keys(
            predicted_topics=[0], new_topic_indexes=[0],
            model_to_code={}, existing_codes=[])
        self.assertEqual(plan.new_topics[0].topic_code, "T001")

    def test_gap_in_existing_codes_does_not_reuse(self):
        """⚠ 不得填補空號：T02 被刪過就是被刪過，重用會讓舊報表對到新主題。"""
        plan = engine.plan_topic_keys(
            predicted_topics=[0], new_topic_indexes=[0],
            model_to_code={}, existing_codes=["T001", "T003"])
        self.assertEqual(plan.new_topics[0].topic_code, "T004")


class NewTopicEntryTests(unittest.TestCase):
    """新主題寫進 topic_state_json 的形狀。

    ⚠ 增量 run 原本**不帶 topics**（`_latest_state_run` 只選 topics 非空的 run），
    topics 一律來自最新 finalize run。DP-Means 長出新主題時，這個 incremental
    run 必須自己寫出**完整**清單（既有 + 新），否則新主題等於沒存進去。
    """

    EXISTING = [
        {"topic_id": 1, "topic_code": "T001", "label": "手工命名", "label_source": "human",
         "status": "active", "display_order": 1},
        {"topic_id": 2, "topic_code": "T002", "label": "另一個", "label_source": "ai",
         "status": "active", "display_order": 2},
    ]

    def _entries(self, doc_counts=None):
        plan = engine.plan_topic_keys(
            predicted_topics=[0, 5], new_topic_indexes=[5],
            model_to_code={0: "T001"}, existing_codes=["T001", "T002"])
        return engine.build_topic_entries(
            existing_topics=self.EXISTING, new_topics=plan.new_topics,
            source_field="wips_independent_claims", run_id=42,
            doc_counts=doc_counts or {"T003": 1})

    def test_existing_topics_are_preserved_verbatim(self):
        """⚠ 既有主題**原樣保留**——人工命名與 label_source 不得被增量覆寫。"""
        entries = self._entries()
        self.assertEqual(entries[0], self.EXISTING[0])
        self.assertEqual(entries[1], self.EXISTING[1])

    def test_new_topic_appended_with_required_fields(self):
        new = self._entries()[-1]
        self.assertEqual(new["topic_code"], "T003")
        self.assertEqual(new["topic_kind"], "model")
        self.assertEqual(new["status"], "active")
        self.assertEqual(new["created_run_id"], 42)
        self.assertEqual(new["source_field"], "wips_independent_claims")
        self.assertEqual(new["model_topic_ids"], [5])
        self.assertEqual(new["doc_count"], 1)

    def test_new_topic_label_is_placeholder_awaiting_ai(self):
        """⚠ label_source 必須是 fallback：DP-Means 增量沒有 c-TF-IDF 關鍵詞，
        這個名字只是佔位，等 ai:topic_label 覆蓋。標成 ai／human 會讓它永遠不被命名。
        """
        new = self._entries()[-1]
        self.assertEqual(new["label_source"], "fallback")
        self.assertTrue(new["label"])
        self.assertEqual(new["keywords"], [])

    def test_display_order_continues_after_existing(self):
        self.assertEqual(self._entries()[-1]["display_order"], 3)

    def test_topic_id_does_not_collide(self):
        self.assertEqual(self._entries()[-1]["topic_id"], 3)

    def test_no_new_topics_returns_existing_unchanged(self):
        """⚠ 對照組：沒有新主題時回傳既有清單，呼叫端據此判斷「不必寫 topics」。"""
        entries = engine.build_topic_entries(
            existing_topics=self.EXISTING, new_topics=[],
            source_field="wips_independent_claims", run_id=42, doc_counts={})
        self.assertEqual(entries, self.EXISTING)


if __name__ == "__main__":
    unittest.main()
