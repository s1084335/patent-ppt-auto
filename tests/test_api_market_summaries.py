"""市場摘要現行版查詢與確認 API 契約測試（/api/v1/market-summaries）。

批3 缺口補齊：store 已有 get_current／get_accepted_current／accept，缺 API 端點。
本檔以拋棄式 DB patent_ppt_apimktsum（upgrade head，絕不碰正式庫 patent_ppt）驗兩端點：
  1. GET /market-summaries/current?workspace_id= → 取現行版草稿（給前端逐筆確認顯示）。
  2. POST /market-summaries/accept {summary_id} → 確認落款 accepted_at。
覆蓋：無摘要回 null、有現行草稿可取、accept 落款後 get_accepted_current 才拿得到、
重按 accept 不重複落款、workspace_id 缺漏 422。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from backend.app.main import app


PREFIX = "/api/v1"
TEST_DB = "patent_ppt_apimktsum"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

WSID = 940101

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    password = os.getenv("PGPASSWORD")
    if password:
        kw["password"] = password
    return kw


def _reset_pool():
    from backend.app.db import connection

    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:
            pass
        connection._pool = None


def _seed_workspace():
    """建一個非全庫 workspace（市場摘要綁 workspace）。"""
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        conn.execute(
            "INSERT INTO app_layer.workspaces "
            "(workspace_id, workspace_name, status, patent_ids_json) "
            "VALUES (%s, %s, 'active', '[]'::jsonb)",
            (WSID, "market ws"),
        )
        conn.commit()


def setUpModule():
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB
    _reset_pool()

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    _seed_workspace()


def tearDownModule():
    _reset_pool()
    for k, v in _prev_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    except Exception:
        pass


class MarketSummaryCurrentTests(unittest.TestCase):
    def setUp(self):
        # 每個測試前清空該 workspace 的摘要，測試互不干擾。
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            conn.execute(
                "DELETE FROM derived_layer.market_doc_summaries WHERE workspace_id = %s",
                (WSID,),
            )
            conn.commit()

    def _create_current(self, *, narrative="市場摘要草稿", payload=None):
        from backend.app.market.market_doc_store import MarketDocSummaryStore

        return MarketDocSummaryStore().create_summary(
            WSID, payload_json=payload, narrative=narrative, source_document="m.pdf"
        )

    def test_current_returns_null_when_no_summary(self):
        """該 workspace 尚無摘要 → 200 且 summary 為 null（前端據此隱藏市場區）。"""
        resp = client.get(f"{PREFIX}/market-summaries/current", params={"workspace_id": WSID})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["summary"])

    def test_current_returns_unaccepted_draft(self):
        """現行版草稿（未 accept）可由 current 端點取回，供前端逐筆確認顯示。"""
        summary_id = self._create_current(narrative="草稿內容", payload={"items": [{"k": "v"}]})
        resp = client.get(f"{PREFIX}/market-summaries/current", params={"workspace_id": WSID})
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()["summary"]
        self.assertEqual(summary["summary_id"], summary_id)
        self.assertEqual(summary["narrative"], "草稿內容")
        self.assertEqual(summary["status"], "current")
        # 尚未確認：accepted_at 為 None。
        self.assertIsNone(summary["accepted_at"])

    def test_missing_workspace_id_is_422(self):
        """workspace_id 缺漏 → 422（FastAPI 必填 query 驗證）。"""
        resp = client.get(f"{PREFIX}/market-summaries/current")
        self.assertEqual(resp.status_code, 422)

    def test_accepted_only_hides_unaccepted_draft(self):
        """accepted_only=true：未確認草稿回 null（報表／並排只讀已確認現行版，實體隔離）。"""
        from backend.app.market.market_doc_store import MarketDocSummaryStore

        summary_id = self._create_current(narrative="未確認")
        # 未確認：accepted_only 拿不到。
        resp = client.get(
            f"{PREFIX}/market-summaries/current",
            params={"workspace_id": WSID, "accepted_only": "true"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["summary"])
        # 確認後：accepted_only 拿得到。
        MarketDocSummaryStore().accept(summary_id)
        resp2 = client.get(
            f"{PREFIX}/market-summaries/current",
            params={"workspace_id": WSID, "accepted_only": "true"},
        )
        self.assertEqual(resp2.json()["summary"]["summary_id"], summary_id)


class MarketSummaryAcceptTests(unittest.TestCase):
    def setUp(self):
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            conn.execute(
                "DELETE FROM derived_layer.market_doc_summaries WHERE workspace_id = %s",
                (WSID,),
            )
            conn.commit()

    def _create_current(self):
        from backend.app.market.market_doc_store import MarketDocSummaryStore

        return MarketDocSummaryStore().create_summary(
            WSID, payload_json=None, narrative="待確認", source_document="m.pdf"
        )

    def test_accept_marks_accepted_and_visible_to_report(self):
        """確認落款後：accept 回 accepted=True，且 get_accepted_current 才拿得到（報表只讀已確認）。"""
        from backend.app.market.market_doc_store import MarketDocSummaryStore

        summary_id = self._create_current()
        store = MarketDocSummaryStore()
        # 確認前：報表讀不到（未確認草稿實體隔離）。
        self.assertIsNone(store.get_accepted_current(WSID))

        resp = client.post(
            f"{PREFIX}/market-summaries/accept", json={"summary_id": summary_id}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["accepted"])

        # 確認後：報表讀得到已確認現行版。
        accepted = store.get_accepted_current(WSID)
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted["summary_id"], summary_id)

    def test_accept_twice_is_idempotent_no_relock(self):
        """重按 accept：第二次 accepted=False（已確認過不重複落款）。"""
        summary_id = self._create_current()
        first = client.post(f"{PREFIX}/market-summaries/accept", json={"summary_id": summary_id})
        second = client.post(f"{PREFIX}/market-summaries/accept", json={"summary_id": summary_id})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.json()["accepted"])
        self.assertFalse(second.json()["accepted"])

    def test_accept_missing_summary_id_is_422(self):
        """body 缺 summary_id → 422。"""
        resp = client.post(f"{PREFIX}/market-summaries/accept", json={})
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
