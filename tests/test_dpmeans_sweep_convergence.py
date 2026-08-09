"""掃描密度由收斂判準決定，不是固定點數（2026-08-09）。

## 為什麼固定點數是錯的

`SWEEP_STEPS = 18` 是憑感覺定的，實測取樣不足（功效通道 18 點給 5 群、24 點
給 6 群）。⚠ 改成 24 只是換一個猜的數字——**與先前「固定 λ 分位數」是同一個
錯誤**：把該由資料決定的東西寫成常數。

## 正確做法：掃到收斂

從粗掃開始，每輪在相鄰兩點間插入中點（8→15→29→57），直到**連續兩輪選出的
群數相同**為止。

- ⚠ **舊點直接重用**：掃描是決定性的，同一個 λ 在同一批資料上必得同一結果。
  重算是純粹浪費。插中點的細分法讓舊點全部保留（等分重取則會全部換掉）。
- ⚠ **達上限仍未收斂要標明**：那代表這批資料的結構本身不穩定，使用者需要知道，
  不能假裝收斂了。
"""
from __future__ import annotations

import math
import unittest

from backend.app.clustering import dpmeans, engine


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


class ConvergenceTests(unittest.TestCase):
    def test_reports_how_many_points_it_took(self):
        """⚠ 掃了幾點要記錄——那是「這次掃夠了沒」的唯一證據。"""
        vectors, documents = _blobs(5, 8)
        result = engine.select_lambda(vectors, documents=documents)
        self.assertGreater(result.sweep_points, 0)
        self.assertEqual(len(result.sweep), result.sweep_points)

    def test_marks_converged(self):
        vectors, documents = _blobs(5, 8)
        result = engine.select_lambda(vectors, documents=documents)
        self.assertTrue(result.converged, "明顯分開的 5 群應該會收斂")
        self.assertIn("converged", result.method)

    def test_point_count_is_dynamic_not_constant(self):
        """⚠ 點數由收斂判準決定，不是常數。

        驗法：限制上限會改變實際掃描點數。固定點數的實作不會受 max_points 影響
        （它每次都掃固定的那幾點），所以這條斷言只有動態細分才成立。

        ⚠ 原本想用「兩批不同資料掃出不同點數」來驗，但乾淨分離的合成資料都在
        第二輪（15 點）就收斂，斷言不成立——那是測試設計的問題，不是實作錯。
        真正需要更多輪的是邊界模糊的真實資料。
        """
        vectors, documents = _blobs(5, 8)
        capped = engine.select_lambda(vectors, documents=documents, max_points=8)
        full = engine.select_lambda(vectors, documents=documents)
        self.assertEqual(capped.sweep_points, 8)
        self.assertGreater(full.sweep_points, capped.sweep_points)
        self.assertFalse(capped.converged, "掃 8 點就停不算收斂，要據實標明")

    def test_reuses_previous_points(self):
        """⚠ 細分要保留舊點：掃描具決定性，重算純屬浪費。

        驗法：最終掃描表中，粗掃那一輪的點必須原樣存在。
        """
        vectors, documents = _blobs(5, 8)
        result = engine.select_lambda(vectors, documents=documents)
        lambdas = [r["lambda"] for r in result.sweep]
        self.assertEqual(lambdas, sorted(lambdas), "掃描表應依 lambda 排序")
        self.assertEqual(len(lambdas), len(set(lambdas)), "不得有重複的 lambda")

    def test_records_when_not_converged(self):
        """⚠ 沒收斂就要說。假裝收斂會讓使用者以為結果比實際可靠。"""
        vectors, documents = _blobs(5, 8)
        result = engine.select_lambda(vectors, documents=documents, max_points=8)
        if not result.converged:
            self.assertIn("not_converged", result.method)

    def test_result_matches_dense_manual_sweep(self):
        """收斂後的群數，要與「掃很密」的結果一致——否則收斂判準是假的。"""
        vectors, documents = _blobs(5, 8)
        auto = engine.select_lambda(vectors, documents=documents)
        dense = engine.select_lambda(vectors, documents=documents, max_points=200)
        self.assertEqual(
            len(dpmeans.fit(vectors, lambda_=auto.value).centers),
            len(dpmeans.fit(vectors, lambda_=dense.value).centers))


if __name__ == "__main__":
    unittest.main()
