"""MCP AI 任務工具契約：敘述型回存 'ai:' guard／版本化、get_report_payload、http bearer token。

guard 與 token 測試不需 DB；narrative 版本化與 get_report_payload 走拋棄式 0021 庫（setUpClass）。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.getenv("PGPASSWORD")
    return kw


class GuardTests(unittest.TestCase):
    """敘述型 AI 落點護欄（純邏輯，無 DB）。"""

    def test_require_ai_prefix_rejects_non_ai(self):
        from backend.app.mcp_server.tools_ai import require_ai_prefix, NarrativeGuardError
        require_ai_prefix("ai:narrative:market_size")  # 合法
        for bad in ("chart:applicant", "export:pptx", "understanding", "topic:label"):
            with self.assertRaises(NarrativeGuardError):
                require_ai_prefix(bad)

    def test_section_must_not_self_prefix_or_empty(self):
        from backend.app.mcp_server.tools_ai import _narrative_output_type, NarrativeGuardError
        self.assertEqual(_narrative_output_type("market_size"), "ai:narrative:market_size")
        for bad in ("", "   ", "ai:already"):
            with self.assertRaises(NarrativeGuardError):
                _narrative_output_type(bad)

    def test_save_rejects_empty_content(self):
        from backend.app.mcp_server.tools_ai import save_analysis_narrative, NarrativeGuardError
        with self.assertRaises(NarrativeGuardError):
            save_analysis_narrative(1, "market_size", "  ", "claude", "v1")


class GetReportPayloadClipTests(unittest.TestCase):
    """get_report_payload 一律裁前 20（PERSIST_RANKING_ROWS）並回 rows_total（純 mock，無 DB）。

    上輪回報聲稱「有裁剪」但程式未裁——本測直接鎖行為：>20 列來源必裁、rows_total 為裁前總數。
    """

    def test_rows_clipped_to_top20_with_total(self):
        from unittest import mock

        from backend.app.mcp_server.tools_ai import get_report_payload
        from backend.app.reports.chart_runner import PERSIST_RANKING_ROWS
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        name = sorted(REPORT_DEFINITIONS)[0]  # 用真報表名，免 patch 白名單
        big = {name: {"columns": ["i"], "rows": [{"i": i} for i in range(25)]}}
        with mock.patch("backend.app.reports.report_engine.run_reports_batch", return_value=big):
            payload = get_report_payload(name)
        self.assertEqual(len(payload["data"]["rows"]), PERSIST_RANKING_ROWS)  # 裁至 20
        self.assertEqual(payload["data"]["rows"][0]["i"], 0)                  # 取前 20、保序
        self.assertEqual(payload["data"]["rows_total"], 25)                   # 裁前總數
        self.assertEqual(payload["rows_total"], 25)
        self.assertEqual(payload["row_count"], PERSIST_RANKING_ROWS)


class HttpTokenTests(unittest.TestCase):
    """http 傳輸 bearer token：無 token 401、正確 token 放行（in-process ASGI，無 DB）。"""

    def test_bearer_token_gate(self):
        # 單一 app＋單一 lifespan（FastMCP session manager 只能初始化一次），一次驗兩態。
        from starlette.testclient import TestClient
        from backend.app.mcp_server.server import mcp
        from backend.app.mcp_server._auth import BearerTokenMiddleware
        token = "secret-tok"
        app = BearerTokenMiddleware(mcp.streamable_http_app(), token)
        body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        accept = "application/json, text/event-stream"
        with TestClient(app) as client:  # context manager 跑 ASGI lifespan（middleware 透傳）
            no_token = client.post("/mcp/", headers={"Accept": accept}, json=body)
            good = client.post("/mcp/", headers={"Authorization": f"Bearer {token}",
                                                 "Accept": accept}, json=body)
            bad = client.post("/mcp/", headers={"Authorization": "Bearer wrong",
                                                "Accept": accept}, json=body)
        self.assertEqual(no_token.status_code, 401)   # 無 token 拒
        self.assertEqual(bad.status_code, 401)        # 錯 token 拒
        self.assertNotEqual(good.status_code, 401)    # 正確 token 通過 auth，交 MCP 處理


class NarrativeDbTests(unittest.TestCase):
    """敘述型回存版本化＋get_report_payload（拋棄式 0021 庫）。"""

    TEST_DB = "patent_ppt_mcpai"
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
            cls.run_id = c.execute(
                "INSERT INTO app_layer.workflow_runs (run_type, status, request_json) "
                "VALUES ('report_generate', 'succeeded', '{}'::jsonb) RETURNING run_id"
            ).fetchone()[0]
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

    def test_narrative_versioned_append_carries_contract(self):
        from backend.app.mcp_server.tools_ai import save_analysis_narrative
        r1 = save_analysis_narrative(self.run_id, "application_trend", "近年高峰於 2020。",
                                     "claude-opus-4-8", "report_narrative_v1",
                                     based_on_version="report_trial_20260721")
        r2 = save_analysis_narrative(self.run_id, "application_trend", "改稿：高峰於 2019-2020。",
                                     "claude-opus-4-8", "report_narrative_v1")
        self.assertEqual((r1["version"], r2["version"]), (1, 2))
        self.assertTrue(r1["output_type"].startswith("ai:"))
        from backend.app.repositories.workflow_outputs_repository import PostgresWorkflowOutputsRepository
        repo = PostgresWorkflowOutputsRepository()
        v1 = repo.get_output(self.run_id, r1["output_type"], version=1)["data_json"]
        self.assertEqual(v1["text"], "近年高峰於 2020。")  # v1 未被覆蓋
        self.assertEqual(v1["based_on_version"], "report_trial_20260721")

    def test_get_report_payload_structure(self):
        from backend.app.mcp_server.tools_ai import get_report_payload
        with self.assertRaises(ValueError):
            get_report_payload("nonexistent_report")
        from backend.app.reports.report_definitions import DEFAULT_REPORT_NAMES
        name = list(DEFAULT_REPORT_NAMES)[0]
        payload = get_report_payload(name)
        self.assertEqual(payload["report_name"], name)
        self.assertEqual(payload["artifact_keys"], [f"{name}.svg"])
        self.assertIn("data", payload)


if __name__ == "__main__":
    unittest.main()
