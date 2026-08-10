"""稽核要能**重現**查詢，而不是把結果再存一遍（2026-08-10 使用者裁決）。

使用者原話：「但保存了只是同樣的東西在存一遍，也是沒有效率」。⚠ 這個判斷是對的，
而且它改變了修法方向：

| 工具 | 查的是什麼 | 結果能不能重現 | 該記什麼 |
|---|---|---|---|
| `list_report_catalog`／`query_report_evidence`／`preview_report_rows` | **snapshot**（report_data 的子集） | ✅ 同一個 snapshot_id 重跑必得同樣結果 | 參數即可——**已經在記** |
| `query_database` | **即時 DB**（會隨資料變動） | ⚠ 只能靠 SQL 重跑，而 DB 內容可能已變 | 完整 SQL ＋ 結果指紋 |

所以真正的缺口只有一個：`query_database` 的 SQL 原本**截斷到 200 字**，長查詢
重跑不了，等於那筆稽核只能證明「查過」，不能證明「查了什麼」。

修法（不複製資料）：
- SQL **不截斷**——它是重現的唯一依據
- 加 `row_hash`：結果的指紋。日後重跑若 hash 相同就證明資料沒變、結論仍成立；
  不同則明確知道要重新檢視。⚠ 指紋是定值大小，不隨結果列數膨脹。
"""
from __future__ import annotations

import unittest

from backend.app.mcp_server import report_research as rrs

LONG_SQL = "SELECT " + ", ".join(f'"欄位{i}"' for i in range(60)) + " FROM core_layer.patents"


class AuditReproducibilityTests(unittest.TestCase):
    """稽核要留下足以重跑的資訊，不留結果副本。"""

    def setUp(self):
        rrs.reset_query_audit()

    def test_sql_is_not_truncated(self):
        """完整 SQL 是重現的唯一依據，截斷等於重跑不了。"""
        source = rrs.__file__
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("sql=text[:200]", text,
                         "query_database 的稽核不得截斷 SQL——那是重現查詢的唯一依據")
        self.assertNotIn("sql=str(sql)[:200]", text)

    def test_audit_entry_carries_row_hash(self):
        """結果指紋讓「資料有沒有變」可查，且大小固定、不隨列數膨脹。"""
        rrs._audit("query_database", snapshot_id=None, sql="SELECT 1",
                   rows=2, truncated=False, error=None,
                   row_hash=rrs.rows_fingerprint([{"a": 1}, {"a": 2}]))
        entry = rrs.get_query_audit()[-1]
        self.assertIn("row_hash", entry)
        self.assertTrue(entry["row_hash"])

    def test_fingerprint_is_stable_and_order_independent_within_row(self):
        """同樣的資料要得到同樣的指紋；欄位順序不同不算資料不同。"""
        a = rrs.rows_fingerprint([{"x": 1, "y": 2}])
        b = rrs.rows_fingerprint([{"y": 2, "x": 1}])
        self.assertEqual(a, b)

    def test_fingerprint_changes_when_data_changes(self):
        """資料變了指紋就要變，否則證明不了任何事。"""
        self.assertNotEqual(
            rrs.rows_fingerprint([{"x": 1}]),
            rrs.rows_fingerprint([{"x": 2}]),
        )

    def test_fingerprint_is_fixed_size(self):
        """⚠ 指紋不得隨結果大小膨脹——那就變成「把結果存一遍」了。"""
        small = rrs.rows_fingerprint([{"x": 1}])
        large = rrs.rows_fingerprint([{"x": i, "y": "長" * 50} for i in range(500)])
        self.assertEqual(len(small), len(large))


if __name__ == "__main__":
    unittest.main()
