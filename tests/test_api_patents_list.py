"""驗收：GET /api/v1/patents（全庫專利分頁清單，供專利總覽跨 workspace 顯示）。

拋棄式 DB patent_ppt_apipatlist（upgrade head），絕不碰正式庫 patent_ppt。
模組層自建可控 core_layer.patents ＋ app_layer.workspaces fixture：一筆專利同時屬於
兩個 workspace、一筆只屬一個、一筆不屬任何 workspace，驗 workspaces 歸屬標示。
覆蓋：分頁（limit/offset/total）、欄位形狀、workspaces 歸屬陣列、
不屬任何 workspace 者回空陣列、limit 上限（>200 → 422、<1 → 422）、
以及 N+1 防護（歸屬映射一次批次查完，查詢次數不隨專利筆數成長）。
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from backend.app.main import app


PREFIX = "/api/v1"
TEST_DB = "patent_ppt_apipatlist"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

GRANT_COL = "授權公告號"

# fixture 專利 id / workspace id（避開正式資料範圍）。
PIDS = [930001, 930002, 930003]
WSIDS = [930101, 930102]

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


def _seed():
    """灌 fixture：三筆專利、兩個 workspace。

    ws A 含 PIDS[0], PIDS[1]；ws B 含 PIDS[0]。PIDS[2] 不屬任何 workspace。
    """
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        for pid, title, num in (
            (PIDS[0], "shared patent", "US93000001B2"),
            (PIDS[1], "ws-a only", "US93000002B2"),
            (PIDS[2], "orphan patent", "US93000003B2"),
        ):
            conn.execute(
                f'INSERT INTO core_layer.patents (id, title, country_code, "{GRANT_COL}") '
                "VALUES (%s, %s, 'US', %s)",
                (pid, title, num),
            )
        conn.execute(
            "INSERT INTO derived_layer.report_patent_base (patent_id, applicant_display_name) "
            "VALUES (%s, %s)",
            (PIDS[0], "REXON INDUSTRIAL"),
        )
        conn.execute(
            "INSERT INTO app_layer.workspaces (workspace_id, workspace_name, status, patent_ids_json) "
            "VALUES (%s, %s, 'active', %s::jsonb)",
            (WSIDS[0], "WS A", json.dumps([PIDS[0], PIDS[1]])),
        )
        conn.execute(
            "INSERT INTO app_layer.workspaces (workspace_id, workspace_name, status, patent_ids_json) "
            "VALUES (%s, %s, 'active', %s::jsonb)",
            (WSIDS[1], "WS B", json.dumps([PIDS[0]])),
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
    _seed()


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


def _items_by_id(items: list[dict]) -> dict[int, dict]:
    return {it["patent_id"]: it for it in items}


class PatentListTests(unittest.TestCase):

    def _list(self, **params):
        return client.get(f"{PREFIX}/patents", params=params)

    def test_lists_all_patents_paginated(self):
        """不帶 q 即回全庫專利，含 total/limit/offset 分頁欄。"""
        resp = self._list(limit=200, offset=0)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("items", "total", "limit", "offset"):
            self.assertIn(key, body)
        ids = {it["patent_id"] for it in body["items"]}
        self.assertTrue(set(PIDS).issubset(ids))
        self.assertGreaterEqual(body["total"], len(PIDS))

    def test_item_shape_includes_workspaces(self):
        """每筆含專利欄位＋workspaces（所屬 workspace 陣列，含 id 與名稱）＋has_figure。

        has_figure 為布林旗標（0026 起）：清單只回「有無代表圖」，圖片內容不進清單回應，
        由前端逐筆走 GET /patents/{id}/figure 惰性載入。

        2026-07-24 起回應另含 2026-07-23 定案的顯示欄位（見 test_api_patents_display_fields），
        故本測試改驗「必含這組基本欄」而非精確等於——顯示欄位增減由該檔負責，
        兩邊不重複維護同一份欄位清單。
        """
        resp = self._list(limit=200)
        self.assertEqual(resp.status_code, 200)
        item = _items_by_id(resp.json()["items"])[PIDS[0]]
        self.assertLessEqual(
            {
                "patent_id",
                "patent_number",
                "title",
                "country_code",
                "has_figure",
                "applicant_display_name",
                "workspaces",
            },
            set(item.keys()),
        )
        # 本測試 fixture 未寫入代表圖，故旗標為 False（不得回 bytea 內容）。
        self.assertIs(item["has_figure"], False)
        self.assertEqual(item["patent_number"], "US93000001B2")
        self.assertEqual(item["applicant_display_name"], "REXON INDUSTRIAL")

    def test_workspace_membership_multi(self):
        """同屬兩個 workspace 的專利，workspaces 兩筆皆列出（id 與 name）。"""
        resp = self._list(limit=200)
        item = _items_by_id(resp.json()["items"])[PIDS[0]]
        got = {(w["workspace_id"], w["workspace_name"]) for w in item["workspaces"]}
        self.assertEqual(got, {(WSIDS[0], "WS A"), (WSIDS[1], "WS B")})

    def test_workspace_membership_single_and_none(self):
        """只屬一個 workspace 者列一筆；不屬任何 workspace 者回空陣列（不是 null）。"""
        by_id = _items_by_id(self._list(limit=200).json()["items"])
        self.assertEqual(
            [w["workspace_id"] for w in by_id[PIDS[1]]["workspaces"]], [WSIDS[0]]
        )
        self.assertEqual(by_id[PIDS[2]]["workspaces"], [])

    def test_keyword_filter_optional(self):
        """keyword 可選：帶 keyword 時對專利號／名稱／申請人過濾，不帶則不過濾。"""
        resp = self._list(keyword="orphan", limit=200)
        self.assertEqual(resp.status_code, 200)
        ids = {it["patent_id"] for it in resp.json()["items"]}
        self.assertIn(PIDS[2], ids)
        self.assertNotIn(PIDS[1], ids)

    def test_pagination_applied(self):
        """limit/offset 生效：limit=1 只回一筆，total 仍為全量。"""
        resp = self._list(limit=1, offset=0)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["items"]), 1)
        self.assertGreaterEqual(body["total"], len(PIDS))

    def test_limit_bounds(self):
        """limit 超上限（>200）或 <1 皆 422（防全表一次撈）。"""
        self.assertEqual(self._list(limit=0).status_code, 422)
        self.assertEqual(self._list(limit=201).status_code, 422)
        self.assertEqual(self._list(offset=-1).status_code, 422)

    def test_membership_map_is_batched_not_n_plus_1(self):
        """N+1 防護：workspace 歸屬映射一次批次查完，查詢次數不隨專利筆數成長。

        對 limit=1（一筆）與 limit=200（多筆）各統計 SQL 執行次數，兩者必須相同——
        若逐筆反查 workspace，多筆時次數會變多。
        """
        from backend.app.app_layer import patent_queries

        counts: list[int] = []
        for limit in (1, 200):
            executed: list[str] = []
            orig = psycopg.Cursor.execute

            def spy(self, query, *args, **kwargs):
                executed.append(str(query))
                return orig(self, query, *args, **kwargs)

            psycopg.Cursor.execute = spy
            try:
                patent_queries.list_patents(limit=limit, offset=0)
            finally:
                psycopg.Cursor.execute = orig
            counts.append(len(executed))
        self.assertEqual(counts[0], counts[1], f"查詢次數隨筆數成長（疑似 N+1）：{counts}")


if __name__ == "__main__":
    unittest.main()
