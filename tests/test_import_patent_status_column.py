"""匯入接「專利狀態」欄＋三護欄（2026-08-07 使用者定案「繼續做好」）。

背景：WIPS 有兩個狀態欄——`状态[US,JP,KR,CN,EP,CA,AU]`（既有來源）與
「專利狀態」（全國家；TW 案在前者恆空、在後者才有值，但目前匯入不吃）。

## 三護欄（覆蓋風險分析定案）

1. **優先序一處定義**：`状态[...]` 非空優先，空才取「專利狀態」——既有國家
   行為零改變，只有 TW（前者恆空）補得進來。
2. **人工登錄保護**：`legal_status_history` 含人工紀錄（無 source 鍵）的案，
   匯入不得靜默覆蓋 legal_status——記進衝突清單交使用者裁決。
3. **歷程不斷鏈**：匯入改動 legal_status 也 append 歷程（source: import），
   錯了追得到、回得去。
"""
from __future__ import annotations

import unittest
from typing import Any

from backend.app.importers import wips_importer


class StatusColumnMergeTests(unittest.TestCase):
    """護欄一：兩欄合併優先序（唯一定義處＝normalize_record）。"""

    def _norm(self, main: str | None, alt: str | None) -> str | None:
        raw: dict[str, Any] = {"申请号": "TW1234567", "国家代码": "TW"}
        if main is not None:
            raw["状态[US,JP,KR,CN,EP,CA,AU]"] = main
        if alt is not None:
            raw["專利狀態"] = alt
        return wips_importer.normalize_record(raw)["patent"].get("legal_status")

    def test_tw_fills_from_patent_status_column(self):
        self.assertEqual(self._norm(None, "已核准"), "已核准")

    def test_main_column_wins_when_both_present(self):
        self.assertEqual(self._norm("授权", "已核准"), "授权")

    def test_blank_alt_stays_none(self):
        self.assertIsNone(self._norm(None, "   "))
        self.assertIsNone(self._norm(None, None))


class _FakeCursor:
    def __init__(self):
        self.executed: list[tuple[str, Any]] = []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return None


class UpdateGuardsTests(unittest.TestCase):
    """護欄二＋三：人工保護與歷程 append 必須進到 UPDATE SQL 本體。"""

    def _sql(self) -> str:
        cur = _FakeCursor()
        wips_importer.update_patent_changed_fields(cur, 1, {"legal_status": "已核准"})
        return cur.executed[0][0]

    def test_manual_history_blocks_import_overwrite(self):
        sql = self._sql()
        # 人工紀錄＝歷程項目沒有 source 鍵；有任何一筆就不准匯入改 legal_status。
        self.assertIn("legal_status_history", sql)
        self.assertIn("'source'", sql)

    def test_import_change_appends_history_with_source(self):
        sql = self._sql()
        self.assertIn("'import'", sql)
        self.assertIn("jsonb_build_object", sql)


class ConflictReportTests(unittest.TestCase):
    """護欄二的可見性：被擋下的覆蓋要現形，不得靜默吞掉。"""

    def test_summary_key_exists_in_import_flow(self):
        import inspect

        src = inspect.getsource(wips_importer)
        self.assertIn("legal_status_conflicts", src,
                      "匯入 summary 沒有衝突清單——被擋下的覆蓋會靜默消失")


if __name__ == "__main__":
    unittest.main()
