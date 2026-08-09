"""DP-Means 全量定案（finalize）產出的主題形狀（tasks 2.4 Red）。

## 與 BERTopic finalize 的差別，以及為什麼可以接受

現行 finalize 依賴 BERTopic 的 c-TF-IDF 取關鍵詞與代表文檔。DP-Means 沒有
c-TF-IDF，所以：

- **關鍵詞**：⚠ 2026-08-09 修正——原本寫「不產」是**錯的**。coherence／diversity
  只需要 top_terms、不綁 BERTopic，所以改由 `clustering/keywords.py` 以
  class-TF-IDF 自行抽取。關鍵詞仍**不得**進 CLI payload（紅線黑名單不變）：
  給了關鍵字，LLM 會覆述關鍵詞而不是讀專利內容命名。
- **代表文檔**：改用「離中心最近的 N 篇」。這是向量直接算得出來的，不需要
  c-TF-IDF，且語意上就是「最能代表這群的文件」。

⚠ 因此 DP-Means 的主題**一定**要經過 ai:topic_label 才有像樣的名字，
`label_source` 必須留 `fallback`。
"""
from __future__ import annotations

import math
import unittest

from backend.app.clustering import dpmeans, engine


def _unit(*xs: float) -> list[float]:
    norm = math.sqrt(sum(x * x for x in xs)) or 1.0
    return [x / norm for x in xs]


class FinalizeTopicShapeTests(unittest.TestCase):
    # 兩群：x 軸附近三筆、y 軸附近兩筆
    VECTORS = [_unit(1.0, 0.0), _unit(0.99, 0.05), _unit(0.98, 0.1),
               _unit(0.0, 1.0), _unit(0.05, 0.99)]
    PATENT_IDS = [101, 102, 103, 201, 202]

    DOCUMENTS = ["ski belt drive", "ski belt motor", "ski drive control",
                 "treadmill deck cushion", "treadmill running damping"]

    def _plan(self, doc_limit=2):
        state = dpmeans.fit(self.VECTORS, lambda_=0.5)
        return state, engine.plan_finalize_topics(
            state=state, vectors=self.VECTORS, patent_ids=self.PATENT_IDS,
            documents=self.DOCUMENTS,
            source_field="wips_independent_claims", run_id=9,
            representative_limit=doc_limit)

    def test_one_topic_per_cluster(self):
        state, topics = self._plan()
        self.assertEqual(len(state.centers), 2)
        self.assertEqual(len(topics), 2)
        self.assertEqual([t["topic_code"] for t in topics], ["T001", "T002"])

    def test_doc_counts_match_membership(self):
        _, topics = self._plan()
        self.assertEqual(sorted(t["doc_count"] for t in topics), [2, 3])

    def test_keywords_extracted_and_label_awaits_ai(self):
        """關鍵詞由 class-TF-IDF 抽出；label_source 仍留 fallback 等 AI 命名。

        ⚠ label 不得用關鍵詞拼接：那正是 ai_topic_label_runner 紅線要擋的東西
        （關鍵詞拼接的舊 label 也在黑名單內）。
        """
        _, topics = self._plan()
        for topic in topics:
            self.assertTrue(topic["keywords"], "每個主題都要有關鍵詞，否則前端是空卡片")
            self.assertEqual(topic["label_source"], "fallback")
            self.assertTrue(topic["label"], "佔位名字仍要有，前端不得顯示空白")

    def test_keywords_do_not_overlap_between_topics(self):
        """⚠ 兩個主題的關鍵詞若一樣，使用者會看到兩張講同一件事的卡片。"""
        # keywords 的結構是 [{"term":..., "weight":...}]，與既有 finalize 同格式
        _, topics = self._plan()
        first = {k["term"] for k in topics[0]["keywords"]}
        second = {k["term"] for k in topics[1]["keywords"]}
        self.assertFalse(first & second)

    def test_representative_docs_are_nearest_to_center(self):
        """代表文檔＝離中心最近的 N 篇（AI 命名讀的就是這些）。"""
        _, topics = self._plan(doc_limit=1)
        first = topics[0]
        self.assertEqual(len(first["representative_patent_ids"]), 1)
        self.assertIn(first["representative_patent_ids"][0], (101, 102, 103))

    def test_representative_limit_respected(self):
        _, topics = self._plan(doc_limit=2)
        for topic in topics:
            self.assertLessEqual(len(topic["representative_patent_ids"]), 2)

    def test_every_topic_has_at_least_one_representative(self):
        """⚠ 一篇都沒有的主題，AI 命名無從下手——那等於這個主題永遠沒名字。"""
        _, topics = self._plan()
        for topic in topics:
            self.assertGreaterEqual(len(topic["representative_patent_ids"]), 1)

    def test_model_topic_ids_map_to_cluster_index(self):
        _, topics = self._plan()
        self.assertEqual([t["model_topic_ids"] for t in topics], [[0], [1]])

    def test_display_order_and_topic_id_start_at_one(self):
        _, topics = self._plan()
        self.assertEqual([t["topic_id"] for t in topics], [1, 2])
        self.assertEqual([t["display_order"] for t in topics], [1, 2])

    def test_empty_state_returns_no_topics(self):
        topics = engine.plan_finalize_topics(
            state=dpmeans.ClusterState(), vectors=[], patent_ids=[], documents=[],
            source_field="x", run_id=1, representative_limit=3)
        self.assertEqual(topics, [])


class FinalizeAssignmentTests(unittest.TestCase):
    """assignment 的 (patent_id, topic_key, distance) 三元組。"""

    VECTORS = [_unit(1.0, 0.0), _unit(0.0, 1.0)]

    def test_assignments_pair_patent_with_topic_code(self):
        state = dpmeans.fit(self.VECTORS, lambda_=0.5)
        rows = engine.plan_finalize_assignments(
            state=state, vectors=self.VECTORS, patent_ids=[11, 22])
        self.assertEqual([r[0] for r in rows], [11, 22])
        self.assertEqual([r[1] for r in rows], ["T001", "T002"])

    def test_distance_is_cosine_to_own_center(self):
        """⚠ 距離要用 cosine（與分群同一把尺），不能混用歐氏距離。"""
        state = dpmeans.fit(self.VECTORS, lambda_=0.5)
        rows = engine.plan_finalize_assignments(
            state=state, vectors=self.VECTORS, patent_ids=[11, 22])
        for _, _, distance in rows:
            self.assertAlmostEqual(distance, 0.0, places=6,
                                   msg="單點群的成員與中心距離應為 0")


if __name__ == "__main__":
    unittest.main()
