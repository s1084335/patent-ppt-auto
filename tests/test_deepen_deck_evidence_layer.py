"""P2：簡報依據層級與口徑欄位治理的契約測試。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_deck_caliber_page import _minimal_content

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"
PY = sys.executable
BLOCKED_TEMPLATE_TERMS = ("本簡報怎麼讀", "圖表原則", "待驗證", "降級")
VAGUE_EVIDENCE_EXAMPLES = ("依據：整體統計", "依據：資料分析", "依據：AI 判斷")
INTERNAL_KEY_EXAMPLES = ("family_country_layout",)


def _walk_text(value):
    """遞迴取出範本中的可見文字，用於檢查 CLI 指引不殘留禁用詞。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk_text(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_text(item)


def _run_check(content: dict) -> subprocess.CompletedProcess:
    """以實際 CLI 腳本驗證 content gate。"""
    tmp = tempfile.TemporaryDirectory()
    work = Path(tmp.name)
    path = work / "content.json"
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [PY, str(SCRIPTS / "check_content.py"), str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tmp.cleanup()
    return proc


class EvidenceGateTests(unittest.TestCase):
    """建議句必須可追 evidence，流程狀態不得印進投影片。"""

    # 🔴 2026-08-19（§9.3）：本組原本把依據紀律驗在 `recommendations[].lines`，
    #    建議頁退場後那個落點已不存在。**紀律沒有跟著退場**——它擋的是
    #    「接不上依據的建議句」，而結論頁的行動同樣是建議，只是換了落點到
    #    `conclusions.rows[].evidence`（check_content._check_p2_evidence_rules）。
    #    ⚠ 這種時候最容易犯的錯是把測試刪掉：閘門還在、測試沒了，
    #      日後閘門被改壞不會有人知道。
    def _content(self) -> dict:
        content = _minimal_content()
        content.pop("read_me", None)
        content.pop("chart_rule", None)
        content["pages"] = [{"title": "測試頁", "takeaway": "測試", "charts": [],
                             "lines": ["測試內容"], "tag": None}]
        content["conclusions"]["rows"] = [
            {"topic": "構型主題", "finding": "測試發現", "reading": "測試判讀",
             "action": "細讀比對",
             "evidence": "依據：CN 121754862 獨立項第 1 要素"},
            {"topic": "低密度主題", "finding": "測試發現", "reading": "測試判讀",
             "action": "佈局", "evidence": "依據：申請年×主題統計"},
        ]
        content["conclusions"]["covered"] = "2/2"
        return content

    def test_conclusions_with_evidence_pass(self):
        proc = _run_check(self._content())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_conclusion_without_evidence_fails(self):
        content = self._content()
        content["conclusions"]["rows"][0]["evidence"] = ""
        content["conclusions"]["rows"][0]["reading"] = "先做構型比對，再決定是否迭代。"
        proc = _run_check(content)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("依據：", proc.stdout)

    def test_workflow_state_words_are_blocked(self):
        """流程狀態詞不得印上投影片——不論它出現在哪一頁。"""
        content = self._content()
        content["pages"][0]["lines"] = ["待驗證：先放進短期策略。"]
        proc = _run_check(content)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("待驗證", proc.stdout)

    def test_vague_evidence_examples_are_blocked(self):
        for phrase in VAGUE_EVIDENCE_EXAMPLES:
            with self.subTest(phrase=phrase):
                content = self._content()
                content["conclusions"]["rows"][0]["evidence"] = phrase
                proc = _run_check(content)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("空泛依據", proc.stdout)

    def test_internal_evidence_keys_are_not_user_facing(self):
        for key in INTERNAL_KEY_EXAMPLES:
            with self.subTest(key=key):
                content = self._content()
                content["conclusions"]["rows"][0]["evidence"] = f"依據：{key}"
                proc = _run_check(content)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("內部欄位", proc.stdout)


class RetiredCoverRuleFieldsTests(unittest.TestCase):
    """P2 後 read_me/chart_rule 不再是必要 schema 欄位。"""

    def test_content_without_read_me_and_chart_rule_passes_gate(self):
        content = _minimal_content()
        content.pop("read_me", None)
        content.pop("chart_rule", None)
        content["pages"] = [{"title": "測試頁", "takeaway": "測試", "charts": [],
                             "lines": ["測試內容"], "tag": None}]
        proc = _run_check(content)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TemplateContractTests(unittest.TestCase):
    """正式撰稿範本不得再引導 CLI 產出口徑規則卡。"""

    def test_content_template_retired_cover_rule_fields(self):
        template = json.loads(
            (PROJECT_ROOT / "skills" / "html-report-to-deck"
             / "references" / "content-template.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("read_me", template)
        self.assertNotIn("chart_rule", template)
        # 🔴 2026-08-19（§9.3）：範本的 `recommendations` 已退場，
        #    「依據：」示範改掛結論列的 `evidence`。⚠ 仍要驗**範本本身**帶示範句
        #    ——CLI 照範本寫，範本不示範就等於沒有這條紀律。
        self.assertNotIn("recommendations", template,
                         "建議頁已退場，範本不得再宣告 recommendations")
        rows = template["conclusions"]["rows"]
        self.assertTrue(rows, "範本的結論頁沒有任何示範列")
        for row in rows:
            joined = "\n".join([str(row.get("evidence") or ""),
                                str(row.get("reading") or "")])
            self.assertIn("依據：", joined,
                          f"範本結論列「{row.get('topic')}」沒有示範依據句")

        template_text = "\n".join(_walk_text(template))
        for term in BLOCKED_TEMPLATE_TERMS:
            self.assertNotIn(term, template_text)

    def test_skill_prompt_rejects_vague_evidence_examples(self):
        skill_text = (PROJECT_ROOT / "skills" / "html-report-to-deck"
                      / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("空泛依據", skill_text)
        for phrase in VAGUE_EVIDENCE_EXAMPLES:
            self.assertIn(phrase, skill_text)
        self.assertIn("可追錨點", skill_text)
        self.assertIn("中文顯示名稱", skill_text)
        self.assertIn("家族國家布局", skill_text)


if __name__ == "__main__":
    unittest.main()
