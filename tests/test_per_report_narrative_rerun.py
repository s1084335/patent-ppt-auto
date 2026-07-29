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
