"""單張報表獨立重產解讀＋帶使用者 prompt（2026-07-29 使用者定案）。

## 使用者需求

- 「(a) 只拿掉輸入框，解讀照樣自動產」——AI 助手欄只留任務進度
- 「報表要能各自獨立重產解釋」——不是整份重跑
- 「重產解釋要能順帶使用者輸入的 prompt 去重解讀」
- 「原本圖表數據不能少」——AI 只碰解讀文字

## 現況缺口

1. `runNarrative()` 送 `{task_type, based_on_version}`，**沒有 report_key**
   → 只能整份重跑；改一張圖的解讀要等全部重產。
2. AI 助手欄的輸入框（`#ai-request`）送的是另一條路徑 `sendAiRequest()`，
   與報表區的重產鈕各走各的——同一個「叫 AI 重寫解讀」有兩個入口。
3. 後端 `build_prompt(instruction=...)` **已支援**使用者附加需求（L174-196），
   但前端重產鈕沒傳；prompt 第 3 點寫死「對每張卡片的每個變體」，
   無法只做一張。

## 定案

- 輸入框移出 AI 助手欄，**移到各報表旁**（重產時就地輸入）
- `ai:narrative` job 接受選填 `report_keys`：有給就只重產那幾張，不給＝整份
- ⚠ 只重產時**必須保留其他報表的既有解讀**，不得整份覆寫成只剩一張
- 輸出契約不變：只准寫 narratives.json、v2 兩層結構、不碰圖表數據
"""
from __future__ import annotations

import inspect
import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"


class NarrativePromptScopeTests(unittest.TestCase):
    """後端：prompt 要能限定只重產指定報表。"""

    @classmethod
    def setUpClass(cls):
        from backend.app.worker import ai_narrative_runner

        cls.mod = ai_narrative_runner
        cls.src = inspect.getsource(ai_narrative_runner)

    def test_build_prompt_accepts_report_keys(self):
        """`build_prompt` 要能限定範圍。"""
        sig = inspect.signature(self.mod.build_prompt)
        self.assertIn("report_keys", sig.parameters,
                      "build_prompt 無法限定重產範圍，只能整份重跑")

    def test_scoped_prompt_names_the_reports(self):
        """限定範圍時，prompt 要明確列出是哪幾張。"""
        prompt = self.mod.build_prompt(
            Path("/tmp/x"), "v1", report_keys=["application_trend"])
        self.assertIn("application_trend", prompt,
                      "限定範圍時 prompt 未指名報表，AI 不知道要做哪張")

    def test_scoped_prompt_preserves_others(self):
        """🔴 只重產一張時，必須明確要求**保留其他報表的既有解讀**。

        否則 AI 會寫出只含一張報表的 narratives.json，其餘解讀全部消失——
        靜默資料損失（檔案有效、內容卻少了一大半）。
        """
        prompt = self.mod.build_prompt(
            Path("/tmp/x"), "v1", report_keys=["application_trend"])
        self.assertRegex(
            prompt, r"保留|不得刪除|其餘.*維持",
            "未要求保留其他報表解讀，重產一張會洗掉全部")

    def test_full_prompt_unchanged_without_scope(self):
        """不給 report_keys＝維持原本的整份重跑行為。"""
        prompt = self.mod.build_prompt(Path("/tmp/x"), "v1")
        self.assertIn("每張卡片", prompt, "未限定範圍時應維持整份重跑語意")

    def test_instruction_still_supported(self):
        """使用者附加需求（prompt）維持可用，且與範圍限定可併用。"""
        prompt = self.mod.build_prompt(
            Path("/tmp/x"), "v1",
            report_keys=["application_trend"], instruction="用白話說")
        self.assertIn("用白話說", prompt)
        self.assertIn("application_trend", prompt)

    def test_output_contract_unchanged(self):
        """⚠ 圖表數據不得被動到——契約仍限定只寫 narratives.json。"""
        prompt = self.mod.build_prompt(
            Path("/tmp/x"), "v1", report_keys=["application_trend"])
        self.assertIn("narratives.json", prompt)
        self.assertRegex(prompt, r"只准寫|不得改動其他檔案",
                         "輸出契約鬆掉，AI 可能動到圖表數據")


