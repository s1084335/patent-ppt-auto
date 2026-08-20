"""workspace 的「選單顯示」與「資料範圍」必須一致（2026-08-17 實機發現）。

## 症狀

一進首頁，選單顯示某個 workspace，列表資料卻是全庫的——使用者會拿錯範圍的
資料下判斷，而且**沒有任何錯誤訊息**。

## 根因

`state.isGlobalWs` 初值是 `false`，但 `state.workspaceId` 初值是 `null`——
兩者描述同一件事卻可以互相矛盾。`isGlobalSelected()` 只讀 `isGlobalWs`，
於是在 `loadWorkspaces()` 完成前（或它失敗走 catch 時）會判定「不是全庫」，
但 `workspaceId` 是 null，取資料的 URL 變成 `/workspaces/null/patents`。

⚠ 同型前科：`onWorkspaceChange` 的註記記著 R-2（2026-08-05）「原本每次載入
硬性重設回全庫——使用者以為還在滑雪機、按重新產製實際送出全庫（#193 實案）」。
那次修的是**持久化**，這次漏的是**顯示與資料的一致性**——同一個坑的另一面。

## 契約

`isGlobalWs` 與 `workspaceId` 是同一份知識的兩個欄位，**必須同源推導**：
- 初值即為「全庫」（`isGlobalWs: true`），與選單第一項恆為全庫一致
- 任何改變範圍的路徑（初始化／恢復／切換）都要同時設定兩者
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

INDEX = (Path(__file__).resolve().parents[1]
         / "backend" / "app" / "static" / "index.html")


class WorkspaceStateConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = INDEX.read_text(encoding="utf-8")

    def test_initial_state_is_global(self):
        """🔴 初值必須是全庫——選單第一項恆為全庫，狀態要跟它一致。"""
        block = self.src[self.src.index("const state = {"):]
        block = block[:block.index("\n};")]
        m = re.search(r"isGlobalWs:\s*(true|false)", block)
        self.assertIsNotNone(m, "state 沒有 isGlobalWs")
        self.assertEqual(
            m.group(1), "true",
            "isGlobalWs 初值不是 true：loadWorkspaces 完成前（或失敗時）"
            "會判定「不是全庫」而去打 /workspaces/null/patents，"
            "但選單顯示的是全庫——顯示與資料不一致")

    def test_catch_branch_falls_back_to_global(self):
        """workspace 清單載不到時要退回全庫（選單也只剩全庫項）。"""
        i = self.src.index("async function loadWorkspaces")
        body = self.src[i:self.src.index("function selectGlobalWorkspace")]
        catch = body[body.index("} catch"):]
        self.assertIn("selectGlobalWorkspace()", catch,
                      "catch 分支沒把狀態設成全庫，但選單已只剩全庫項——不一致")

    def test_single_source_helper_exists(self):
        """設定範圍要走同一個 helper，不各自賦值兩個欄位。"""
        self.assertIn("function selectWorkspaceById", self.src,
                      "缺少 selectWorkspaceById：isGlobalWs 與 workspaceId "
                      "在多處各自賦值，遲早再分岔")


if __name__ == "__main__":
    unittest.main()
