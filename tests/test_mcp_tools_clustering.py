"""MCP clustering tools（純函式層）的單元測試。

workspace_service 以 mock 取代驗接線與型別轉換；list_workspaces 走真 DB
smoke（連不到就 skip）。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

import psycopg
from alembic import command
from alembic.config import Config

from backend.app.mcp_server import tools_clustering

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.getenv("PGPASSWORD")
    return kw


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
    """拋棄式 0021 庫 smoke：禁連正式庫。patent_count 由 workspaces.patent_ids_json 取。

    種一筆 active（patent_ids_json 3 筆）＋一筆 archived，驗只回 active、patent_count 正確、
    工具 SQL 不碰已下沉 legacy_0021 的 workspace_patents。
    """

    TEST_DB = "patent_ppt_mcpcluster"
    _prev: dict[str, str | None] = {}

    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{cls.TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{cls.TEST_DB}"')
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = cls.TEST_DB
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        command.upgrade(cfg, "head")
        with psycopg.connect(**_kw(cls.TEST_DB)) as c:
            c.execute("INSERT INTO app_layer.workspaces (workspace_name, status, patent_ids_json) "
                      "VALUES ('smoke active', 'active', '[101,102,103]'::jsonb)")
            c.execute("INSERT INTO app_layer.workspaces (workspace_name, status, patent_ids_json) "
                      "VALUES ('smoke archived', 'archived', '[201]'::jsonb)")
            c.commit()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._prev.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{cls.TEST_DB}" WITH (FORCE)')
        except Exception:  # noqa: BLE001
            pass

    def test_shape(self):
        result = tools_clustering.list_workspaces()
        self.assertIsInstance(result["workspaces"], list)
        self.assertIn("wips_independent_claims", result["source_fields"])
        self.assertIn("effect_summary", result["source_fields"])
        names = {w["workspace_name"] for w in result["workspaces"]}
        self.assertEqual(names, {"smoke active"})  # archived 不外洩
        active = result["workspaces"][0]
        self.assertEqual(active["patent_count"], 3)  # 由 patent_ids_json 長度取


if __name__ == "__main__":
    unittest.main()