class NarrativeRerunUiTests(unittest.TestCase):
    """前端：輸入框移到報表旁，AI 助手欄只留任務進度。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_ai_panel_request_box_removed(self):
        """AI 助手欄不再有「對報表解讀提出需求」輸入框（使用者：只留任務進度）。"""
        self.assertNotIn("對報表解讀提出需求", self.html,
                         "AI 助手欄仍有解讀需求輸入框")

    def test_send_ai_request_removed(self):
        """連帶移除 sendAiRequest——它與報表區的重產鈕是同一件事的第二個入口。"""
        code = "\n".join(
            line for line in self.html.split("\n")
            if not line.strip().startswith("//"))
        self.assertNotIn("function sendAiRequest", code,
                         "sendAiRequest 未移除，同一功能仍有兩個入口")

    def test_rerun_passes_report_key(self):
        """重產鈕要帶 report_key，才做得到「各自獨立重產」。"""
        body = self._fn("runNarrative")
        self.assertIn("report_keys", body,
                      "重產未帶 report_keys，仍是整份重跑")

    def test_rerun_passes_instruction(self):
        """重產要能帶使用者當下輸入的 prompt。"""
        body = self._fn("runNarrative")
        self.assertRegex(body, r"instruction",
                         "重產未帶使用者輸入的 prompt")

    def _fn(self, name: str) -> str:
        match = re.search(
            r"async function " + name + r"\(.*?\n\}", self.html, re.S)
        self.assertIsNotNone(match, f"找不到 {name}")
        return match.group(0)


if __name__ == "__main__":
    unittest.main()


class NarrativeChainWiringTests(unittest.TestCase):
    """🔴 report_keys 必須走完整條線，不能在中途被靜默丟棄。

    ## 實測發現（2026-07-29）

    前端送 `{report_keys: [...], instruction: "..."}`，實測 payload：

        {'cli_kind':'claude', 'based_on_version':'v1', 'instruction':'用白話說'}
                                          ↑ report_keys 不見了

    ⚠ `CreateAiTaskRequest` 沒宣告 `report_keys`，**Pydantic 對未知欄位靜默忽略**
    ——API 照樣回 200、job 照樣建立，只是永遠整份重跑。使用者以為只重產一張。

    ⚠ 本專案今日第二次同型錯誤（前次為前端送 `aliases`、後端欄位是 `variants`）。
    只驗兩端、不驗中間那段就會漏掉。本測試**逐段**驗：
    API model → to_payload → handler → runner。
    """

    def test_api_model_declares_report_keys(self):
        from backend.app.api.ai_tasks import CreateAiTaskRequest

        self.assertIn("report_keys", CreateAiTaskRequest.model_fields,
                      "API model 未宣告 report_keys，Pydantic 會靜默丟棄")

    def test_payload_carries_report_keys(self):
        """to_payload 要把它帶下去（這是實測抓到斷點的那一段）。"""
        from backend.app.api.ai_tasks import CreateAiTaskRequest

        req = CreateAiTaskRequest(**{
            "task_type": "ai:narrative", "based_on_version": "v1",
            "report_keys": ["application_trend"], "instruction": "用白話說"})
        payload = req.to_payload()
        self.assertEqual(payload.get("report_keys"), ["application_trend"],
                         "report_keys 未進 payload，worker 收不到")
        self.assertEqual(payload.get("instruction"), "用白話說")

    def test_handler_forwards_report_keys(self):
        """handler 要把 payload 的 report_keys 傳給 runner。"""
        import inspect

        from backend.app.worker.handlers import handle_ai_narrative

        src = inspect.getsource(handle_ai_narrative)
        self.assertIn("report_keys", src,
                      "handler 未轉傳 report_keys，runner 收不到")

    def test_runner_accepts_report_keys(self):
        """runner 簽名要收得下（前面幾段都通了才有意義）。"""
        import inspect

        from backend.app.worker.ai_narrative_runner import run_narrative

        self.assertIn("report_keys", inspect.signature(run_narrative).parameters)


class NarrativeUnifiedEntryTests(unittest.TestCase):
    """統一入口：初次一次跑全部、重解讀各報表獨立（2026-07-30 使用者定案）。

    使用者原話：「AI 解讀如果要統一入口要做成初次和重解讀都能啟動，
    現在沒有 prompt 就無法啟動，初次通常是一次跑全部而重解讀是個報表獨立」。

    ⚠ 查證：`instruction` 本來就選填（空字串不送），程式沒擋。
    真正缺的是**「一次跑全部」的入口**——各報表下方只有單張重產鈕。

    兩者走**同一支** `runNarrative`，差別只在有沒有帶 `report_keys`，不是兩條路。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_run_all_button_exists(self):
        """檢視列要有「產生全部解讀」入口（初次用）。"""
        self.assertIn("btn-run-all-narrative", self.html,
                      "缺一次跑全部的入口，初次只能逐張點")

    def test_run_all_omits_report_keys(self):
        """整份重跑＝不帶 report_keys（runner 據此走原本的全份語意）。"""
        self.assertIn('onclick="runNarrative()"', self.html,
                      "全部解讀鈕應不帶 scopeKey")

    def test_prompt_box_only_for_single(self):
        """🔴 整份重跑不得讀單張的輸入框。

        那個框屬於目前檢視的那張報表，整份重跑時讀它會把單張的需求
        誤當成全域需求套用到所有報表。
        """
        body = _fn_narrative = re.search(
            r"async function runNarrative\(.*?\n\}", self.html, re.S).group(0)
        self.assertIn("scopeKey ? el('narrative-prompt') : null", body,
                      "整份重跑仍會讀單張輸入框")

    def test_button_label_restored_not_hardcoded(self):
        """⚠ 還原按鈕字面不可寫死——同一支函式服務兩顆按鈕。"""
        body = re.search(
            r"async function runNarrative\(.*?\n\}", self.html, re.S).group(0)
        self.assertNotIn("btn.textContent = '產生 AI 解讀'", body,
                         "寫死字面會把「產生全部解讀」改名")
        self.assertIn("btnLabel", body, "未保存原字面供還原")


