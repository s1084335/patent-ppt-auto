"""封面四個數字由引擎供給（tasks §2）——一方產生、一方消費。

## 為什麼

封面的件／族／受理局／專利類型三分法，現在是 **CLI 自己填**（範本 `stats` 四格
是 `["<N>", "件數"]` 這種占位）。CLI 手上沒有權威數字，只能從別的地方推——
封面顯示 281 件（實際 55）就是這樣來的。

⚠ 三分法**不得在封面自行判定**（2.3）：判別基準是 `transforms/patent_kind.py`
唯一定義處。封面自己比對 `document_kind` 等於第二份定義，兩份會各自演進。

## 家族口徑（2.2，順帶收掉 1.5b 記下的三個數字）

滑雪機有三個數字，語意不同：

| 數字 | 來源 | 用途 |
|---|---|---|
| **48** | `report_patent_base` 的 `COUNT(DISTINCT 家族ID)` 於母體 | **封面採這個** |
| 46 | `report_family_country` 各國家族數相加 | 加總錯誤，1.5 已修掉 |
| 40 | `report_family_country` 的 `DISTINCT family_id` | 該表收錄的家族（受保護國家有列的） |

⚠ 缺同族 ID 的專利**各自算一族**，不得併成一族「未知」——
`FAMILY_ID_EXPRESSION` 的 `COALESCE(..., 'P' || patent_id)` 已經是這個語意，
封面直接沿用，不另寫一份。
"""
from __future__ import annotations

import inspect
import unittest
from unittest import mock

from backend.app.reports import chart_runner


class CoverStatsExistsTests(unittest.TestCase):
    def test_engine_exposes_cover_stats(self):
        """🔴 核心：引擎要產出封面數字，CLI 才有東西可消費。"""
        self.assertTrue(
            hasattr(chart_runner, "fetch_cover_stats"),
            "引擎沒有供給封面數字——CLI 只能自己湊，那正是封面 281 的由來")

    def test_patent_ids_is_required(self):
        """與 1.4 同一條紀律：忘記傳母體要當場炸，不是靜默退回全庫。"""
        sig = inspect.signature(chart_runner.fetch_cover_stats)
        self.assertIn("patent_ids", sig.parameters)
        self.assertIs(
            sig.parameters["patent_ids"].default, inspect.Parameter.empty,
            "patent_ids 有預設值——呼叫端忘記傳就靜默退回全庫")

    def test_report_data_carries_cover_stats(self):
        src = inspect.getsource(chart_runner.run_chart_trial)
        self.assertRegex(
            src, r'"cover_stats"\s*:\s*fetch_cover_stats\(\s*patent_ids\s*=\s*ctx\.patent_ids',
            "report_data 沒有帶 cover_stats，或沒有把 ctx 的母體傳下去")


class ScopedSqlTests(unittest.TestCase):
    """側錄實際 SQL——母體閘門也會擋，但這裡驗它真的算對東西。"""

    def _run(self, patent_ids, rows_by_sql=None):
        seen: list[tuple] = []

        class Cur:
            def execute(self, sql, params=None):
                seen.append((str(sql), params))
                self._sql = str(sql)

            def fetchone(self):
                text = getattr(self, "_sql", "")
                for needle, value in (rows_by_sql or {}).items():
                    if needle in text:
                        return value
                return {"n": 0}

            def fetchall(self):
                return []

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Conn(Cur):
            def cursor(self, **kw):
                return Cur()

        with mock.patch.object(chart_runner, "_app_layer_connect", lambda: Conn()), \
                mock.patch.object(chart_runner, "fetch_patent_kind_summary",
                                  lambda **kw: {"tally": {"發明": 17, "新型": 27, "設計": 11},
                                                "summary": "", "design_note": ""}):
            result = chart_runner.fetch_cover_stats(patent_ids=patent_ids)
        return result, seen

    def test_every_query_is_scoped(self):
        _, seen = self._run([1, 2, 3])
        self.assertTrue(seen, "沒有送出任何 SQL")
        for sql, params in seen:
            with self.subTest(sql=sql[:60]):
                self.assertRegex(sql, r"(?i)\bWHERE\b", "封面數字的查詢沒有母體條件")
                self.assertRegex(sql, r"(?i)patent_id")

    def test_family_count_uses_the_single_definition(self):
        """家族 ID 運算式只能有一個定義處，不得在封面另寫一份。"""
        _, seen = self._run([1, 2, 3])
        from backend.app.reports.report_engine import FAMILY_ID_EXPRESSION

        joined = "\n".join(s for s, _ in seen)
        self.assertIn(
            FAMILY_ID_EXPRESSION, joined,
            "封面自己寫了一份家族 ID 運算式——兩份會各自演進而不報錯")

    def test_kind_split_delegates_not_recomputes(self):
        """三分法沿用 patent_kind 唯一定義處，封面不自行比對 document_kind。"""
        src = inspect.getsource(chart_runner.fetch_cover_stats)
        self.assertNotRegex(
            src, r"document_kind",
            "封面自行判定專利種類——判別基準的唯一定義處是 transforms/patent_kind")
        result, _ = self._run([1, 2, 3])
        self.assertEqual(result["kind_tally"], {"發明": 17, "新型": 27, "設計": 11})

    def test_returns_the_four_cover_numbers(self):
        result, _ = self._run([1, 2, 3])
        for key in ("patent_count", "family_count", "jurisdiction_count", "kind_tally"):
            self.assertIn(key, result, f"封面缺少 {key}")


if __name__ == "__main__":
    unittest.main()
