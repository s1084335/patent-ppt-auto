"""掃描上限由資料的自然解析度決定，不是固定點數（2026-08-09）。

## 為什麼固定 60 是錯的

λ 只在跨過「某個實際存在的成對距離值」時才改變分群結果——兩個 λ 之間若沒有
任何距離落在其中，它們產生**完全相同**的分群。所以掃描有數學上的自然終點：
**細分到步進細於區間內距離值的間隔時，再細分只是在掃空白**。

⚠ 實測：技術通道 n=35 有 595 對距離，P10–P60 區間內約 297 個值——固定上限 60
只有它的五分之一。需要細掃時（相鄰群數的 λ 區間很窄）會提早停，標成未收斂，
或更糟：碰巧兩輪相同而**假收斂**。

## 兩道防線

1. **解析度上限**（資料驅動）：區間內實際距離值的數量。超過它，新增的點必定
   與鄰點同結果。
2. ~~時間預算~~：⚠ 2026-08-09 使用者定案**先不做**——「之後給使用者用有需要
   再來做這個機制」。不為想像中的需求預加設定項。
"""
from __future__ import annotations

import math
import unittest

from backend.app.clustering import engine


def _unit(*xs: float) -> list[float]:
    norm = math.sqrt(sum(x * x for x in xs)) or 1.0
    return [x / norm for x in xs]


def _blobs(groups: int, per_group: int):
    vectors, documents = [], []
    for g in range(groups):
        for m in range(per_group):
            v = [0.0] * groups
            v[g] = 1.0
            v[(g + 1) % groups] = 0.03 * (m + 1)
            vectors.append(_unit(*v))
            documents.append(f"topic{g} term{g}a term{g}b doc{m}")
    return vectors, documents


class ResolutionLimitTests(unittest.TestCase):
    def test_limit_comes_from_distinct_distances_in_range(self):
        """上限＝掃描區間內實際距離值的數量。"""
        distances = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        limit = engine.resolution_limit(distances, low=0.3, high=0.6)
        self.assertEqual(limit, 4, "0.3/0.4/0.5/0.6 共 4 個值")

    def test_scales_with_data_not_constant(self):
        """⚠ 這是重點：資料越多、距離值越密，上限越高。固定常數做不到。"""
        few = [i / 10 for i in range(11)]
        many = [i / 100 for i in range(101)]
        self.assertLess(engine.resolution_limit(few, low=0.2, high=0.8),
                        engine.resolution_limit(many, low=0.2, high=0.8))

    def test_duplicate_distances_count_once(self):
        """⚠ 重複的距離值不增加解析度——它們是同一個切點。"""
        self.assertEqual(
            engine.resolution_limit([0.5, 0.5, 0.5, 0.7], low=0.4, high=0.8), 2)

    def test_empty_range_reports_zero_resolution(self):
        """⚠ 這個函式回的是**資料的解析度**，空區間就是 0。

        保底（至少掃完第一輪）是呼叫端的責任——把兩件事混在一個回傳值裡，
        會讓「解析度」這個量失去意義。
        """
        self.assertEqual(engine.resolution_limit([0.1, 0.9], low=0.4, high=0.5), 0)

    def test_caller_still_sweeps_when_resolution_is_zero(self):
        """對照組：解析度為 0 時 select_lambda 仍要能產出結果。"""
        vectors, documents = _blobs(3, 5)
        result = engine.select_lambda(vectors, documents=documents)
        self.assertGreater(result.sweep_points, 0)


class NoFixedPointCapTests(unittest.TestCase):
    def test_can_exceed_old_fixed_cap_when_data_needs_it(self):
        """⚠ 資料需要時要能掃超過舊的 60 點上限。

        驗法：時間預算給足、資料的解析度上限遠高於 60 時，實作不得被 60 擋住。
        """
        self.assertFalse(hasattr(engine, "SWEEP_MAX_POINTS"),
                         "固定點數上限應已移除，改由解析度與時間預算決定")


if __name__ == "__main__":
    unittest.main()