class OnclickQuotingTests(unittest.TestCase):
    """🔴 內嵌 onclick 的字串參數不得用雙引號（2026-07-30 使用者實機回報「按不下去」）。

    ## 根因

    `onclick="runNarrative(' + JSON.stringify(scopeKey) + ')"` 產出：

        onclick="runNarrative("ipc_main_distribution")"
                             ↑ 這個雙引號**提前結束 onclick 屬性**

    headless 瀏覽器實測：

        修正前 onclick 屬性: 'f('        ← 被截斷，點擊毫無反應
        修正後 onclick 屬性: "f('ipc_main_distribution')"

    ⚠ 靜默失敗：HTML 不會報錯、按鈕照樣畫得出來、滑鼠指標照樣變手形，
    只是點下去什麼都沒發生——比拋錯更難查。

    ## 修法

    本檔其餘 onclick 一律用單引號包參數（`onclick="fn(\'x\')"`）。
    ⚠ 連帶要跳脫參數內的單引號，避免同型問題換個方向再發生。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_no_json_stringify_inside_onclick(self):
        """onclick 內不得用 JSON.stringify——它產雙引號，會截斷屬性。"""
        hits = re.findall(r"onclick=\"[^\"]*JSON\.stringify", self.html)
        self.assertEqual(
            hits, [],
            f"onclick 內用了 JSON.stringify（雙引號會截斷屬性）：{hits}")

    def test_rerun_button_onclick_is_callable(self):
        """重產鈕的 onclick 要是完整、可呼叫的表達式。"""
        match = re.search(
            r"id=\"btn-run-narrative\"'\s*\+\s*'\s*onclick=\"([^\"]*)\"", self.html)
        self.assertIsNotNone(match, "找不到重產鈕的 onclick")
        expr = match.group(1)
        self.assertIn("runNarrative(", expr)
        self.assertTrue(expr.rstrip().endswith(")") or "'" in expr,
                        f"onclick 表達式不完整：{expr!r}")


class CliResultNoneSafetyTests(unittest.TestCase):
    """🔴 `parse_cli_result` 對 stdout/stderr 為 None 要有防護（2026-07-30 實機 failed）。

    ## 問題

    使用者按「產生 PPT」→ job #132 failed，訊息：

        AttributeError: 'NoneType' object has no attribute 'splitlines'

    ⚠ 那個 AttributeError **逃出了 NarrativeRunnerError**，訊息完全看不出
    是哪個環節、哪個變數——比明確的錯誤更難查。

    ## 根因

    `parse_cli_result` 三處直接 `.strip()`：

        result.stderr.strip() or result.stdout.strip()   # exit != 0 分支
        text = result.stdout.strip()

    ⚠ `_default_build_ppt` 已於 6f50611 加了 `completed.stdout or ""` 防護，
    但 **`parse_cli_result` 沒有**——同一個坑在相鄰模組漏一處。

    實測重現：注入 stdout=None 的 fake runner，錯誤指向
    ai_narrative_runner.py:242。
    """

    @staticmethod
    def _result(exit_code=0, stdout=None, stderr=None):
        from backend.app.worker.ai_narrative_runner import CliResult

        return CliResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

    def test_none_stdout_raises_runner_error(self):
        """stdout=None 要拋 NarrativeRunnerError，不得是 AttributeError。"""
        from backend.app.worker.ai_narrative_runner import (
            NarrativeRunnerError,
            parse_cli_result,
        )

        with self.assertRaises(NarrativeRunnerError):
            parse_cli_result(self._result(stdout=None))

    def test_none_on_failure_path(self):
        """⚠ exit != 0 且兩者皆 None——原本會在組錯誤訊息時就 AttributeError。"""
        from backend.app.worker.ai_narrative_runner import (
            NarrativeRunnerError,
            parse_cli_result,
        )

        with self.assertRaises(NarrativeRunnerError) as ctx:
            parse_cli_result(self._result(exit_code=1, stdout=None, stderr=None))
        self.assertIn("exit=1", str(ctx.exception), "錯誤訊息要保留退出碼")

    def test_message_is_actionable(self):
        """錯誤訊息要看得出是「CLI 沒有輸出」，不是一個裸的 AttributeError。"""
        from backend.app.worker.ai_narrative_runner import (
            NarrativeRunnerError,
            parse_cli_result,
        )

        with self.assertRaises(NarrativeRunnerError) as ctx:
            parse_cli_result(self._result(stdout=None))
        msg = str(ctx.exception)
        self.assertTrue(
            "無" in msg or "空" in msg,
            f"訊息看不出是無輸出：{msg}")
