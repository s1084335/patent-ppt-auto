"""MCP clustering tools（純函式層）的單元測試。

workspace_service 以 mock 取代驗接線與型別轉換；list_workspaces 走真 DB
smoke（連不到就 skip）。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from backend.app.mcp_server import tools_clustering


class WiringTests(unittest.TestCase):
    """對 workspace_service 的接線：參數正規化、回傳 json_safe。"""

    def test_dashboard_passes_int_workspace_id(self):
        with mock.patch.object(
            tools_clustering.workspace_service, "workspace_dashboard", return_value={"workspace": {}}
        ) as fn:
            tools_clustering.get_workspace_dashboard("7")
        fn.assert_called_once_with(7)

    def test_candidate_payload_passes_int_run_id(self):
        with mock.patch.object(
            tools_clustering.workspace_service, "candidate_review_payload", return_value={}
        ) as fn:
            tools_clustering.get_candidate_review_payload(3)
        fn.assert_called_once_with(3)

    def test_labeling_payload_normalizes_topic_ids(self):
        with mock.patch.object(
            tools_clustering.workspace_service, "topic_labeling_payload", return_value={}
        ) as fn:
            tools_clustering.get_topic_labeling_payload(1, "effect_summary", topic_ids=["5", 6])
        fn.assert_called_once_with(workspace_id=1, source_field="effect_summary", topic_ids=[5, 6])

    def test_labeling_payload_empty_topic_ids_becomes_none(self):
        with mock.patch.object(
            tools_clustering.workspace_service, "topic_labeling_payload", return_value={}
        ) as fn:
            tools_clustering.get_topic_labeling_payload(1, "effect_summary", topic_ids=[])
        fn.assert_called_once_with(workspace_id=1, source_field="effect_summary", topic_ids=None)

    def test_apply_candidate_explanations_wiring(self):
        explanations = [{"candidate_id": 1, "explanation": "保守方案主題較少，適合概覽。"}]
        with mock.patch.object(
            tools_clustering.workspace_service,
            "apply_candidate_explanations",
            return_value={"requested_count": 1, "updated_count": 1},
        ) as fn:
            result = tools_clustering.apply_candidate_explanations("4", explanations)
        fn.assert_called_once_with(run_id=4, explanations=explanations)
        self.assertEqual(result["requested_count"], 1)
        self.assertEqual(result["updated_count"], 1)

    def test_apply_labels_wiring(self):
        labels = [{"topic_id": 2, "label": "傳動結構", "summary": "…"}]
        with mock.patch.object(
            tools_clustering.workspace_service, "apply_topic_labels", return_value={"updated_count": 1}
        ) as fn:
            result = tools_clustering.apply_topic_labels(1, "wips_independent_claims", labels)
        fn.assert_called_once_with(
            workspace_id=1, source_field="wips_independent_claims", labels=labels, updated_by="claude-code"
        )
        self.assertEqual(result["updated_count"], 1)

    def test_merge_history_wrapped_in_key(self):
        with mock.patch.object(
            tools_clustering.workspace_service, "merge_history", return_value=[{"merge_run_id": 9}]
        ):
            result = tools_clustering.get_merge_history(1, "effect_summary")
        self.assertEqual(result["merge_history"][0]["merge_run_id"], 9)


class ListWorkspacesSmokeTests(unittest.TestCase):
    """真 DB smoke：連得到開發庫才跑，連不到就 skip。"""

    @classmethod
    def setUpClass(cls):
        import psycopg
        from dotenv import load_dotenv

        from backend.app.db.connection import get_connection_kwargs

        project_root = Path(__file__).resolve().parents[1]
        load_dotenv(project_root / ".env", override=False)
        try:
            with psycopg.connect(**get_connection_kwargs(), connect_timeout=3):
                pass
        except Exception as exc:  # noqa: BLE001 - 任何連線失敗都代表環境沒 DB
            raise unittest.SkipTest(f"DB unreachable: {exc}")

    def test_shape(self):
        result = tools_clustering.list_workspaces()
        self.assertIsInstance(result["workspaces"], list)
        self.assertIn("wips_independent_claims", result["source_fields"])
        self.assertIn("effect_summary", result["source_fields"])


if __name__ == "__main__":
    unittest.main()
