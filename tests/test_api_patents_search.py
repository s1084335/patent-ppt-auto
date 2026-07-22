"""驗收：GET /api/v1/patents/search（既有庫專利號搜尋，供案件比對選被比對專利）。

拋棄式 DB patent_ppt_apipatsearch（upgrade head），絕不碰正式庫 patent_ppt。
模組層自建可控 core_layer.patents fixture：涵蓋六欄專利號其中數欄、一筆
report_patent_base 申請人，供號命中/片段/limit/空結果與 applicant 顯示斷言。
覆蓋：精確號命中、片段 ILIKE、limit 上限（>200 → 422、<1 → 422）、
查無回空、回傳欄位形狀（patent_id/patent_number/title/country_code/applicant_display_name）。
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
TEST_DB = "patent_ppt_apipatsearch"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

# 六欄專利號其中三欄（授權公告號 / 未審查的公開號 / 申請號），驗六欄 COALESCE 通用不綁單一欄。
GRANT_COL = "授權公告號"
PUB_COL = "未審查的公開號"
APP_COL = "申請號"

# fixture 專利 id（避開正式資料範圍）。
PIDS = [920001, 920002, 920003, 920004]

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


def _seed_patents():
    """灌 fixture：三筆各用不同號欄，第四筆無任何號（驗 COALESCE 後 patent_number 可能為 NULL）。"""
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        conn.execute(
            f'INSERT INTO core_layer.patents (id, title, country_code, "{GRANT_COL}") '
            "VALUES (%s, %s, 'US', %s)",
            (PIDS[0], "controller widget", "US12345678B2"),
        )
        conn.execute(
            f'INSERT INTO core_layer.patents (id, title, country_code, "{PUB_COL}") '
            "VALUES (%s, %s, 'TW', %s)",
            (PIDS[1], "motor assembly", "TW202099999A"),
        )
        conn.execute(
            f'INSERT INTO core_layer.patents (id, title, country_code, "{APP_COL}") '
            "VALUES (%s, %s, 'US', %s)",
            (PIDS[2], "sensor module", "US99900011"),
        )
        conn.execute(
            "INSERT INTO core_layer.patents (id, title, country_code) VALUES (%s, %s, 'JP')",
            (PIDS[3], "no number patent"),
        )
        conn.execute(
            "INSERT INTO derived_layer.report_patent_base (patent_id, applicant_display_name) "
            "VALUES (%s, %s)",
            (PIDS[0], "REXON INDUSTRIAL"),
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
    _seed_patents()


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


class PatentSearchTests(unittest.TestCase):

    def _search(self, **params):
        return client.get(f"{PREFIX}/patents/search", params=params)

    def test_exact_number_hit(self):
        """精確授權公告號命中對應專利。"""
        resp = self._search(q="US12345678B2")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        ids = [it["patent_id"] for it in items]
        self.assertIn(PIDS[0], ids)

    def test_partial_fragment_hit(self):
        """片段（ILIKE）命中：以號的一部分找到專利。"""
        resp = self._search(q="202099")
        self.assertEqual(resp.status_code, 200)
        ids = [it["patent_id"] for it in resp.json()["items"]]
        self.assertIn(PIDS[1], ids)

    def test_application_number_column_hit(self):
        """六欄 COALESCE 通用：申請號欄的號也能被搜到（不綁單一號欄）。"""
        resp = self._search(q="99900011")
        self.assertEqual(resp.status_code, 200)
        ids = [it["patent_id"] for it in resp.json()["items"]]
        self.assertIn(PIDS[2], ids)

    def test_item_shape(self):
        """回傳每筆含 patent_id/patent_number/title/country_code/applicant_display_name。"""
        resp = self._search(q="US12345678B2")
        self.assertEqual(resp.status_code, 200)
        item = next(it for it in resp.json()["items"] if it["patent_id"] == PIDS[0])
        self.assertEqual(
            set(item.keys()),
            {"patent_id", "patent_number", "title", "country_code", "applicant_display_name"},
        )
        self.assertEqual(item["patent_number"], "US12345678B2")
        self.assertEqual(item["applicant_display_name"], "REXON INDUSTRIAL")

    def test_empty_result(self):
        """查無回空清單。"""
        resp = self._search(q="zzz_no_such_number_xyz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"], [])

    def test_limit_cap_and_bounds(self):
        """limit 超上限（>200）或 <1 皆 422（防全表掃）。"""
        self.assertEqual(self._search(q="US", limit=0).status_code, 422)
        self.assertEqual(self._search(q="US", limit=201).status_code, 422)

    def test_limit_applied(self):
        """limit 生效：limit=1 時最多回一筆。"""
        resp = self._search(q="US", limit=1)
        self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(len(resp.json()["items"]), 1)

    def test_missing_q_returns_422(self):
        """缺 q → 422。"""
        self.assertEqual(client.get(f"{PREFIX}/patents/search").status_code, 422)


if __name__ == "__main__":
    unittest.main()
