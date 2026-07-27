"""TopicResponse.keywords 型別契約（2026-07-27 實機 500 回歸測試）。

症狀：分類區「主題載入失敗（HTTP 500）」，容器 traceback：

    ValidationError: 10 validation errors for TopicResponse
    keywords.0  Input should be a valid string
      input_value={'term': 'hedge', 'weight': 0.0579...}, input_type=dict

根因：寫入端與讀取端型別不一致——
- 寫入（clustering/runner.py `_persist_final_topics`）存
  `[{"term": str, "weight": float}, ...]`；
- 讀取（api/topics.py `TopicResponse`）宣告 `keywords: list[str]`。

只要 run 有正式 topics，list_topics 必炸 500。此為與 model_artifact_hash
同一類的「同一欄位兩種落點」問題（見 decisions.md 2026-07-27）。

修法：改讀取端 schema 對齊實際存的結構（weight 有用途，不可為了型別丟掉），
不改寫入端。本測鎖住 keywords 能容納 {term, weight} 物件。
"""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.app.api.topics import TopicResponse


def _topic(**overrides):
    """組一筆最小 TopicResponse 欄位，keywords 由呼叫端指定。"""
    data = {
        "topic_key": "T001",
        "label": "測試主題",
        "summary": "",
        "doc_count": 12,
        "keywords": [],
        "label_source": "fallback",
        "display_order": 1,
        "status": "active",
        "merged_into_topic_key": None,
    }
    data.update(overrides)
    return data


class TopicKeywordsContractTests(unittest.TestCase):
    """keywords 必須容納寫入端實際產出的 {term, weight} 結構。"""

    def test_accepts_term_weight_objects(self):
        """c-TF-IDF 關鍵詞（含權重）不得讓回應驗證失敗。"""
        kws = [
            {"term": "hedge", "weight": 0.057923937126328304},
            {"term": "trimmer", "weight": 0.04360368230478471},
        ]
        resp = TopicResponse(**_topic(keywords=kws))
        self.assertEqual(len(resp.keywords), 2)
        # 權重要保留（供排序／顯示），不得在驗證過程被丟掉
        first = resp.keywords[0]
        term = first["term"] if isinstance(first, dict) else first.term
        weight = first["weight"] if isinstance(first, dict) else first.weight
        self.assertEqual(term, "hedge")
        self.assertAlmostEqual(weight, 0.057923937126328304)

    def test_empty_keywords_still_valid(self):
        """系統桶主題（UNCLASSIFIED／OTHER）沒有 keywords，須照常通過。"""
        resp = TopicResponse(**_topic(keywords=[]))
        self.assertEqual(resp.keywords, [])

    def test_rejects_malformed_keyword_entry(self):
        """缺 term 的項目仍應被擋下，不因放寬而失去驗證能力。"""
        with self.assertRaises(ValidationError):
            TopicResponse(**_topic(keywords=[{"weight": 0.5}]))


if __name__ == "__main__":
    unittest.main()
