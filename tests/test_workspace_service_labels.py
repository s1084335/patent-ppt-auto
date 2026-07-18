"""workspace_service LLM 寫回介面的驗證規則測試。

涵蓋 2026-07-16 code review 的修正：label_source 預設 llm（0010 constraint）、
apply_candidate_explanations 不靜默跳過、apply 端字數硬上限，以及
sources.py 的通道命名導向。驗證邏輯都在連 DB 之前執行，raise 類測試
不需資料庫；成功路徑以 mock psycopg.connect 驗證組出的參數。
"""
from __future__ import annotations

import unittest
from unittest import mock

from backend.app.clustering import workspace_service
from backend.app.clustering.sources import SOURCE_SPECS, get_source_spec


def _mock_cursor(connect: mock.MagicMock, rowcount: int = 1) -> mock.MagicMock:
    """取出 with psycopg.connect(...) / with conn.cursor(...) 的 cursor mock。"""
    conn = connect.return_value.__enter__.return_value
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.rowcount = rowcount
    return cursor


class ApplyTopicLabelsTests(unittest.TestCase):
    """apply_topic_labels 的 source 白名單與字數硬上限。"""

    def test_default_source_is_llm(self):
        """不帶 source 時必須落在 0010 constraint 允許的 'llm'，不得再是 claude_cli。"""
        with mock.patch.object(workspace_service.psycopg, "connect") as connect:
            cursor = _mock_cursor(connect)
            workspace_service.apply_topic_labels(
                workspace_id=1,
                source_field="wips_independent_claims",
                labels=[{"topic_id": 2, "label": "阻力調節機構"}],
            )
        rows = cursor.executemany.call_args.args[1]
        self.assertEqual(rows[0][2], "llm")

    def test_rejects_source_outside_whitelist(self):
        """manual 與舊值 claude_cli 都不得走 AI 寫回路徑。"""
        for bad_source in ("claude_cli", "manual", "gpt"):
            with self.assertRaises(ValueError):
                workspace_service.apply_topic_labels(
                    workspace_id=1,
                    source_field="wips_independent_claims",
                    labels=[{"topic_id": 2, "label": "傳動結構", "source": bad_source}],
                )

    def test_rejects_label_over_hard_limit(self):
        with self.assertRaises(ValueError):
            workspace_service.apply_topic_labels(
                workspace_id=1,
                source_field="wips_independent_claims",
                labels=[
                    {"topic_id": 2, "label": "超" * (workspace_service.LABEL_MAX_CHARS + 1)}
                ],
            )

    def test_rejects_summary_over_hard_limit(self):
        with self.assertRaises(ValueError):
            workspace_service.apply_topic_labels(
                workspace_id=1,
                source_field="wips_independent_claims",
                labels=[
                    {
                        "topic_id": 2,
                        "label": "傳動結構",
                        "summary": "長" * (workspace_service.SUMMARY_MAX_CHARS + 1),
                    }
                ],
            )


class ApplyCandidateExplanationsTests(unittest.TestCase):
    """apply_candidate_explanations 不靜默跳過，回報 requested/updated。"""

    def test_empty_list_raises(self):
        with self.assertRaises(ValueError):
            workspace_service.apply_candidate_explanations(run_id=4, explanations=[])

    def test_missing_candidate_id_raises(self):
        with self.assertRaises(ValueError):
            workspace_service.apply_candidate_explanations(
                run_id=4, explanations=[{"explanation": "少了 candidate_id"}]
            )

    def test_empty_explanation_raises(self):
        with self.assertRaises(ValueError):
            workspace_service.apply_candidate_explanations(
                run_id=4, explanations=[{"candidate_id": 1, "explanation": "  "}]
            )

    def test_explanation_over_hard_limit_raises(self):
        with self.assertRaises(ValueError):
            workspace_service.apply_candidate_explanations(
                run_id=4,
                explanations=[
                    {
                        "candidate_id": 1,
                        "explanation": "長" * (workspace_service.EXPLANATION_MAX_CHARS + 1),
                    }
                ],
            )

    def test_returns_requested_and_updated_counts(self):
        """壞 candidate_id 由 requested/updated 差異呈現，不再無聲吞掉。"""
        with mock.patch.object(workspace_service.psycopg, "connect") as connect:
            _mock_cursor(connect, rowcount=1)
            result = workspace_service.apply_candidate_explanations(
                run_id=4,
                explanations=[
                    {"candidate_id": 1, "explanation": "保守方案主題較少，適合快速概覽全貌。"},
                    {"candidate_id": 2, "explanation": "平衡方案兼顧粒度與可讀性，適合多數情境。"},
                ],
            )
        self.assertEqual(result, {"requested_count": 2, "updated_count": 2})


class InstructionAndSourceSpecTests(unittest.TestCase):
    """instruction 字數定案與通道命名導向。"""

    def test_hard_limits_leave_headroom_over_suggestions(self):
        """硬上限必須大於建議上限，避免正常輸出被誤擋。"""
        self.assertGreaterEqual(workspace_service.LABEL_MAX_CHARS, 8)
        self.assertGreaterEqual(workspace_service.SUMMARY_MAX_CHARS, 40)
        self.assertGreaterEqual(workspace_service.EXPLANATION_MAX_CHARS, 40)

    def test_payload_doc_limit_is_ten_and_within_storage_limit(self):
        """2026-07-17 使用者定案：傳給 LLM 10 筆；DB 儲存上限 15 筆不變。"""
        self.assertEqual(workspace_service.LLM_PAYLOAD_DOC_LIMIT, 10)
        self.assertEqual(workspace_service.LLM_REPRESENTATIVE_DOC_LIMIT, 15)
        self.assertLessEqual(
            workspace_service.LLM_PAYLOAD_DOC_LIMIT,
            workspace_service.LLM_REPRESENTATIVE_DOC_LIMIT,
        )

    def test_naming_hint_is_channel_specific(self):
        technical = get_source_spec("wips_independent_claims").naming_hint
        effect = get_source_spec("effect_summary").naming_hint
        self.assertIn("技術", technical)
        self.assertIn("功效", effect)
        self.assertNotEqual(technical, effect)

    def test_all_source_specs_declare_naming_hint(self):
        for spec in SOURCE_SPECS.values():
            self.assertTrue(spec.naming_hint.strip())


if __name__ == "__main__":
    unittest.main()
