"""DP-Means 自行抽取主題關鍵詞（tasks 3.1 Red）。

## 為什麼要自己抽

2026-08-09 使用者指出「主題一致性與主題多樣性可以繼續用」。查證後確認：
`topic_cv_coherence_per_topic` 與 `topic_diversity` 都只需要 `top_terms`
（每群的關鍵詞清單），**不綁 BERTopic**——gensim 算 c_v 用的是文件本身。

所以只要 DP-Means 自己產 top terms，兩個既有指標就完全可用。
⚠ 這推翻了先前「DP-Means 沒有 c-TF-IDF 所以指標填 None」的判斷。

## 做法

class-TF-IDF：把每群的文件當成一個「類別文件」算詞頻，再除以該詞在全體類別
中的普遍程度。與 BERTopic 的 c-TF-IDF 同原理，但只用既有 tokenizer，不引入
新依賴。

⚠ 關鍵詞仍**不得**進 CLI payload——`ai_topic_label_runner` 的紅線黑名單不變。
它們的用途是品質指標與前端顯示，不是餵給 LLM 命名。
"""
from __future__ import annotations

import unittest

from backend.app.clustering import keywords


class ClassTfidfTests(unittest.TestCase):
    DOCS = [
        "ski machine belt drive motor control",
        "ski machine belt drive speed control",
        "treadmill running deck cushion shock",
        "treadmill running deck cushion damping",
    ]
    LABELS = [0, 0, 1, 1]

    def test_returns_terms_per_topic(self):
        result = keywords.extract_top_terms(self.DOCS, labels=self.LABELS, limit=3)
        self.assertEqual(sorted(result), [0, 1])
        self.assertLessEqual(len(result[0]), 3)

    def test_terms_are_distinctive_not_merely_frequent(self):
        """⚠ 這是 class-TF-IDF 的重點：挑**這群才有**的詞，不是最常出現的詞。

        兩群都有的詞（如果存在）不該排在前面——否則兩個主題的關鍵詞會長得
        一模一樣，多樣性歸零，使用者看到兩張講一樣事情的卡片。
        """
        result = keywords.extract_top_terms(self.DOCS, labels=self.LABELS, limit=4)
        # ⚠ 不斷言某個特定詞入選：本案例中群 0 的五個詞 tf 與 df 全部相同，
        # 分數並列，誰進前四由固定的次要排序鍵決定——那是可重現性的設計，
        # 不是辨識度。要驗的是**跨群不污染**。
        self.assertIn("treadmill", result[1])
        self.assertNotIn("treadmill", result[0])
        self.assertNotIn("ski", result[1])
        self.assertFalse(set(result[0]) & set(result[1]),
                         "兩個明顯不同的主題，關鍵詞不該有交集")

    def test_singleton_topic_still_gets_terms(self):
        """⚠ 單件主題也要有關鍵詞——否則它在前端是一張空卡片。"""
        result = keywords.extract_top_terms(
            [*self.DOCS, "elliptical trainer stride length"],
            labels=[*self.LABELS, 2], limit=3)
        self.assertTrue(result[2])

    def test_empty_input_returns_empty(self):
        self.assertEqual(keywords.extract_top_terms([], labels=[], limit=3), {})

    def test_deterministic(self):
        """同樣輸入必得同樣關鍵詞——指標要可重現。"""
        first = keywords.extract_top_terms(self.DOCS, labels=self.LABELS, limit=3)
        second = keywords.extract_top_terms(self.DOCS, labels=self.LABELS, limit=3)
        self.assertEqual(first, second)

    def test_limit_respected(self):
        result = keywords.extract_top_terms(self.DOCS, labels=self.LABELS, limit=2)
        for terms in result.values():
            self.assertLessEqual(len(terms), 2)

    def test_blank_documents_do_not_crash(self):
        """⚠ 空文本會出現（該欄位缺值），不得讓整批指標計算失敗。"""
        result = keywords.extract_top_terms(
            ["", "  ", "ski machine"], labels=[0, 0, 1], limit=3)
        self.assertEqual(result.get(0, []), [])
        self.assertTrue(result[1])


class DiversityIntegrationTests(unittest.TestCase):
    """抽出的 top terms 要能直接餵既有 topic_diversity。"""

    def test_distinct_topics_have_high_diversity(self):
        from backend.app.clustering.model import topic_diversity

        terms = keywords.extract_top_terms(
            ClassTfidfTests.DOCS, labels=ClassTfidfTests.LABELS, limit=3)
        self.assertGreater(topic_diversity(terms), 0.8,
                           "兩個明顯不同的主題，關鍵詞不該重疊")


if __name__ == "__main__":
    unittest.main()
