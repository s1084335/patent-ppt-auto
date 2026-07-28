"""refresh_derived 的 aliases／all 整合為一條（2026-07-28 使用者定案）。

實機發現：兩張家族表（report_family_quality／report_family_country）從專案開始
就是 **0 列**，國家佈局與家族完整性兩張報表因此永遠產不出來。

根因不是計算壞掉——我實跑 `refresh_report_family_country()` 立刻產出 52 家族、
32 列國家佈局，機制完全正常。是**從未被觸發**：

    匯入完成自動排（handlers._enqueue_refresh_derived）   scope='aliases'
    確認公司名後自動排（api/company_aliases.py:142）        scope='aliases'
    MCP refresh_derived_data(scope)                        唯一能傳 'all' 的地方

而 `handle_refresh_derived` 只在 scope == 'all' 時才刷家族。全系統沒有任何自動
路徑會送 'all' → 那兩張表永遠不更新。

實測成本（60 筆）：aliases 0.77 秒、家族那段 0.58 秒、合計 1.35 秒。
家族計算比公司名收斂**更輕**（後者有三個 LATERAL join 逐列查對照表，前者只是
家族層級 group by）。這不是效能取捨，是功能沒接。

使用者定案：**兩者的產出都是需求，整合起來**——refresh_derived 一律刷全部，
不再用 scope 分岔。
"""
from __future__ import annotations

import inspect
import unittest
from unittest import mock


class RefreshAlwaysCoversFamilyTests(unittest.TestCase):
    """不帶 scope（或帶任何值）都要刷到家族表。"""

    def _run(self, payload):
        from backend.app.worker import handlers

        ctx = mock.Mock()
        with mock.patch.object(handlers, "_refresh_all_derived",
                               return_value={"ok": True}) as runner:
            handlers.handle_refresh_derived(payload, ctx)
        return runner

    def test_default_payload_refreshes_family(self):
        """匯入後自動排的那筆（原本 scope='aliases'）也要刷家族。"""
        self._run({}).assert_called_once()

    def test_aliases_scope_still_refreshes_family(self):
        """舊 payload 相容：scope='aliases' 不得再只刷一半。"""
        self._run({"scope": "aliases"}).assert_called_once()


class SingleRefreshPathTests(unittest.TestCase):
    """整合後只剩一條刷新路徑，不再有 scope 分岔。"""

    def test_no_scope_branch_in_handler(self):
        from backend.app.worker import handlers

        src = inspect.getsource(handlers.handle_refresh_derived)
        self.assertNotIn(
            'scope == "all"', src,
            "handler 仍以 scope 分岔——家族表會繼續有機會被跳過")

    def test_helper_runs_both_refreshes(self):
        """單一入口要同時跑 report_patent_base 與 family_country。

        ⚠ 鎖行為不鎖字面：實作把兩段各抽成 _refresh_patent_base_only／_refresh_family_only
        （便於失敗隔離與測試注入），字串 refresh_report_patent_base 因此不在
        _refresh_all_derived 的原始碼裡。改為實際呼叫並確認兩支都被跑到。
        """
        from backend.app.worker import handlers

        with mock.patch.object(handlers, "_refresh_patent_base_only",
                               return_value={"rows": 60}) as base, \
             mock.patch.object(handlers, "_refresh_family_only",
                               return_value={"family_count": 52}) as fam:
            result = handlers._refresh_all_derived()
        base.assert_called_once()
        fam.assert_called_once()
        self.assertIn("report_patent_base", result)
        self.assertIn("report_family_country", result)

    def test_family_failure_does_not_lose_base_result(self):
        """家族那段失敗時，公司名收斂的結果仍要回得去（失敗隔離）。

        兩者是獨立產出；家族計算掛掉不該讓已完成的 report_patent_base 刷新一起報廢。
        """
        from backend.app.worker import handlers

        with mock.patch.object(handlers, "_refresh_patent_base_only",
                               return_value={"rows": 60}), \
             mock.patch.object(handlers, "_refresh_family_only",
                               side_effect=RuntimeError("boom")):
            result = handlers._refresh_all_derived()
        self.assertIn("report_patent_base", result)
        self.assertIn("family_error", result,
                      "家族失敗要明確回報，不得靜默吞掉")


class CallersNoLongerPassScopeTests(unittest.TestCase):
    """呼叫端不必再指定 scope。"""

    def test_import_enqueue_has_no_scope(self):
        from backend.app.worker import handlers

        src = inspect.getsource(handlers._enqueue_refresh_derived)
        self.assertNotIn('"scope": "aliases"', src,
                         "匯入後仍送 scope='aliases'——語意已整合，不該再傳")


if __name__ == "__main__":
    unittest.main()
