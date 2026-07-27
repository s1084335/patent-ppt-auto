"""主題→專利指派關係的讀取來源契約（2026-07-27 實機回歸測試）。

症狀（使用者截圖）：
1. 分類區點某個主題 → 專利表除「主附圖／申請國家」外**全部欄位空白**；
2. 「全部」檢視下「技術分類／功效分類」兩欄也永遠空白。

根因：兩處讀取端都去 topic JSON 找 `patent_ids` 這個鍵——
- `app_layer/workspace_queries.py::_topic_assignment_map`
- `api/topics.py::list_topic_patents`

但 `clustering/runner.py::_persist_final_topics` 寫入的 topic **沒有這個鍵**
（實測鍵為 label／keywords／doc_count／representative_patent_ids… 等），
指派關係另外寫在 `derived_layer.topic_assignments` 表（run_id, patent_id, topic_key）。

`dict.get("patent_ids", [])` 於是永遠回 []，靜默取不到任何專利。
此為當日第五次「寫入端與讀取端落點不一致」（見 decisions.md 2026-07-27
「同一欄位不得有兩種落點」）。

修法：兩處讀取端一律改走 `derived_layer.topic_assignments`，
不再依賴 topic JSON 內不存在的 patent_ids。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


def _strip_comments_and_docstrings(body: str) -> str:
    """去掉 # 註解與三引號 docstring，只留可執行程式碼。

    註解裡會為了說明歷史 bug 而寫出 `topic.get("patent_ids")` 字樣，
    不去掉會讓「不得讀 patent_ids」的斷言誤判。
    """
    body = re.sub(r'"""[\s\S]*?"""', "", body)
    body = re.sub(r"'''[\s\S]*?'''", "", body)
    return "\n".join(re.sub(r"#.*$", "", line) for line in body.splitlines())

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_QUERIES = PROJECT_ROOT / "backend" / "app" / "app_layer" / "workspace_queries.py"
API_TOPICS = PROJECT_ROOT / "backend" / "app" / "api" / "topics.py"
RUNNER = PROJECT_ROOT / "backend" / "app" / "clustering" / "runner.py"


class TopicAssignmentSourceTests(unittest.TestCase):
    """指派關係只能來自 topic_assignments 表，不得讀 topic JSON 的 patent_ids。"""

    def test_writer_does_not_produce_patent_ids_key(self):
        """前提確認：寫入端從未產生 patent_ids 鍵（本 bug 的成因）。

        若哪天寫入端真的開始寫這個鍵，本測試會提醒重新評估讀取策略。
        """
        source = RUNNER.read_text(encoding="utf-8")
        start = source.index("def _persist_final_topics")
        end = source.index("\ndef ", start + 1)
        body = _strip_comments_and_docstrings(source[start:end])
        self.assertNotIn(
            '"patent_ids"', body,
            "寫入端若已寫 patent_ids，讀取端策略需重新評估",
        )

    def test_workspace_queries_reads_assignments_table(self):
        """_topic_assignment_map 必須查 topic_assignments，不得讀 topic['patent_ids']。"""
        source = WORKSPACE_QUERIES.read_text(encoding="utf-8")
        start = source.index("def _topic_assignment_map")
        end = source.index("\ndef ", start + 1)
        body = _strip_comments_and_docstrings(source[start:end])
        self.assertNotRegex(
            body, r'get\(\s*["\']patent_ids["\']',
            "不得讀 topic JSON 的 patent_ids（該鍵不存在，永遠回空）",
        )
        self.assertIn(
            "topic_assignments", body,
            "指派關係的唯一來源是 derived_layer.topic_assignments",
        )

    def test_api_topics_reads_assignments_table(self):
        """list_topic_patents 必須查 topic_assignments，不得讀 topic['patent_ids']。"""
        source = API_TOPICS.read_text(encoding="utf-8")
        start = source.index("def list_topic_patents")
        end = source.index("\n@router", start + 1)
        body = _strip_comments_and_docstrings(source[start:end])
        self.assertNotRegex(
            body, r'get\(\s*["\']patent_ids["\']',
            "不得讀 topic JSON 的 patent_ids（該鍵不存在，永遠回空）",
        )


if __name__ == "__main__":
    unittest.main()
