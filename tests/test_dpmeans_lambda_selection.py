"""校準時自動掃 lambda 並用判準挑選（tasks 3.1 Red）。

## 為什麼不能用固定分位數

先前設計是「lambda ＝ 全體兩兩距離的 P33」。⚠ 那等於假設「**所有批次**的專利，
第 33 百分位就是對的群半徑」——而那個假設只在滑雪機這一批的兩個通道驗過。
換一批專利（不同技術領域、不同撰寫風格、不同件數），分布形狀就變了。

## 正確的設計：把實驗變成流程

每批資料進來時自己掃自己的 lambda 區間，用四項判準過濾，再用主題一致性挑最佳：

| 判準 | 門檻 | 為什麼 |
|---|---|---|
| ① 中位群大小 | ≥ 3 件 | 典型主題要夠厚，少於 3 件講不出趨勢 |
| ② 單點群文件佔比 | ≤ 15% | ⚠ 用**文件**佔比不是群數佔比——小資料裡一兩個單件主題是可容許的 |
| ③ 群間最小距離 | ≥ 0.30 | 低於此表示兩個主題在講同一件事 |
| ④ 換順序穩定度 | 群數變動 ≤ 1 | DP-Means 順序敏感；變動大代表落在分界上 |

**掃描範圍也自適應**：用該批資料的距離分位當上下界，不寫死絕對值——⚠ 寫死
0.5–1.1 這種區間，換一批分布不同的資料就整段落在區間外。
"""
from __future__ import annotations

import math
import unittest

from backend.app.clustering import dpmeans, engine


def _unit(*xs: float) -> list[float]:
    norm = math.sqrt(sum(x * x for x in xs)) or 1.0
    return [x / norm for x in xs]


def _blobs(groups: int, per_group: int) -> tuple[list[list[float]], list[str]]:
    """造 N 個明顯分開的群，每群 M 筆。用高維讓群之間確實正交。"""
    vectors, documents = [], []
    for g in range(groups):
        for m in range(per_group):
            vector = [0.0] * groups
            vector[g] = 1.0
            # 加一點群內擾動，讓群內距離不為 0
            vector[(g + 1) % groups] = 0.02 * (m + 1)
            vectors.append(_unit(*vector))
            documents.append(f"topic{g} term{g}a term{g}b sample{m}")
    return vectors, documents


class LambdaSelectionTests(unittest.TestCase):
    def test_finds_lambda_matching_true_structure(self):
        """⚠ 這是整個設計的重點：資料有 4 個明顯的群，就該選出約 4 群的 lambda。"""
        vectors, documents = _blobs(groups=4, per_group=5)
        result = engine.select_lambda(vectors, documents=documents)
        self.assertIsNotNone(result.value)
        state = dpmeans.fit(vectors, lambda_=result.value)
        self.assertEqual(len(state.centers), 4)

    def test_adapts_to_different_structure(self):
        """⚠ 換一批資料（6 群）要選出不同的 lambda——這正是固定分位數做不到的事。"""
        four, docs4 = _blobs(groups=4, per_group=5)
        six, docs6 = _blobs(groups=6, per_group=4)
        a = engine.select_lambda(four, documents=docs4)
        b = engine.select_lambda(six, documents=docs6)
        self.assertEqual(len(dpmeans.fit(four, lambda_=a.value).centers), 4)
        self.assertEqual(len(dpmeans.fit(six, lambda_=b.value).centers), 6)

    def test_records_how_it_was_chosen(self):
        """CLU-008：值與**推導方法**都要留下，否則日後回答不了「為什麼是這個值」。"""
        vectors, documents = _blobs(groups=3, per_group=6)
        result = engine.select_lambda(vectors, documents=documents)
        self.assertIn("sweep", result.method)
        self.assertTrue(result.version)

    def test_reproducible(self):
        """同一批資料兩次校準必得同一個 lambda。"""
        vectors, documents = _blobs(groups=3, per_group=6)
        first = engine.select_lambda(vectors, documents=documents)
        second = engine.select_lambda(vectors, documents=documents)
        self.assertEqual(first.value, second.value)

    def test_falls_back_when_nothing_passes(self):
        """⚠ 全軍覆沒時要回退到分位數公式並**說明**，不得回 None 讓校準整個失敗。

        會發生在資料本身沒有結構時（全部很像、或全部互相遠離）。那是資料的
        性質，不是錯誤——仍要產出一個可用的 run，讓使用者看到結果再判斷。
        """
        # 全部幾乎相同：任何 lambda 都只會得到 1 群，③④ 無從判定
        identical = [_unit(1.0, 0.001 * i) for i in range(8)]
        result = engine.select_lambda(identical, documents=["same text"] * 8)
        self.assertIsNotNone(result.value)
        self.assertGreater(result.value, 0.0)
        self.assertIn("fallback", result.method)

    def test_sweep_range_is_data_relative(self):
        """⚠ 掃描範圍要由資料的距離分布決定，不得寫死絕對值。

        驗法：把所有向量的差異縮小一個數量級後，選出的 lambda 也該跟著縮小。
        寫死區間的話，縮小後的資料整段落在區間外，選不出東西。
        """
        vectors, documents = _blobs(groups=4, per_group=5)
        tight = [_unit(*[1.0 + 0.1 * x for x in v]) for v in vectors]
        wide = engine.select_lambda(vectors, documents=documents)
        narrow = engine.select_lambda(tight, documents=documents)
        self.assertLess(narrow.value, wide.value)

    def test_small_sample_still_returns_value(self):
        """⚠ 文件數低於分群門檻的情形由呼叫端擋；這裡不得 raise。"""
        result = engine.select_lambda(
            [_unit(1.0, 0.0), _unit(0.0, 1.0)], documents=["a", "b"])
        self.assertGreater(result.value, 0.0)


