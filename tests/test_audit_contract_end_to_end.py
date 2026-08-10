"""稽核的生產端與消費端必須接得上——用**真實產出**驗，不得自造字典。

## 為什麼需要這一支

2026-08-10 犯過一次：`validate_research_effort` 以 `status` 判斷查詢失敗，但稽核
（`_audit` 唯一定義處）從來沒寫過那個欄位——`entry.get("status", "ok")` 永遠回
"ok"，「查了但全部失敗」那個分支一次都不會觸發。

⚠ **而當時的測試是綠的**，因為測試餵的是自己編的 `{"status": "error"}` 字典。
測試驗的是「我腦中以為的格式」，不是「系統真正產出的格式」——這種測試不只沒有
守住，還提供了虛假的安全感。

## 本檔的做法

不自造任何 entry：**實際呼叫產生稽核的路徑**，把真實產出餵給消費端。
任一端改欄位、改語意，這裡就會紅。

⚠ 這是「同一份知識兩個落點」的通用解法之一：無法 import 共用時（這裡是兩個模組
各自持有格式認知），就讓測試把兩端**釘在一起**。
"""
from __future__ import annotations

import unittest

from backend.app.mcp_server import report_research as rrs
from backend.app.reports.planning_contracts import validate_research_effort


class AuditContractEndToEndTests(unittest.TestCase):
    """生產端產出什麼，消費端就要看得懂什麼。"""

    def setUp(self):
        rrs.reset_query_audit()

    def test_successful_real_query_is_recognised_as_success(self):
        """真實成功查詢 → 消費端必須判定為「有查證」。

        ⚠ 用 list_report_catalog（不需 DB、不需 snapshot）產生真實稽核。
        """
        rrs.list_report_catalog()
        audit = rrs.get_query_audit()
        self.assertTrue(audit, "list_report_catalog 應留下稽核")
        self.assertEqual(
            validate_research_effort(audit), [],
            f"消費端看不懂真實稽核格式：{audit[0]}",
        )

    def test_real_failure_is_recognised_as_failure(self):
        """真實失敗查詢 → 消費端必須判定為「沒有有效查證」。

        🔴 這就是 2026-08-10 那個缺陷會被抓到的地方：若消費端看錯欄位，
        失敗會被誤判成成功，本測直接紅。
        """
        with self.assertRaises(rrs.ReportResearchError):
            rrs.query_report_evidence(report_key="不存在的報表", snapshot_id="v1")
        audit = rrs.get_query_audit()
        self.assertTrue(audit, "失敗的查詢也要留下稽核")
        errors = validate_research_effort(audit)
        self.assertTrue(
            errors,
            f"全部查詢都失敗卻被判定為有查證——消費端與稽核格式對不上：{audit}",
        )

    def test_failure_entry_carries_the_field_consumer_reads(self):
        """把「消費端讀哪個欄位」釘死在真實產出上。

        ⚠ 不斷言欄位**叫什麼名字**，而是斷言「失敗紀錄裡有那個讓消費端判定失敗的
        資訊」——名字可以改，但兩端必須一起改。
        """
        with self.assertRaises(rrs.ReportResearchError):
            rrs.query_report_evidence(report_key="不存在的報表", snapshot_id="v1")
        entry = rrs.get_query_audit()[-1]
        self.assertTrue(
            any(entry.get(key) for key in ("error", "status", "failed")),
            f"失敗稽核沒有任何可辨識的失敗標記：{entry}",
        )

    def test_success_entry_has_no_stale_none_fields(self):
        """精簡契約：值為 None 的欄位不得寫進紀錄（2026-08-10 使用者定案）。"""
        rrs.list_report_catalog()
        entry = rrs.get_query_audit()[-1]
        self.assertFalse(
            [k for k, v in entry.items() if v is None],
            f"稽核不得留 None 值欄位：{entry}",
        )


if __name__ == "__main__":
    unittest.main()
