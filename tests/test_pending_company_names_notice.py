"""#15 匯入後主動告知新增待補中文名（2026-07-28 定案，2026-08-04 實作）。

三層收斂只吸收大小寫與空白差異；標點差異與全新公司要人工補，
但使用者不會知道有新的要補。匯入回報帶「新增待補名稱數」＋前端指路。
⚠ 只告知數量、不預先分組（同日定案）。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from backend.app.api.company_aliases import (
    COUNT_PENDING_FOR_PATENTS_SQL,
    count_pending_names_for_patents,
)


class CountFnTests(unittest.TestCase):
    def test_empty_batch_short_circuits(self):
        cur = mock.Mock()
        self.assertEqual(count_pending_names_for_patents(cur, []), 0)
        cur.execute.assert_not_called()

    def test_query_scopes_to_batch_and_confirmed_exclusion(self):
        """SQL 必須①限定本批 patent_ids ②排除 confirmed 別稱 ③拆 ` | ` 多值。"""
        cur = mock.Mock()
        cur.fetchone.return_value = (8,)
        n = count_pending_names_for_patents(cur, [1, 2, 3])
        self.assertEqual(n, 8)
        sql, params = cur.execute.call_args[0]
        self.assertIn("ANY(%(patent_ids)s)", sql)
        self.assertIn("review_status = 'confirmed'", sql)
        self.assertIn("regexp_split_to_table", sql, "` | ` 多值沒拆——會出現假公司名")
        self.assertEqual(params, {"patent_ids": [1, 2, 3]})

    def test_normalize_rule_matches_pending_codes_sql(self):
        """⚠ normalize 規則要與 _PENDING_CODES_SQL 同一把 lookup_key——
        兩邊不同步時，待補清單與匯入提示的數字會對不起來。"""
        from backend.app.api.company_aliases import _PENDING_CODES_SQL

        rule = "lower(regexp_replace(BTRIM(part), '\\s+', ' ', 'g'))"
        self.assertIn(rule, COUNT_PENDING_FOR_PATENTS_SQL)
        self.assertIn(rule, _PENDING_CODES_SQL)


class HandlerWiringTests(unittest.TestCase):
    def test_summary_gets_count(self):
        from backend.app.worker import handlers

        summary = {"patent_ids": [7, 8]}
        with mock.patch("psycopg.connect") as connect:
            cur = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = (5,)
            handlers._count_pending_company_names(summary)
        self.assertEqual(summary["pending_company_names"], 5)

    def test_failure_is_isolated(self):
        """計數炸掉不得影響匯入——只回填 error 欄。"""
        from backend.app.worker import handlers

        summary = {"patent_ids": [7]}
        with mock.patch("psycopg.connect", side_effect=RuntimeError("db down")):
            handlers._count_pending_company_names(summary)
        self.assertIn("pending_company_names_error", summary)
        self.assertNotIn("pending_company_names", summary)


class FrontendNoticeTests(unittest.TestCase):
    def test_result_card_shows_pending_names(self):
        html = (Path(__file__).resolve().parents[1] / "backend" / "app" / "static"
                / "index.html").read_text(encoding="utf-8")
        self.assertIn("pending_company_names", html)
        self.assertIn("未對照的專利權人名稱", html)


if __name__ == "__main__":
    unittest.main()
