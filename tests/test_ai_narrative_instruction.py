"""AI 助手輸入框的兩個缺口（待辦 C-7b，2026-07-27 修）。

實測：右欄「對目前頁面提出修改需求」送出後，ai:narrative **必定 failed**
（DB 累積 30 筆 failed，錯誤訊息 `full_report_latest\\v 缺 report_data.json`）。

**bug ①**：前端送 `based_on_version = state.nav`——`state.nav` 是頁籤名
（'patents'／'topics'／'reports'），卻被當成報表版本號。runner 的
`resolve_run_dir` 會去找 `full_report_latest/<頁籤名>/report_data.json`，
必然不存在 → NarrativeRunnerError。不論在哪一頁按都失敗。
修法：不送該欄位——runner 未給時本就會自動取最新 report_trial_ 目錄。

**bug ②**：使用者打的 `instruction` payload 有存，但 `ai_narrative_runner`
**零消費**（整份檔案沒出現 instruction 字樣）。placeholder 寫著
「把趨勢圖改成近十年、加一段風險摘要…」，實際上打了也沒作用——
比單純失敗更誤導：看起來成功，卻沒做使用者要求的事。
修法：prompt 納入 instruction；屬 prompt 契約變更故升 prompt_version。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from backend.app.worker import ai_narrative_runner as runner

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"


class FrontendBasedOnVersionTests(unittest.TestCase):
    """bug ①：前端不得把頁籤名當報表版本送出。"""

    def test_does_not_send_nav_as_version(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotRegex(
            html, r"based_on_version\s*=\s*state\.nav",
            "state.nav 是頁籤名不是報表版本，送出必讓 ai:narrative failed；"
            "不帶此欄位時 runner 會自動取最新報表版本",
        )


class NarrativeInstructionTests(unittest.TestCase):
    """bug ②：使用者輸入的 instruction 必須真的進 prompt。"""

    def _prompt(self, instruction=None):
        return runner.build_prompt(
            Path("/tmp/report_trial_x"), "report_trial_x", instruction=instruction
        )

    def test_instruction_appears_in_prompt(self):
        """給了 instruction 就必須出現在 prompt 內（否則使用者白打）。"""
        text = "把趨勢圖改成近十年，並加一段風險摘要"
        self.assertIn(text, self._prompt(text))

    def test_no_instruction_keeps_prompt_clean(self):
        """未給 instruction 時 prompt 不得留下空的需求段落。"""
        p = self._prompt(None)
        self.assertNotIn("使用者額外需求", p)

    def test_instruction_cannot_override_output_contract(self):
        """instruction 是附加需求，不得凌駕輸出契約——prompt 需明示這點。

        使用者可能打「不要寫 narratives.json」「順便改其他檔案」之類的指示；
        AI 必須仍遵守只寫單一檔案、維持 v2 契約。
        """
        p = self._prompt("隨便改點什麼")
        self.assertIn("narratives.json", p)
        self.assertTrue(
            "不得" in p and ("覆蓋" in p or "凌駕" in p or "牴觸" in p),
            "prompt 需明示額外需求不得牴觸輸出契約",
        )

    def test_prompt_version_bumped(self):
        """prompt 契約變更需升版，供產出追溯。

        ⚠ 這支長期斷言 v3、程式卻早在 v4——2026-07-31 發現時已是既有失敗。
        改對齊 runner 的單一來源常數，日後升版不必再改兩處。
        """
        self.assertEqual(runner.PROMPT_VERSION, "report_narrative_v5")


if __name__ == "__main__":
    unittest.main()
