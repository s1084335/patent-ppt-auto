"""前端按鈕必須觸發 goal-driven 整條線（2026-08-10 使用者目標）。

使用者原話：「記得目標是我要能從前端我來按下去啟動，所以整條線要接通」。

## 查到的斷點

前端「產生 PPT」按鈕（`requestExportPpt`）送的是：

    task_type: 'ai:report_ppt'
    params: { based_on_version, approval_overrides, workspace_id }

**沒有派 `ai:report_plan`，也沒送 `selected_charts` 與 `north_star_goal`。**
於是使用者按下去得到的一律是**固定頁序**的 PPT——選圖、最大目標、四道規劃閘門、
narrative 濃縮全都不會發生。

而 `loadPptChartPicker()`（選圖清單）與 `collectPptPlanBrief()`（收集目標）都已經
寫好，卻**零個呼叫端**。這是本專案同型問題第五次：做了正確的東西，沒接到會被
觸發的地方。

## 接續為什麼要在 worker 端

`handlers._enqueue_chained_report_ppt` 的 docstring 記著實測教訓：narrative→PPT
的接續原本寫在前端輪詢，使用者關分頁／電腦睡著就斷在解讀完成，PPT 任務從來沒被
建立，而畫面顯示解讀 succeeded——「看起來成功的靜默停止」。

plan→ppt 沿用同一條規則：前端只負責**送出一次**並顯示進度，鏈由 worker 端接。
"""
from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"
AI_BRIDGE = PROJECT_ROOT / "backend" / "app" / "worker" / "ai_bridge.py"


def _request_export_ppt() -> str:
    """取 requestExportPpt 的函式本體。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    start = html.index("async function requestExportPpt")
    return html[start:html.index("\nfunction ", start + 1)]


class FrontendWiringTests(unittest.TestCase):
    """按鈕要送得出規劃所需的一切。"""

    def test_picker_and_brief_are_actually_called(self):
        """選圖清單與目標收集不得只有定義沒有呼叫。

        ⚠ 這正是本次斷點：兩個函式都寫好了，但沒有任何地方呼叫它們。
        """
        html = INDEX_HTML.read_text(encoding="utf-8")
        for name in ("loadPptChartPicker", "collectPptPlanBrief"):
            # 定義 1 次 + 至少 1 次呼叫
            self.assertGreaterEqual(
                html.count(name), 2,
                f"{name} 只有定義沒有呼叫端——寫了沒接上等於沒做",
            )

    def test_picker_is_called_when_content_loads_not_only_in_edit_mode(self):
        """選圖清單要在**內容載入後**就填，不能只在編輯模式才填。

        🔴 2026-08-10 實機抓到：一度把 `loadPptChartPicker()` 掛在
        `renderExportPreview()`，而那支只有**編輯模式**才呼叫——一般預覽走
        `loadExportPptFiles`，於是使用者點進匯出報告頁時選圖清單永遠是空的。

        ⚠ 這條是上一支測試補不到的：它只驗得到「有呼叫端」，驗不到「在正確時機
        呼叫」。開瀏覽器實際點進去才發現，勾選框數是 0。
        """
        html = INDEX_HTML.read_text(encoding="utf-8")
        start = html.index("async function loadExportPreview")
        body = html[start:html.index("\nasync function ensurePptxRenderer", start)]
        self.assertIn(
            "loadPptChartPicker()", body,
            "選圖清單要在 loadExportPreview 取得內容後就填——"
            "掛在只有編輯模式會走的路徑上，等於一般使用者永遠看不到",
        )

    def test_export_button_dispatches_report_plan(self):
        """按鈕要派 `ai:report_plan`（goal-driven 的入口），不是直接跳到組版。"""
        body = _request_export_ppt()
        self.assertIn(
            "ai:report_plan", body,
            "產生 PPT 必須先走目標規劃；直接派 ai:report_ppt 會退回固定頁序",
        )

    def test_export_button_sends_selected_charts_and_goal(self):
        """選圖與最大目標是規劃的必要輸入，前端不送就等於沒有 goal-driven。"""
        body = _request_export_ppt()
        for field in ("selected_charts", "north_star_goal"):
            self.assertIn(field, body, f"送出的 params 缺 {field}")

    def test_chain_flag_is_sent_not_frontend_polling(self):
        """接續由 worker 端負責：前端送 `then_export_ppt`，不自己輪詢後再送第二個任務。

        ⚠ 依 handlers._enqueue_chained_report_ppt 的實測教訓——前端輪詢會在
        關分頁時斷鏈，且斷得「看起來成功」。
        """
        body = _request_export_ppt()
        self.assertIn("then_export_ppt", body)


class WorkerChainTests(unittest.TestCase):
    """plan 成功後由 worker 接續派 PPT。"""

    def test_plan_job_chains_to_ppt(self):
        """`_run_ai_report_plan_job` 要在成功後依旗標接續派 ai:report_ppt。"""
        source = AI_BRIDGE.read_text(encoding="utf-8")
        start = source.index("def _run_ai_report_plan_job")
        body = source[start:source.index("\n_AI_JOB_RUNNERS", start)]
        self.assertIn("then_export_ppt", body,
                      "規劃成功後要能接續組版，否則使用者得手動按第二次")
        self.assertIn("ai:report_ppt", body)


if __name__ == "__main__":
    unittest.main()
