"""封面三分法必須吃母體（tasks §1.4）——同型錯誤的第 3 例。

## 症狀

封面顯示 **281 件（設計 21）**，滑雪機 workspace 實際是 **55 件（設計 11）**。
根因單純到不可思議：

```sql
SELECT patent_type, document_kind FROM derived_layer.report_patent_base
```

**沒有 WHERE。** 不論報表跑哪個 workspace，它都撈全庫。

⚠ 而且不報錯——封面數字看起來完全正常，只是全錯。

## 為什麼把 `patent_ids` 做成必填

三問的 Q2 問「滿足它的唯一途徑是不是把事情做對」。
若給預設值（`patent_ids=None` 代表全庫），呼叫端**忘記傳**時會靜默退回全庫——
那正是現在這個 bug 的形狀，只是換個寫法重來一次。

必填的話「忘記傳」是 `TypeError`，當場炸。這比閘門強：不是「事後檢查有沒有做對」，
而是「**做不對就跑不起來**」（恆等式，零自由度）。

全庫用途仍可用——明確傳整包 id 進來，意圖寫在呼叫端而不是靠預設值。
"""
from __future__ import annotations

import inspect
import unittest
from unittest import mock

from backend.app.reports import chart_runner


class PatentIdsIsRequiredTests(unittest.TestCase):
    def test_patent_ids_has_no_default(self):
        """🔴 必填：忘記傳就 TypeError，不會靜默退回全庫。"""
        sig = inspect.signature(chart_runner.fetch_patent_kind_summary)
        self.assertIn("patent_ids", sig.parameters,
                      "沒有 patent_ids 參數——它會繼續撈全庫")
        param = sig.parameters["patent_ids"]
        self.assertIs(
            param.default, inspect.Parameter.empty,
            "patent_ids 有預設值——呼叫端忘記傳就靜默退回全庫，"
            "那正是本 bug 的形狀（封面 281 vs 實際 55）")


class SqlIsScopedTests(unittest.TestCase):
    """側錄實際送出的 SQL——只讀原始碼會被字串拼接騙過。"""

    def _executed_sql(self, patent_ids):
        seen: list[tuple] = []

        class Cur:
            def execute(self, sql, params=None):
                seen.append((str(sql), params))

            def fetchall(self):
                return []

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Conn(Cur):
            def cursor(self, **kw):
                return Cur()

        with mock.patch.object(chart_runner, "_app_layer_connect", lambda: Conn()):
            chart_runner.fetch_patent_kind_summary(patent_ids=patent_ids)
        self.assertTrue(seen, "沒有送出任何 SQL")
        return seen[0]

    def test_sql_filters_by_patent_ids(self):
        """🔴 核心：SQL 要真的帶母體條件，而且 id 要進 params。"""
        sql, params = self._executed_sql([11, 22, 33])
        self.assertRegex(
            sql, r"(?i)\bWHERE\b",
            "SQL 沒有 WHERE——不論跑哪個 workspace 都會撈全庫")
        self.assertRegex(sql, r"(?i)patent_id",
                         "WHERE 沒有限定 patent_id")
        flat = []
        for p in (params or ()):
            flat.extend(p if isinstance(p, (list, tuple)) else [p])
        self.assertIn(11, flat, "patent_ids 沒有被當成參數帶進去")

    def test_empty_patent_ids_is_not_silently_whole_db(self):
        """⚠ 空母體要回空結果，不得退回全庫。

        「這個 workspace 沒有成員」與「全庫」是兩件完全不同的事，
        靜默退回全庫會讓封面數字看起來很正常。
        """
        sql, params = self._executed_sql([])
        self.assertRegex(sql, r"(?i)\bWHERE\b",
                         "空母體時 SQL 沒有 WHERE——退回全庫了")


class CallerPassesScopeTests(unittest.TestCase):
    def test_run_chart_trial_passes_the_real_scope(self):
        """呼叫端要把 **ctx 的母體** 傳下去——改對函式但沒改呼叫端等於沒修。

        ⚠ 2026-08-18 變異檢查修正：原本只斷言 `patent_ids=` 出現，
        結果把呼叫改成 `patent_ids=None` 照樣綠——那等於「有傳這個字，但傳的是
        全庫」。**只驗參數名不驗值**是假性通過的固定型態，本專案今天已踩過三次。
        """
        src = inspect.getsource(chart_runner.run_chart_trial)
        self.assertRegex(
            src, r"fetch_patent_kind_summary\(\s*patent_ids\s*=\s*ctx\.patent_ids",
            "run_chart_trial 沒有把 ctx.patent_ids 傳下去——"
            "傳 None 或寫死值等於退回全庫（封面 281 vs 實際 55 就是這樣來的）")


if __name__ == "__main__":
    unittest.main()
