"""workspace_service LLM 寫回介面的驗證規則測試。

涵蓋 2026-07-16 code review 的修正：label_source 預設 llm（0010 constraint）、
apply_candidate_explanations 不靜默跳過、apply 端字數硬上限，以及
sources.py 的通道命名導向。驗證邏輯都在連 DB 之前執行，raise 類測試
不需資料庫；成功路徑以 mock psycopg.connect 驗證組出的參數。
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from backend.app.clustering import workspace_service
from backend.app.clustering.runner import CANDIDATE_REFERENCE_PARAMETER_KEY
from backend.app.clustering.preprocessing import sha256_text
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
        """不帶 source 時必須落在 0010 constraint 允許的 'llm'，不得再是 claude_cli。

        0021：標籤寫進 topic_state_json->'topics'，改以讀出整份 topics、
        套用後一次寫回；這裡驗寫回內容的 label_source。
        """
        with mock.patch.object(workspace_service.psycopg, "connect") as connect:
            cursor = _mock_cursor(connect)
            # _require_latest_state_run 取最新帶 topics 的 run
            cursor.fetchone.return_value = {
                "run_id": 9,
                "workspace_id": 1,
                "source_field": "wips_independent_claims",
                "topic_state_json": {
                    "topics": [
                        {"topic_id": 2, "topic_code": "T002", "topic_kind": "model",
                         "status": "active", "label_source": "fallback"}
                    ]
                },
            }
            workspace_service.apply_topic_labels(
                workspace_id=1,
                source_field="wips_independent_claims",
                labels=[{"topic_code": "T002", "label": "阻力調節機構"}],
            )
        # 最後一次 execute 是 _merge_topic_state 的整份 topics 寫回
        patch = cursor.execute.call_args.args[1][0].obj
        self.assertEqual(patch["topics"][0]["label_source"], "llm")
        self.assertEqual(patch["topics"][0]["label"], "阻力調節機構")

    def test_rejects_source_outside_whitelist(self):
        """manual 與舊值 claude_cli 都不得走 AI 寫回路徑。"""
        for bad_source in ("claude_cli", "manual", "gpt"):
            with self.assertRaises(ValueError):
                workspace_service.apply_topic_labels(
                    workspace_id=1,
                    source_field="wips_independent_claims",
                    labels=[{"topic_code": "T002", "label": "傳動結構", "source": bad_source}],
                )

    def test_rejects_label_over_hard_limit(self):
        with self.assertRaises(ValueError):
            workspace_service.apply_topic_labels(
                workspace_id=1,
                source_field="wips_independent_claims",
                labels=[
                    {"topic_code": "T002",
                     "label": "超" * (workspace_service.LABEL_MAX_CHARS + 1)}
                ],
            )

    def test_rejects_summary_over_hard_limit(self):
        with self.assertRaises(ValueError):
            workspace_service.apply_topic_labels(
                workspace_id=1,
                source_field="wips_independent_claims",
                labels=[
                    {
                        "topic_code": "T002",
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
        """壞 candidate_id 由 requested/updated 差異呈現，不再無聲吞掉。

        0021：候選在 topic_state_json->'candidates'，updated_count 依實際套用到的
        候選計數；此處給兩筆存在的候選，兩筆都應被更新。
        """
        with mock.patch.object(workspace_service.psycopg, "connect") as connect:
            cursor = _mock_cursor(connect, rowcount=1)
            cursor.fetchone.return_value = (
                {"candidates": [{"candidate_id": 1}, {"candidate_id": 2}]},
            )
            result = workspace_service.apply_candidate_explanations(
                run_id=4,
                explanations=[
                    {"candidate_id": 1, "explanation": "保守方案主題較少，適合快速概覽全貌。"},
                    {"candidate_id": 2, "explanation": "平衡方案兼顧粒度與可讀性，適合多數情境。"},
                ],
            )
        self.assertEqual(result, {"requested_count": 2, "updated_count": 2})

    def test_unknown_candidate_id_is_reported_by_count_gap(self):
        """不屬於此 run 的 candidate_id 只會讓 updated_count 少於 requested_count。"""
        with mock.patch.object(workspace_service.psycopg, "connect") as connect:
            cursor = _mock_cursor(connect, rowcount=1)
            cursor.fetchone.return_value = ({"candidates": [{"candidate_id": 1}]},)
            result = workspace_service.apply_candidate_explanations(
                run_id=4,
                explanations=[
                    {"candidate_id": 1, "explanation": "保守方案主題較少，適合快速概覽全貌。"},
                    {"candidate_id": 99, "explanation": "不屬於此 run 的候選說明。"},
                ],
            )
        self.assertEqual(result, {"requested_count": 2, "updated_count": 1})


class CandidateReviewPayloadTests(unittest.TestCase):
    """候選說明 payload 只輸出主題數指標，不展開代表獨立項。"""

    @staticmethod
    def _run(candidates: list[dict[str, object]]) -> dict[str, object]:
        """0021：load_run_scope 回傳的 run 列，候選與 input_doc_count 都在 state 內。"""
        return {
            "run_id": 4,
            "workspace_id": 2,
            "source_field": "wips_independent_claims",
            "status": "succeeded",
            "topic_state_json": {
                "input_doc_count": 200,
                "candidates": candidates,
            },
        }

    @staticmethod
    def _candidate(parameters: dict[str, object]) -> dict[str, object]:
        return {
            "candidate_id": 7,
            "candidate_type": "conservative",
            "candidate_k": 10,
            "coherence": 0.6,
            "diversity": 0.7,
            "balance": 0.8,
            "score": 0.9,
            # 0021：JSON 內鍵名為 parameters（與 runner._persist_calibration 同源）
            "parameters": parameters,
            "llm_explanation": None,
        }

    def test_payload_uses_metrics_without_representative_documents(self) -> None:
        """候選主題數選擇只交給 LLM 指標，不展開 refs 或文檔全文。"""
        reference = {
            "patent_id": 11,
            "patent_number": "US11",
            "model_topic_id": 3,
            "rank": 1,
            "text_hash": sha256_text("A cleaned independent claim."),
        }
        with mock.patch.object(workspace_service.psycopg, "connect") as connect:
            conn = connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.return_value = self._run(
                [self._candidate(
                    {"topic_count": 1, CANDIDATE_REFERENCE_PARAMETER_KEY: [reference]})]
            )
            payload = workspace_service.candidate_review_payload(4)

        candidate = payload["candidates"][0]
        self.assertEqual(candidate["k"], 10)
        self.assertEqual(candidate["coherence"], 0.6)
        self.assertEqual(candidate["diversity"], 0.7)
        self.assertEqual(candidate["balance"], 0.8)
        self.assertEqual(candidate["score"], 0.9)
        self.assertNotIn("topics", candidate)
        self.assertNotIn(CANDIDATE_REFERENCE_PARAMETER_KEY, candidate["parameters"])
        self.assertNotIn("representative_documents", json.dumps(payload, ensure_ascii=False))
        self.assertIn("不要要求或引用代表文檔", payload["instruction"])

    def test_old_candidate_without_references_can_still_explain_metrics(self) -> None:
        """舊 run 沒有 refs 時仍可做主題數候選指標解釋。"""
        with mock.patch.object(workspace_service.psycopg, "connect") as connect:
            conn = connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.return_value = self._run(
                [self._candidate({"topic_count": 10})]
            )
            payload = workspace_service.candidate_review_payload(4)

        self.assertEqual(payload["candidates"][0]["parameters"], {"topic_count": 10})
        self.assertNotIn("topics", payload["candidates"][0])


class TopicLabelingPayloadTests(unittest.TestCase):
    """topic 標籤/摘要階段才讀代表文件，且不截斷全文。"""

    def test_fetch_source_excerpts_returns_full_text(self) -> None:
        long_text = "claim-" * 500
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = [{"source_text": long_text}]

        result = workspace_service._fetch_source_excerpts(
            cursor, "wips_independent_claims", [11]
        )

        self.assertEqual(result, [long_text])
        self.assertGreater(len(result[0]), 800)

class InstructionAndSourceSpecTests(unittest.TestCase):
    """instruction 字數定案與通道命名導向。"""

    def test_hard_limits_leave_headroom_over_suggestions(self):
        """硬上限必須大於建議上限，避免正常輸出被誤擋。"""
        self.assertGreaterEqual(workspace_service.LABEL_MAX_CHARS, 8)
        self.assertGreaterEqual(workspace_service.SUMMARY_MAX_CHARS, 40)
        self.assertGreaterEqual(workspace_service.EXPLANATION_MAX_CHARS, 40)

    def test_topic_labeling_doc_limit_is_five_and_within_storage_limit(self):
        """正式 topic 標籤/摘要階段每主題讀前 5 筆，DB 仍可保存較多 refs。"""
        self.assertEqual(workspace_service.TOPIC_LABELING_DOC_LIMIT, 5)
        self.assertEqual(workspace_service.LLM_REPRESENTATIVE_DOC_LIMIT, 15)
        self.assertLessEqual(
            workspace_service.TOPIC_LABELING_DOC_LIMIT,
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
