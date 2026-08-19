"""目視迴圈的權限與診斷（2026-08-19，實機 job #426 事後）。

## 病徵

`#426` 跑了 3763 秒後失敗，訊息是「目視第 2 輪回報問題但未修改 content.json
（停滯）」。看起來像模型不聽話，實際是**工具白名單漏了 `Edit`**：

    finding 3：阻塞：本輪對 content.json 的寫入被權限層擋下
              （Edit 兩次皆回 requested permissions … haven't granted it yet）

事件鏈：第 1 輪 CLI 用 `Write` 整檔重寫成功 → 第 2 輪要做的是兩個字元的定點
替換（全形斜線 → 頓號），自然改用 `Edit` → 被擋 → content.json 的 sha256 沒變
→ runner 判定停滯 → 硬失敗。63 分鐘與一次完整重跑就這樣沒了。

## 三道防線

1. **白名單要有 `Edit`**：`Write` 是整檔重寫、`Edit` 是定點替換。只給 Write
   反而風險更高——要改兩個字元卻只能整檔重寫，任何一次重寫都可能連帶改掉別的。
   ⚠ 定點替換是**更小**的權限，不是更大的。
2. **停滯與被擋要分得開**：兩者都是「回報問題但沒改檔」，但一個是模型判斷不需
   要改、另一個是系統設定缺陷。混為一談會讓人去調提示詞而不是去補白名單。
3. **錯誤訊息不得吃掉證據**：原本 `"；".join(last_findings)[:400]`，而關鍵的
   finding 3 剛好被截在「阻塞：本輪對 conte」。完整內容在 `visual_verdict.json`
   ——訊息必須指向它。
"""
from __future__ import annotations

import unittest

from backend.app.worker import ai_report_deck_runner as deck
from backend.app.worker.cli_gateway import RESEARCH_TOOLS


class EditPermissionTests(unittest.TestCase):
    """防線 1：目視迴圈要改 content.json，就必須有 Edit。"""

    def test_research_tools_grants_edit(self):
        self.assertIn(
            "Edit", RESEARCH_TOOLS,
            "白名單沒有 Edit——CLI 想做定點替換會被擋，而 runner 只看檔案有沒有變，"
            "於是誤判成模型停滯（實機 job #426）")

    def test_deck_review_command_grants_edit(self):
        """⚠ 驗**實際組出的指令**，不只驗常數：常數對了但 deck 線沒用到它，
        白名單一樣是空的（`ai_candidate_explanation_runner` 就踩過這種漂移）。"""
        argv = deck.build_deck_cli_command("claude", "prompt")
        self.assertIn("--allowedTools", argv)
        tools = argv[argv.index("--allowedTools") + 1:]
        self.assertIn("Edit", tools, f"deck 目視指令沒給 Edit：{tools}")

    def test_write_still_granted(self):
        """反面：Edit 是**增加**一種手段，不得把 Write 換掉——
        整頁重寫（拆頁、轉純文字頁）仍需要 Write。"""
        self.assertIn("Write", RESEARCH_TOOLS)


class PermissionBlockDetectionTests(unittest.TestCase):
    """防線 2：從 findings 認出「想改但被擋」。"""

    BLOCKED = (
        "阻塞：本輪對 content.json 的寫入被權限層擋下"
        "（Edit 兩次皆回 requested permissions … haven't granted it yet）",
    )
    NORMAL = (
        "發現 p11 行首是全形斜線「／」，屬 pitfalls #41 的行首標點瑕疵。",
        "已逐頁看過 18 頁（無抽樣）：文字未溢出卡片、無重疊。",
    )

    def test_detects_permission_block(self):
        hit = deck.permission_blocked(self.BLOCKED)
        self.assertEqual(len(hit), 1, f"沒認出被擋的 finding：{hit}")

    def test_normal_findings_are_not_flagged(self):
        """⚠ 反面必驗：把正常回報誤判成權限問題，會讓真正的版面缺陷被當成
        環境問題放過去——比漏判更糟。"""
        self.assertEqual(deck.permission_blocked(self.NORMAL), [])

    def test_english_only_wording_also_detected(self):
        """CLI 的原文是英文，中文那句是它自己加的摘要——不能只認中文。"""
        findings = ["Edit tool failed: requested permissions for content.json, "
                    "but you haven't granted it yet"]
        self.assertEqual(len(deck.permission_blocked(findings)), 1)

    def test_empty_is_safe(self):
        self.assertEqual(deck.permission_blocked([]), [])
        self.assertEqual(deck.permission_blocked(None), [])


class StallMessageTests(unittest.TestCase):
    """防線 3：兩種失敗訊息都要說得出下一步，並指向完整證據。"""

    def test_blocked_message_names_the_cause_and_evidence(self):
        msg = deck.visual_stall_message(
            2, PermissionBlockDetectionTests.BLOCKED, "D:/x/visual_verdict.json")
        self.assertIn("權限", msg)
        self.assertNotIn("停滯", msg, "被權限擋下不是停滯，混用會把人導向錯的修法")
        self.assertIn("visual_verdict.json", msg, "訊息沒指向完整證據")

    def test_stall_message_still_says_stalled(self):
        msg = deck.visual_stall_message(
            2, PermissionBlockDetectionTests.NORMAL, "D:/x/visual_verdict.json")
        self.assertIn("停滯", msg)
        self.assertIn("visual_verdict.json", msg)

    def test_findings_are_not_silently_swallowed(self):
        """⚠ 原本 [:400] 把 finding 3 截掉，我第一次查就被誤導。
        截斷可以，但必須留下取得完整內容的路徑（上面兩條已驗），
        且第一條 finding 要看得到。"""
        msg = deck.visual_stall_message(
            2, PermissionBlockDetectionTests.NORMAL, "D:/x/visual_verdict.json")
        self.assertIn("全形斜線", msg)


if __name__ == "__main__":
    unittest.main()
