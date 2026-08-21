"""報表產製必須套用 workspace 母體（2026-08-17 實機發現的資料正確性 bug）。

## 症狀

選「滑雪機（55 件）」產報表，趨勢表卻出現 2022 年 61 件、2023 年 60 件
——**超過該 workspace 的總件數**。標題寫滑雪機、數字是全庫 281 件。

## 根因

`run_chart_trial` 的母體入口是 `patent_ids`（其 docstring 明寫「worker 的
report_generate payload 走這條」），而 `workspace_id` 只用於：分群資料、
封面 workspace 名稱。前端 `submitReports()` **只送 workspace_id 不送
patent_ids**，於是 `payload.get("patent_ids")` 恆為 None → 引擎跑全庫。

⚠ 為什麼一直沒被發現：**標題是對的、只有數字錯**——比整份壞掉更難察覺。
且過去多在全庫下驗；滑雪機那份 `_v17` 是舊 PPT 線產物，走不同路徑。

## 修法

後端從 `workspace_id` 解出成員（`app_layer.workspaces.patent_ids_json`），
不要求前端傳長串 id——前端傳 id 陣列脆弱（幾百個 id 進 payload），
且成員的唯一事實來源本來就在 DB。

⚠ 全庫 workspace（`is_global`）與未指定 workspace 都走全庫（`patent_ids=None`），
那是既有正確行為，不得因本修正而改變。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.worker import handlers


class ResolveWorkspacePatentIdsTests(unittest.TestCase):
    """`_resolve_workspace_patent_ids`：workspace → 成員 id 清單。"""

    def test_none_workspace_returns_none(self):
        """未指定 workspace＝全庫，回 None（讓引擎走全庫路徑）。"""
        self.assertIsNone(handlers._resolve_workspace_patent_ids(None))

    def test_global_workspace_returns_none(self):
        """🔴 全庫 workspace 也要回 None——它的成員就是全部，
        傳一份幾百個 id 的清單只會拖慢查詢且無意義。"""
        with patch.object(handlers, "_fetch_workspace_row",
                          return_value={"is_global": True, "patent_ids_json": [1, 2, 3]}):
            self.assertIsNone(handlers._resolve_workspace_patent_ids(4))

    def test_normal_workspace_returns_members(self):
        with patch.object(handlers, "_fetch_workspace_row",
                          return_value={"is_global": False,
                                        "patent_ids_json": [93, 94, 96]}):
            self.assertEqual(handlers._resolve_workspace_patent_ids(3), [93, 94, 96])

    def test_empty_membership_fails_loud(self):
        """🔴 成員為空＝設定有問題，不得靜默退回全庫。

        退回全庫的話報表會「看起來正常但範圍全錯」——正是本 bug 的形態。
        """
        with patch.object(handlers, "_fetch_workspace_row",
                          return_value={"is_global": False, "patent_ids_json": []}):
            with self.assertRaises(ValueError):
                handlers._resolve_workspace_patent_ids(3)

    def test_missing_workspace_fails_loud(self):
        """workspace 不存在＝payload 有問題，同樣不得靜默跑全庫。"""
        with patch.object(handlers, "_fetch_workspace_row", return_value=None):
            with self.assertRaises(ValueError):
                handlers._resolve_workspace_patent_ids(999)


class ReportGenerateScopeTests(unittest.TestCase):
    """handler 要把成員接到 `run_chart_trial` 的 `patent_ids`。"""

    def _run(self, payload, *, members):
        captured = {}

        def fake_trial(**kwargs):
            captured.update(kwargs)
            return {"output_dir": "/tmp/x", "reports": {}}

        class Ctx:
            def heartbeat(self, *a, **k):
                pass

            def keepalive(self, *a, **k):
                class _N:
                    def __enter__(self_inner):
                        return None

                    def __exit__(self_inner, *exc):
                        return False
                return _N()

            def check_cancelled(self):
                pass

        with patch.object(handlers, "run_chart_trial", fake_trial), \
             patch.object(handlers, "_resolve_workspace_patent_ids",
                          return_value=members), \
             patch.object(handlers, "_resolve_report_cluster_data", return_value=None), \
             patch.object(handlers, "_resolve_workspace_name", return_value="滑雪機"), \
             patch.object(handlers.report_artifact_store, "upload_run_dir",
                          return_value=1):
            handlers.handle_report_generate(payload, Ctx())
        return captured

    def test_workspace_members_become_patent_ids(self):
        """🔴 本 bug 的核心斷言：選了 workspace，引擎就要收到它的成員。"""
        got = self._run({"workspace_id": 3, "report_names": ["application_trend"]},
                        members=[93, 94, 96])
        self.assertEqual(got["patent_ids"], [93, 94, 96],
                         "workspace 母體沒接到引擎——報表會用全庫資料卻掛著該 workspace 的名字")

    def test_explicit_patent_ids_win(self):
        """payload 明確給 patent_ids 時以它為準（既有契約，不得被 workspace 蓋掉）。"""
        got = self._run({"workspace_id": 3, "patent_ids": [7],
                         "report_names": ["application_trend"]},
                        members=[93, 94, 96])
        self.assertEqual(got["patent_ids"], [7])

    def test_global_workspace_stays_full_scope(self):
        got = self._run({"workspace_id": 4, "report_names": ["application_trend"]},
                        members=None)
        self.assertIsNone(got["patent_ids"])


if __name__ == "__main__":
    unittest.main()
