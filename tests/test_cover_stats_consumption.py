"""封面數字要被 CLI **逐字消費**，不是自己填（tasks §2，一方產生、一方消費）。

## 為什麼光「引擎有數字」不夠

`report_data.json` 有了 `cover_stats` 之後，如果 deck 的範本仍寫
`["<N>", "件專利"]` 這種占位、閘門也不比對，CLI 照樣可以自己湊——
**引擎供給了但沒人消費**，等於沒修。今天已經看過三次「能力在、守門在，
中間那段沒接上」。

作法沿用既有的 `topic_facts` 紀律：`assemble_from_version` 把引擎數字寫成
intake 檔，`check_content` 逐字比對。

## 順帶收掉的兩個錯誤示範

- 範本 `stats_note` 寫「存活家族 <N> 個」——那是**第二個家族口徑**（§2.4 要移除）
- `narrative.md` 的量詞範例寫「存活家族 46」——**46 是各國相加的錯數字**
  （1.5 已查出：滑雪機 40 個家族分布 4 國，相加得 46）。錯數字當範例會被照抄。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "html-report-to-deck"


class IntakeFileTests(unittest.TestCase):
    def test_assembler_writes_cover_stats_intake(self):
        """🔴 引擎數字要落成 intake 檔，CLI 才有東西可逐字引用。"""
        src = (SKILL / "scripts" / "assemble_from_version.py").read_text(encoding="utf-8")
        self.assertIn(
            "cover_stats.json", src,
            "assemble_from_version 沒有寫出 cover_stats intake——"
            "引擎供給了但 CLI 拿不到，等於沒修")

    def test_intake_comes_from_report_data_not_recomputed(self):
        """⚠ intake 只能搬引擎的值，不得在 deck 端重算（那是第二份定義）。"""
        src = (SKILL / "scripts" / "assemble_from_version.py").read_text(encoding="utf-8")
        self.assertIn(
            'report_data.get("cover_stats")', src,
            "cover_stats 不是從 report_data 取的——deck 端自己算就會與引擎漂開")


class GateTests(unittest.TestCase):
    def test_check_content_verifies_cover_numbers(self):
        """🔴 逐字比對：CLI 改寫封面數字要紅（與 topic_facts 同一條紀律）。"""
        src = (SKILL / "scripts" / "check_content.py").read_text(encoding="utf-8")
        self.assertIn(
            "cover_stats", src,
            "check_content 沒有比對封面數字——CLI 可以照樣自己填")


class TemplateTests(unittest.TestCase):
    def _template(self) -> dict:
        return json.loads(
            (SKILL / "references" / "content-template.json").read_text(encoding="utf-8"))

    def test_stats_note_drops_the_second_family_caliber(self):
        """§2.4：`stats_note` 不得再出現「存活家族」——那是第二個家族口徑。"""
        note = self._template().get("stats_note", "")
        self.assertNotIn(
            "存活家族", note,
            "stats_note 仍寫「存活家族」——封面已有家族數，這是第二個口徑；"
            "而且它的來源（各國相加）本身就是錯的")

    def test_fourth_brick_is_patent_kind(self):
        """§2 定案：封面四格＝件／族／受理局／專利類型（一格三數字）。"""
        labels = [label for _n, label in self._template().get("stats", [])]
        self.assertEqual(len(labels), 4, f"封面不是四格：{labels}")
        self.assertTrue(
            any("類型" in label or "設計" in label for label in labels),
            f"第四格不是專利類型：{labels}——2026-08-18 定案四格為件／族／受理局／類型")


class WritingGuideTests(unittest.TestCase):
    def test_quantifier_example_does_not_teach_a_wrong_number(self):
        """⚠ 錯數字當範例會被照抄。

        `narrative.md` 的量詞範例寫「存活家族 46」——46 是各國相加的結果
        （滑雪機 40 個家族分布 4 國）。1.5 已查出並修掉來源，範例也要換。
        """
        text = (SKILL / "references" / "narrative.md").read_text(encoding="utf-8")
        self.assertNotIn(
            "存活家族 46", text,
            "寫作指引仍以「存活家族 46」當範例——那是加總錯誤產生的數字")


if __name__ == "__main__":
    unittest.main()
