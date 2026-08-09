"""MCP 取證要留下稽核紀錄（A7，2026-08-09）。

## 為什麼需要

2026-08-09 使用者問「你要怎知道系統 CLI 有沒有去資料庫找證據來寫？」——當時
只能靠**事後翻 CLI transcript**（`~/.claude/projects/*/*.jsonl`）數 tool_use
才回答得出來。那是開發機上的檔案，正式部署根本沒有；而且它只在本機留存。

⇒ 取證通道自己要記：**誰查的、查了什麼、回幾列、有沒有截斷**。

## 邊界

⚠ audit 是**觀測**不是防護：它不阻擋任何查詢，只讓「查了沒有」與「查了什麼」
從推論變成事實。防護在別的地方（唯讀連線、單句 SELECT、白名單）。

⚠ 不記完整 SQL 的參數值：查詢字串本身會進紀錄（要能重現），但回傳的資料列
不進去——那是專利內容，稽核紀錄不該變成資料副本。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.mcp_server import report_research as rr


class AuditRecordingTests(unittest.TestCase):
    def setUp(self):
        rr.reset_query_audit()

    def test_snapshot_tool_records_one_entry(self):
        rr.query_report_evidence(
            "lifecycle", "v1",
            snapshot_loader=lambda _s: {"chart_rows": {"lifecycle": [{"a": 1}, {"a": 2}]}})
        audit = rr.get_query_audit()
        self.assertEqual(len(audit), 1)
        entry = audit[0]
        self.assertEqual(entry["tool"], "query_report_evidence")
        self.assertEqual(entry["snapshot_id"], "v1")
        self.assertEqual(entry["rows"], 2)
        self.assertFalse(entry["truncated"])

    def test_truncation_is_recorded(self):
        """⚠ 截斷必須進紀錄：CLI 以為拿到全部、實際只有一半，事後要查得出來。"""
        rows = [{"a": i} for i in range(10)]
        rr.query_report_evidence(
            "lifecycle", "v1", limit=3,
            snapshot_loader=lambda _s: {"chart_rows": {"lifecycle": rows}})
        entry = rr.get_query_audit()[0]
        self.assertEqual(entry["rows"], 3)
        self.assertTrue(entry["truncated"])

    def test_failed_query_is_recorded_too(self):
        """查失敗也要留痕——只記成功的話，看起來會像從來沒查過。"""
        with self.assertRaises(rr.ReportResearchError):
            rr.query_database("DROP TABLE patents")
        audit = rr.get_query_audit()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["tool"], "query_database")
        self.assertTrue(audit[0]["error"])

    def test_audit_keeps_query_but_not_returned_rows(self):
        """⚠ 記查詢、不記資料：稽核紀錄不該變成專利內容的副本。"""
        rr.query_report_evidence(
            "lifecycle", "v1",
            snapshot_loader=lambda _s: {"chart_rows": {"lifecycle": [{"secret": "內容"}]}})
        blob = str(rr.get_query_audit())
        self.assertIn("lifecycle", blob)
        self.assertNotIn("內容", blob, "回傳的資料列不得進稽核紀錄")

    def test_multiple_calls_accumulate_in_order(self):
        loader = lambda _s: {"chart_rows": {"lifecycle": [{"a": 1}]}}  # noqa: E731
        rr.query_report_evidence("lifecycle", "v1", snapshot_loader=loader)
        rr.list_report_catalog()
        rr.preview_report_rows("lifecycle", "v1", snapshot_loader=loader)
        # ⚠ preview 內部會先呼叫 query_report_evidence，所以它自己那筆排在後面
        # ——兩筆都留是刻意的：稽核要看得出 CLI 呼叫的是 preview 還是直接 query。
        self.assertEqual([e["tool"] for e in rr.get_query_audit()],
                         ["query_report_evidence", "list_report_catalog",
                          "query_report_evidence", "preview_report_rows"])

    def test_reset_clears(self):
        rr.list_report_catalog()
        self.assertTrue(rr.get_query_audit())
        rr.reset_query_audit()
        self.assertEqual(rr.get_query_audit(), [])


if __name__ == "__main__":
    unittest.main()

class AuditReachesRunnerTests(unittest.TestCase):
    """稽核要跨行程傳得回來。

    🔴 MCP server 是 CLI 的**子行程**，runner 在 worker 行程——記在記憶體裡
    runner 一筆也拿不到，等於沒有。以環境變數指定的 JSONL 落檔傳遞：
    runner 起 CLI 前指定路徑，server 子行程繼承環境變數後逐筆寫入。

    ⚠ 未設環境變數時只留記憶體（測試與離線除錯用），不預設寫檔——不該因為
    有人 import 這個模組就在檔案系統留下東西。
    """

    def setUp(self):
        rr.reset_query_audit()

    def test_writes_jsonl_when_path_configured(self):
        import json as _json
        import os
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "audit.jsonl"
            os.environ[rr.AUDIT_PATH_ENV] = str(target)
            try:
                rr.list_report_catalog()
                rr.query_report_evidence(
                    "lifecycle", "v1",
                    snapshot_loader=lambda _s: {"chart_rows": {"lifecycle": [{"a": 1}]}})
            finally:
                os.environ.pop(rr.AUDIT_PATH_ENV, None)
            lines = [_json.loads(x) for x in
                     target.read_text(encoding="utf-8").splitlines() if x.strip()]
            self.assertEqual([e["tool"] for e in lines],
                             ["list_report_catalog", "query_report_evidence"])

    def test_no_file_written_without_env(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            rr.list_report_catalog()
            self.assertEqual(list(Path(tmp).iterdir()), [],
                             "未設定路徑時不該在檔案系統留下東西")

    def test_unwritable_path_does_not_break_queries(self):
        """⚠ 稽核寫不進去不得讓取證失敗——它是觀測，不是查詢的前置條件。"""
        import os

        os.environ[rr.AUDIT_PATH_ENV] = "Z:/nonexistent-drive/audit.jsonl"
        try:
            catalog = rr.list_report_catalog()
        finally:
            os.environ.pop(rr.AUDIT_PATH_ENV, None)
        self.assertTrue(catalog, "查詢本身必須照常回傳")
