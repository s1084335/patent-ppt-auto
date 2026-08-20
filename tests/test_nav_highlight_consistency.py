"""側欄高亮與 state.nav 必須同源（2026-08-17 實機發現：進首頁左側是白的）。

## 症狀

一進首頁，內容區已是「瀏覽專利」，**左側側欄卻沒有任何高亮**——使用者不知道
自己在哪一區。要點過一次側欄才會亮。

## 根因

高亮只在 `navTo()` 裡設定（`classList.toggle('active', ...)`），
而啟動路徑 `loadWorkspaces()` **直接改 `state.nav = 'browse'` 再 `renderMain()`**
——繞過 `navTo()`，內容畫了、class 沒設。

⚠ 與同日修的 workspace 顯示不一致（`isGlobalWs` 初值）**完全同型**：
同一份狀態有兩個設定路徑，其中一條漏了同步。修法也一致——收斂單一入口。

## 契約

改變 `state.nav` 的地方都要走 `setNav()`（它同時設 state 與 class），
不得直接賦值。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

INDEX = (Path(__file__).resolve().parents[1]
         / "backend" / "app" / "static" / "index.html")


class NavHighlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = INDEX.read_text(encoding="utf-8")

    def test_set_nav_helper_exists(self):
        """單一入口：同時設 state.nav 與側欄 class。"""
        self.assertIn("function setNav", self.src,
                      "缺少 setNav——state.nav 與側欄 class 會在多處各自設定而分岔")

    def test_no_direct_nav_assignment_outside_helper(self):
        """🔴 除了 setNav 自己，不得有 `state.nav = ` 直接賦值。

        那正是本 bug：loadWorkspaces 直接賦值，側欄高亮沒跟上。
        """
        # 取出 setNav 函式本體，其餘部分不得出現直接賦值。
        # ⚠ 用下一個 `function ` 當結尾，不能用 `\n}`——那會抓到內部
        #   forEach 的收尾，把函式體切斷、反而把合法賦值算成違規。
        i = self.src.index("function setNav")
        end = self.src.index("function navTo", i)
        rest = self.src[:i] + self.src[end:]
        # ⚠ 必須排除比較運算：`state.nav === 'browse'` 不是賦值。
        #   負向前瞻擋掉後面還跟著 `=` 的情況（== 與 ===）。
        hits = [m.start() for m in re.finditer(r"state\.nav\s*=(?!=)", rest)]
        lines = [rest[:h].count("\n") + 1 for h in hits]
        self.assertEqual(
            hits, [],
            f"這些位置直接改 state.nav（行號約 {lines}）——請改走 setNav，"
            "否則側欄高亮會與內容不一致")

    def test_initial_highlight_applied_on_boot(self):
        """啟動序列要讓側欄一開始就亮（不是等使用者點）。"""
        boot = self.src[self.src.index("══ 初始化 ══"):]
        self.assertTrue(
            "setNav(" in boot or "applyNavHighlight(" in boot,
            "初始化沒有套用側欄高亮——進首頁左側會是白的")


if __name__ == "__main__":
    unittest.main()
