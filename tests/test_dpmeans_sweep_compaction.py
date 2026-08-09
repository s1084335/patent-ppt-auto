"""掃描表寫進 DB 前先精簡（2026-08-09 使用者定案）。

## 為什麼要精簡

實測：18 列完整掃描表 4,410 bytes，與同一個 run 的 topics（4,220 bytes）同量級
——等於讓 `topic_state_json` **翻倍**。而被刷掉的列，品質指標本來就是 `None`
（三層漏斗根本沒算它們），保存價值很低。

⚠ **關鍵**：掃描是**決定性的**——同一批資料重跑必得同一張表（n=44 約 6 秒）。
所以沒必要全存，需要完整指標時重算即可。

## 保留原則

- **選中那列完整保留**：回答「為什麼是這個 λ」。
- **其餘只留 (lambda, topic_count, failed)**：回答「其他點為什麼不行」——
  ⚠ 這一項不能省。只存選中列的話，使用者無從判斷它是不是剛好卡在邊緣。
"""
from __future__ import annotations

import json
import unittest

from backend.app.clustering import engine


def _row(lambda_, topic_count, failed=(), **extra):
    row = {
        "lambda": lambda_, "topic_count": topic_count, "median_size": 5,
        "singleton_doc_share": 0.0, "between_min": 0.8,
        "coherence": 0.55, "diversity": 0.44, "balance": 1.0,
        "small_topic_ratio": 0.0, "score": 0.75, "failed": list(failed),
        "stability_spread": 0,
    }
    row.update(extra)
    return row


SWEEP = [
    _row(0.10, 20, ["median_size"]),
    _row(0.50, 5),
    _row(0.60, 5),
    _row(0.90, 3, ["stability"]),
    _row(1.00, 1, ["single_cluster"]),
]


class CompactionTests(unittest.TestCase):
    def test_chosen_row_keeps_all_fields(self):
        compact = engine.compact_sweep(SWEEP, chosen_lambda=0.50)
        chosen = [r for r in compact if r["lambda"] == 0.50][0]
        self.assertEqual(chosen, SWEEP[1], "選中列要完整——它回答「為什麼是這個 λ」")

    def test_other_rows_keep_only_shape_and_reason(self):
        compact = engine.compact_sweep(SWEEP, chosen_lambda=0.50)
        other = [r for r in compact if r["lambda"] == 0.60][0]
        self.assertEqual(set(other), {"lambda", "topic_count", "failed"})

    def test_failed_rows_keep_their_reason(self):
        """⚠ 淘汰原因不能省——沒有它就看不出選中點是不是卡在邊緣。"""
        compact = engine.compact_sweep(SWEEP, chosen_lambda=0.50)
        by_lambda = {r["lambda"]: r for r in compact}
        self.assertEqual(by_lambda[0.90]["failed"], ["stability"])
        self.assertEqual(by_lambda[1.00]["failed"], ["single_cluster"])

    def test_all_rows_preserved_in_order(self):
        """列數與順序不變——精簡的是每列的欄位，不是丟掉整列。"""
        compact = engine.compact_sweep(SWEEP, chosen_lambda=0.50)
        self.assertEqual([r["lambda"] for r in compact], [r["lambda"] for r in SWEEP])

    def test_meaningfully_smaller(self):
        full = len(json.dumps(SWEEP).encode())
        compact = len(json.dumps(engine.compact_sweep(SWEEP, chosen_lambda=0.50)).encode())
        self.assertLess(compact, full * 0.5, f"精簡後 {compact} vs 原 {full}，省得不夠多")

    def test_no_chosen_lambda_still_works(self):
        """⚠ 全軍覆沒時沒有選中列——不得因此 raise。"""
        compact = engine.compact_sweep(SWEEP, chosen_lambda=None)
        self.assertEqual(len(compact), len(SWEEP))
        for row in compact:
            self.assertEqual(set(row), {"lambda", "topic_count", "failed"})

    def test_empty_sweep(self):
        self.assertEqual(engine.compact_sweep([], chosen_lambda=0.5), [])


if __name__ == "__main__":
    unittest.main()
