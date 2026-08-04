"""CLI JSON 取出必須容忍前後贅字（2026-07-27 實機 9g）。

實機兩次失敗，同一個原因：

- `ai:irrelevant_filter #102`（跑 183 秒、第一批 11 筆已落庫）
  CLI 回 `依契約輸出：\\n\\n```json\\n{...}\\n``` `
- `ai:patent_note #122`
  CLI 回 `以下為契約指定的 JSON 物件：\\n\\n```json\\n{...}\\n``` `

兩者都因為剝 code fence 的邏輯只判斷 `text.startswith("```")`——**前面多一句話就整段
原樣丟給 json.loads** → `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`。

⚠ 這段程式碼**複製了七份**（七支 runner 各一），所以要收口成共用函式，
不是逐支修——逐支修＝下次又只修到其中幾支（本專案本日已 12 次同型斷鏈）。

prompt 已明令「只輸出 JSON、不要多餘說明」，但 LLM 偶爾仍會加開場白。
**解析端要容錯**：能取到合法 JSON 就取，不因為多一句話讓整趟 AI 判讀（含已花的 token）作廢。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

WORKER_DIR = PROJECT_ROOT / "backend" / "app" / "worker"


class ExtractJsonPayloadTests(unittest.TestCase):
    """共用取 JSON 函式：容忍圍欄前後的贅字。"""

    def _extract(self, text: str):
        from backend.app.worker.ai_payload_file import extract_json_payload

        return extract_json_payload(text)

    def test_plain_json(self):
        """純 JSON（最理想情況）。"""
        self.assertEqual(self._extract('{"results": [1, 2]}'), {"results": [1, 2]})

    def test_fenced_json(self):
        """```json 圍欄，開頭就是圍欄。"""
        self.assertEqual(
            self._extract('```json\n{"results": [1]}\n```'), {"results": [1]})

    def test_fence_without_language_tag(self):
        """``` 圍欄不帶語言標記。"""
        self.assertEqual(self._extract('```\n{"a": 1}\n```'), {"a": 1})

    def test_prefix_then_fence_irrelevant_filter_case(self):
        """🔴 實機 job 102：圍欄前有「依契約輸出：」。"""
        raw = '依契約輸出：\n\n```json\n{"results": [{"patent_id": 114, "verdict": "不相干"}]}\n```'
        self.assertEqual(
            self._extract(raw),
            {"results": [{"patent_id": 114, "verdict": "不相干"}]})

    def test_prefix_then_fence_patent_note_case(self):
        """🔴 實機 job 122：圍欄前有「以下為契約指定的 JSON 物件：」。"""
        raw = '以下為契約指定的 JSON 物件：\n\n```json\n{"notes": [{"patent_id": 151, "note": "x"}]}\n```'
        self.assertEqual(
            self._extract(raw), {"notes": [{"patent_id": 151, "note": "x"}]})

    def test_suffix_after_fence(self):
        """圍欄後有補充說明。"""
        raw = '```json\n{"a": 1}\n```\n\n以上共 1 筆，如需調整請告知。'
        self.assertEqual(self._extract(raw), {"a": 1})

    def test_prefix_and_suffix(self):
        """前後都有贅字。"""
        raw = '好的，結果如下：\n```json\n{"a": 1}\n```\n希望對你有幫助。'
        self.assertEqual(self._extract(raw), {"a": 1})

    def test_bare_json_with_prefix_no_fence(self):
        """沒有圍欄、但 JSON 前有贅字（取第一個 { 到最後一個 }）。"""
        raw = '結果：\n{"a": 1, "b": {"c": 2}}'
        self.assertEqual(self._extract(raw), {"a": 1, "b": {"c": 2}})

    def test_nested_braces_not_truncated(self):
        """巢狀物件不得被截斷（取最後一個 } 而非第一個）。"""
        raw = 'x\n{"outer": {"inner": [1, 2]}, "tail": "y"}\nz'
        self.assertEqual(
            self._extract(raw), {"outer": {"inner": [1, 2]}, "tail": "y"})

    def test_raises_when_no_json(self):
        """完全沒有 JSON 時明確 raise，不回 None 讓呼叫端誤判成空結果。"""
        with self.assertRaises(ValueError):
            self._extract("我無法完成這個任務。")

    def test_raises_on_malformed_json(self):
        """有圍欄但內容不是合法 JSON 時 raise（不靜默吞掉）。"""
        with self.assertRaises(ValueError):
            self._extract('```json\n{"a": ,}\n```')


class AllRunnersUseSharedExtractorTests(unittest.TestCase):
    """七支 runner 都必須走共用函式，不得各自實作。

    同一段剝圍欄邏輯原本複製了七份，實機兩支炸掉才發現。收口成一份後，
    修一次全部受益；逐支修＝下次又只修到其中幾支。
    """

    RUNNERS = (
        "ai_candidate_explanation_runner",
        "ai_company_zh_name_runner",
        "ai_irrelevant_filter_runner",
        "ai_patent_note_runner",
        "ai_report_ppt_runner",
        "ai_topic_label_runner",
    )

    def test_no_runner_has_own_fence_stripping(self):
        """沒有 runner 自己寫 startswith("```")。"""
        for name in self.RUNNERS:
            with self.subTest(runner=name):
                src = (WORKER_DIR / f"{name}.py").read_text(encoding="utf-8")
                self.assertNotIn(
                    'startswith("```")', src,
                    f"{name} 仍自己剝 code fence——應改用 "
                    "ai_payload_file.extract_json_payload（實機 9g）")

    def test_runners_import_shared_extractor(self):
        """每支 runner 都要用到共用函式。"""
        for name in self.RUNNERS:
            with self.subTest(runner=name):
                src = (WORKER_DIR / f"{name}.py").read_text(encoding="utf-8")
                self.assertIn(
                    "extract_json_payload", src,
                    f"{name} 未使用共用的 extract_json_payload")


if __name__ == "__main__":
    unittest.main()
