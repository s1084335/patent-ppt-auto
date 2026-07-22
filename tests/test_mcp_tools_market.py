"""market MCP 三工具契約（拋棄式庫）：get／save／aggregate 薄包裝，不重寫邏輯。

沿用 batch1 的拋棄式 0021→head 庫模式；工具走 MarketStore 直連（env PG*），不涉連線池單例。
只驗薄包裝行為：save 落款回 id、get 過濾、save 防重、aggregate 轉 aggregate.py 的 min–max。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

import psycopg
from alembic import command
from alembic.config import Config

from backend.app.mcp_server import tools_market

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.getenv("PGPASSWORD")
    return kw


def _payload(metric_value=None, metric="market_size", **extra):
    value = {"year": 2024, "market_definition": "manufacturer"}
    if metric_value is not None:
        value[metric] = metric_value
    p = {"source_name": "R", "source_url": "https://example.com/r", "published_on": "2024-06-01",
         "reliability": "industry_gov_corp", "summary": "公開摘要逐字", "value": value}
    p.update(extra)
    return p


def _candidate():
    """建立 Claude CLI 產出的候選 market evidence。"""
    payload = _payload(5.0)
    payload["evidence_excerpt"] = (
        "The source describes a measured market signal for commercial users in 2025."
    )
    payload["source_url"] = "https://market.example.org/source-1"
    return {
        "kind": "market_size",
        "scope": "robot mower",
        "target": "US",
        "payload_json": payload,
        "source_url": "https://market.example.org/source-1",
        "summary": "官方或產業來源指出市場訊號存在。",
    }


class MarketMcpWorkflowTests(unittest.TestCase):
    """不碰 DB 的 MCP 候選流程測試。"""

    def test_prepare_market_evidence_task(self):
        """Claude CLI 取得的是研究任務，不是可直接入庫的市場結論。"""
        result = tools_market.prepare_market_evidence_task(
            scope="robot mower",
            targets=["US"],
            kinds=["market_size"],
            report_version="r1",
        )

        self.assertEqual(result["status"], "needs_external_research")
        self.assertEqual(result["output_type"], "market:evidence_candidates")
        self.assertTrue(any("source_url" in rule for rule in result["anti_hallucination_rules"]))

    def test_save_market_evidence_candidates_writes_workflow_output_only(self):
        """候選 evidence 只能先進 workflow_outputs，不可直接寫 market_evidence。"""
        with mock.patch.object(
            tools_market, "save_workflow_output", return_value={"run_id": 9, "version": 2}
        ) as save:
            result = tools_market.save_market_evidence_candidates(
                run_id=9,
                scope="robot mower",
                candidates=[_candidate()],
                report_version="r1",
            )

        self.assertEqual(result["version"], 2)
        args = save.call_args.args
        self.assertEqual(args[0], 9)
        self.assertEqual(args[1], "market:evidence_candidates")
        self.assertFalse(args[2]["guard"]["accepted"])

    def test_accept_market_evidence_candidates_saves_selected_only(self):
        """只有使用者選定的候選 evidence 會寫入正式 market_evidence。"""
        first = _candidate()
        second = _candidate()
        second["source_url"] = "https://market.example.org/source-2"
        second["payload_json"]["source_url"] = "https://market.example.org/source-2"
        with mock.patch.object(tools_market, "MarketStore") as store_cls:
            store_cls.return_value.save_evidence.side_effect = [11]
            result = tools_market.accept_market_evidence_candidates(
                candidates=[first, second],
                accepted_indexes=[1],
            )

        self.assertEqual(result["accepted_count"], 1)
        self.assertEqual(result["ids"], [11])
        saved_args = store_cls.return_value.save_evidence.call_args.args
        self.assertEqual(saved_args[4], "https://market.example.org/source-2")


class MarketMcpDbTests(unittest.TestCase):
    """拋棄式庫：save/get/aggregate 三工具真跑（只碰 derived_layer.market_evidence）。"""

    TEST_DB = "patent_ppt_mcpmarket"
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

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._prev.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{cls.TEST_DB}" WITH (FORCE)')
        except Exception:  # noqa: BLE001
            pass

    def setUp(self):
        with psycopg.connect(**_kw(self.TEST_DB)) as c:
            c.execute("TRUNCATE derived_layer.market_evidence RESTART IDENTITY")
            c.commit()

    def test_save_then_get(self):
        r = tools_market.save_market_evidence(
            "market_size", "割草機", "US", _payload(5.0), "https://m.com/1", "摘要")
        self.assertIn("id", r)
        self.assertTrue(r["accepted"])
        g = tools_market.get_market_evidence(scope="割草機")
        self.assertEqual(g["count"], 1)
        self.assertEqual(g["evidence"][0]["target"], "US")

    def test_save_rejects_duplicate(self):
        from backend.app.market.market_store import DuplicateEvidenceError
        tools_market.save_market_evidence(
            "market_size", "割草機", "US", _payload(5.0), "https://dup.com/x", "a")
        with self.assertRaises(DuplicateEvidenceError):
            tools_market.save_market_evidence(
                "customer", "割草機", "經銷商", _payload(), "https://dup.com/x", "b")

    def test_aggregate_min_max(self):
        tools_market.save_market_evidence(
            "market_size", "割草機", "US", _payload(4.0), "https://a.com/1", "a")
        tools_market.save_market_evidence(
            "market_size", "割草機", "US", _payload(5.0), "https://a.com/2", "b")
        rep = tools_market.aggregate_market_evidence(scope="割草機")
        g = rep["metrics"]["market_size"][0]
        self.assertEqual((g["min"], g["max"]), (4.0, 5.0))


if __name__ == "__main__":
    unittest.main()