class DegenerateSolutionTests(unittest.TestCase):
    """⚠ 全部併成一群是退化解，不得被選中。

    2026-08-09 發現：判準③（群間最小距離）在只有 1 群時算不出值，回 None，
    而 None 原本被當成「通過」。四項判準全過，而且 diversity 對單一群回 1.0
    （沒有重疊是因為沒有第二組可比），反而拿到最高分。

    實測：5 個明顯分開的群、40 篇文件，最後選出的竟是「1 群 40 篇」。
    ⚠ 這種錯不會報錯——使用者看到的是「這批專利只有一個主題」。
    """

    def test_single_cluster_is_rejected(self):
        vectors, documents = _blobs(groups=5, per_group=8)
        result = engine.select_lambda(vectors, documents=documents)
        state = dpmeans.fit(vectors, lambda_=result.value)
        self.assertGreater(len(state.centers), 1,
                           "全部併成一群等於沒分群，不得被選中")

    def test_single_cluster_row_marked_failed(self):
        """掃描表要標明它為什麼被刷掉，不是靜默略過。"""
        vectors, documents = _blobs(groups=5, per_group=8)
        result = engine.select_lambda(vectors, documents=documents)
        singles = [r for r in result.sweep if r["topic_count"] == 1]
        self.assertTrue(singles, "本案例應該掃到至少一個會併成單群的 lambda")
        for row in singles:
            self.assertIn("single_cluster", row["failed"])

    def test_recovers_true_structure(self):
        """修正後應該找回真實的 5 群結構。"""
        vectors, documents = _blobs(groups=5, per_group=8)
        result = engine.select_lambda(vectors, documents=documents)
        self.assertEqual(len(dpmeans.fit(vectors, lambda_=result.value).centers), 5)


class SweepReportTests(unittest.TestCase):
    """掃描結果要能留給使用者看——為什麼選這個、其他被什麼判準刷掉。"""

    def test_report_lists_candidates_and_failures(self):
        vectors, documents = _blobs(groups=4, per_group=5)
        result = engine.select_lambda(vectors, documents=documents)
        self.assertTrue(result.sweep, "掃描表要留下，否則使用者無從判斷可信度")
        for row in result.sweep:
            self.assertIn("lambda", row)
            self.assertIn("topic_count", row)
            self.assertIn("failed", row)

    def test_chosen_row_passed_all_criteria(self):
        vectors, documents = _blobs(groups=4, per_group=5)
        result = engine.select_lambda(vectors, documents=documents)
        chosen = [r for r in result.sweep if r["lambda"] == result.value]
        self.assertTrue(chosen)
        self.assertEqual(chosen[0]["failed"], [])



class MetadataCompatibilityTests(unittest.TestCase):
    """⚠ `LambdaSelection` 與 `dpmeans.LambdaResult` 必須能互換餵進 run metadata。

    2026-08-09 實機驗收抓到：`build_run_metadata` 讀 `sample_size`，而
    `LambdaSelection` 少了那個欄位——AttributeError 直到 finalize 才炸。
    純函式測不出來，因為兩邊各自都是合法物件。
    """

    def test_selection_works_with_build_run_metadata(self):
        from backend.app.clustering import artifacts

        vectors, documents = _blobs(groups=3, per_group=6)
        selection = engine.select_lambda(vectors, documents=documents)
        meta = artifacts.build_run_metadata(
            algorithm=artifacts.ALGORITHM_DPMEANS, lambda_result=selection,
            pca_normalized=True, topics_before=0, topics_after=3)
        self.assertEqual(meta["lambda"]["value"], selection.value)
        self.assertEqual(meta["lambda"]["method"], selection.method)
        self.assertIsInstance(meta["lambda"]["sample_size"], int)

    def test_lambda_result_also_works(self):
        """對照組：舊型別不得因為新增型別而壞掉。"""
        from backend.app.clustering import artifacts, dpmeans as dp

        result = dp.derive_lambda([[1.0, 0.0], [0.0, 1.0], [0.9, 0.1]])
        meta = artifacts.build_run_metadata(
            algorithm=artifacts.ALGORITHM_DPMEANS, lambda_result=result)
        self.assertEqual(meta["lambda"]["value"], result.value)


if __name__ == "__main__":
    unittest.main()
