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

    def _content(self) -> dict:
        content = _minimal_content()
        content.pop("read_me", None)
        content.pop("chart_rule", None)
        content["pages"] = [{"title": "測試頁", "takeaway": "測試", "charts": [],
                             "lines": ["測試內容"], "tag": None}]
        content["recommendations"][0]["lines"] = [
            "依據：CN 121754862 獨立項第 1 要素",
            "先做構型比對，再決定是否進入設計迭代。",
        ]
        content["recommendations"][1]["lines"] = [
            "依據：申請年×主題統計",
            "低密度區先做需求驗證。",
        ]
        content["recommendations"][2]["lines"] = [
            "依據：申請人年度布局",
            "避開近期集中申請的構型。",
        ]
        content["recommendations"][3]["lines"] = [
            "依據：家族國家布局",
            "優先看有效權利較密集的國家。",
        ]
        return content

    def test_recommendations_with_evidence_pass(self):
        proc = _run_check(self._content())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_recommendation_without_evidence_fails(self):
        content = self._content()
        content["recommendations"][0]["lines"] = ["先做構型比對，再決定是否迭代。"]
        proc = _run_check(content)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("依據：", proc.stdout)

    def test_workflow_state_words_are_blocked(self):
        content = self._content()
        content["recommendations"][0]["lines"][1] = "待驗證：先放進短期策略。"
        proc = _run_check(content)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("待驗證", proc.stdout)


class RetiredCoverRuleFieldsTests(unittest.TestCase):
    """P2 後 read_me/chart_rule 不再是必要 schema 欄位。"""

    def test_content_without_read_me_and_chart_rule_passes_gate(self):
        content = _minimal_content()
        content.pop("read_me", None)
        content.pop("chart_rule", None)
        content["pages"] = [{"title": "測試頁", "takeaway": "測試", "charts": [],
                             "lines": ["測試內容"], "tag": None}]
        for rec in content["recommendations"]:
            rec["lines"] = ["依據：測試事實", "依據足夠才提出動作。"]
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
        for rec in template["recommendations"]:
            joined = "\n".join(rec["lines"])
            self.assertIn("依據：", joined)

        template_text = "\n".join(_walk_text(template))
        for term in BLOCKED_TEMPLATE_TERMS:
            self.assertNotIn(term, template_text)


if __name__ == "__main__":
    unittest.main()
