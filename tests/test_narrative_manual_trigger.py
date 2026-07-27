"""AI 解讀的手動觸發（2026-07-28）。

實機（run 21）：報表 report_trial_20260727_173209 產完自動排的 ai:narrative 失敗，
解讀區永遠顯示「AI 解讀尚未產生。可於右欄『AI 助手』送出解讀需求。」——但那句是
**寫死的文案，不是按鈕**，右欄 AI 助手也沒有送 ai:narrative 的路徑。前端全檔零處
發 ai:narrative。

後果：`report_generate` 完自動 enqueue 是**唯一**啟動方式（handlers.py
_enqueue_report_narrative），而它採失敗隔離只記 log。一旦那筆失敗，使用者除了
「重產整份報表」之外沒有任何重試手段——報表本身還好好的，卻得整包重跑。

修法：報表頁解讀區給一個「產生 AI 解讀」鈕，打既有 `POST /api/v1/ai-tasks`
（task_type=ai:narrative、based_on_version 綁該版本）。**後端零改動**——該端點
早就支援泛型 task_type，缺的只是前端入口。

⚠ 本測試鎖「有觸發路徑」而非鎖 DOM 細節：重點是那句文案不得再是死字串，
必須真的能送出。
"""
from __future__ import annotations

import re
import unittest

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


class NarrativeManualTriggerTests(unittest.TestCase):
    """前端必須有送出 ai:narrative 的實際路徑。"""

    @classmethod
    def setUpClass(cls):
        cls.html = client.get("/").text

    def test_frontend_can_send_narrative_task(self):
        """前端存在發 ai:narrative 的程式碼——不能只有文案叫使用者去別處送。

        ⚠ 不可只斷言 'ai:narrative' 出現在整份 HTML：該字串早已存在於註解與說明文字，
        會假性通過（本測試初版即如此，Red 時這條是唯一「通過」的）。必須落在觸發
        函式本體內才算真的有送出路徑。
        """
        body = re.search(r"async function runNarrative\s*\([^)]*\)\s*\{(.*?)\n\}",
                         self.html, re.S)
        self.assertIsNotNone(
            body,
            "前端沒有任何發 ai:narrative 的路徑：解讀只能靠報表產完自動排，"
            "那筆失敗後無從重試")
        self.assertIn("ai:narrative", body.group(1))

    def test_narrative_trigger_button_exists(self):
        """解讀區有可按的觸發鈕（掛點 id 供 JS 控 disabled／文字）。"""
        self.assertIn("btn-run-narrative", self.html)

    def test_trigger_function_defined(self):
        """觸發函式已定義（不是只有鈕、onclick 指向不存在的函式）。"""
        self.assertRegex(
            self.html, r"function\s+runNarrative\s*\(",
            "有 btn-run-narrative 卻沒有 runNarrative 函式——按了會 ReferenceError")

    def test_posts_to_ai_tasks_endpoint(self):
        """走既有 /ai-tasks 端點，不另造第二條建 job 的路。"""
        body = re.search(r"async function runNarrative\s*\([^)]*\)\s*\{(.*?)\n\}",
                         self.html, re.S)
        self.assertIsNotNone(body, "找不到 runNarrative 函式本體")
        self.assertIn("/ai-tasks", body.group(1))

    def test_binds_based_on_version(self):
        """必須綁定當前檢視的報表版本——不綁版本會解讀到別份報表。"""
        body = re.search(r"async function runNarrative\s*\([^)]*\)\s*\{(.*?)\n\}",
                         self.html, re.S)
        self.assertIn("based_on_version", body.group(1))

    def test_stale_copy_removed(self):
        """舊死文案「可於右欄『AI 助手』送出解讀需求」必須拿掉——右欄沒有那功能。"""
        self.assertNotIn("可於右欄「AI 助手」送出解讀需求", self.html)


if __name__ == "__main__":
    unittest.main()
