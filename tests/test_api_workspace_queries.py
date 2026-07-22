"""查詢端 API 驗收：GET /api/v1/workspaces（清單）與 /workspaces/{id}（詳情）。

0021 對齊：以拋棄式 DB patent_ppt_apiwsqueries（upgrade head）驗證，絕不碰正式庫
patent_ppt。成員專利存 app_layer.workspaces.patent_ids_json（bigint 陣列）；compose
lineage 存 legacy_0021.workspace_compose_sources。清單固定排序 workspace_id DESC
（0021 已無 created_at），清單投影不含 created_at；詳情的 compose_sources 仍帶
created_at（來源自 legacy 表）。每個 test 以 UUID 產生跨執行唯一的 workspace_name，
直接以 SQL 灌拋棄式 workspace（含一般與組合）精確控制 patent_count／is_composed／
compose_sources。覆蓋：分頁與固定排序、status filter 與 total、patent_count／
is_composed、一般詳情、組合直接來源、404、以及 limit/offset/status 非法值 422。
"""
from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from backend.app.main import app


PREFIX = "/api/v1"
TEST_DB = "patent_ppt_apiwsqueries"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

# fixture 專利 id（避開正式資料範圍；本測試只在拋棄式 DB 內灌這些）。
PIDS = [920001, 920002, 920003, 920004, 920005, 920006]

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組出連測試庫的 psycopg 參數（與其他 0021 API 測試同源）。"""
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
    """關閉並清空 lazy 連線池單例，讓 get_pool() 依目前 env 重建（避免綁到別庫）。"""
    from backend.app.db import connection

    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:  # noqa: BLE001
            pass
        connection._pool = None


def _seed_patents():
    """灌可控 core_layer.patents fixture（只需 id 供 patent_ids_json 成員存在）。"""
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        for i, pid in enumerate(PIDS):
            conn.execute(
                "INSERT INTO core_layer.patents (id, title, country_code) VALUES (%s, %s, 'TW')",
                (pid, f"query fixture {i}"),
            )
        conn.commit()


def setUpModule():
    """建拋棄式 DB → upgrade head → 灌 patent fixture；admin 不可用則整組 skip。"""
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"  # Windows localhost 走 IPv6 會慢
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
        pass


class WorkspaceQueriesTests(unittest.TestCase):
    def setUp(self):
        """每個測試以 UUID 建立固定一組拋棄式 workspace，記錄 id 供斷言與清理。"""
        # UUID 前綴：workspace_name 嵌入，跨執行唯一、不撞固定 marker。
        self.run = uuid.uuid4().hex
        self._ws: list[int] = []
        self._seq = 0
        p = PIDS
        # 建立順序即 workspace_id 遞增順序（IDENTITY）；讀取路徑固定排序 workspace_id DESC。
        # 相對 DESC 順序（workspace_id 大→小）：E, D, C, B, A。
        self.ws_a = self._make_ws("A", "active", [p[0], p[1]])                    # 2 件
        self.ws_b = self._make_ws("B", "active", [p[2], p[3], p[4]])              # 3 件
        self.ws_c = self._make_ws("C", "active", [p[0], p[1], p[2], p[3], p[4]])  # 聯集 5 件
        self.ws_d = self._make_ws("D", "archived", [p[0]])                        # 1 件、archived
        self.ws_e = self._make_ws("E", "disabled", [])                           # 0 件、disabled
        # C 為 A、B 的組合：灌 lineage（source_patent_count 用 2 與 3 以驗證對應）。
        self._make_lineage(self.ws_c, [(self.ws_a, 2), (self.ws_b, 3)])

    def tearDown(self):
        """只刪本次執行建立的 workspace 與其 lineage。"""
        if not self._ws:
            return
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            conn.execute(
                "DELETE FROM legacy_0021.workspace_compose_sources "
                "WHERE workspace_id = ANY(%s) OR source_workspace_id = ANY(%s)",
                (self._ws, self._ws),
            )
            conn.execute("DELETE FROM app_layer.workspaces WHERE workspace_id = ANY(%s)", (self._ws,))
            conn.commit()

    # ── 建置輔助 ─────────────────────────────────────────
    def _name(self, tag: str) -> str:
        self._seq += 1
        return f"vq_{self.run}_{tag}_{self._seq}"

    def _make_ws(self, tag: str, status: str, patent_ids: list[int]) -> int:
        """建立一個 workspace（0021 種法：成員存 patent_ids_json bigint 陣列），回傳 workspace_id。"""
        from psycopg.types.json import Jsonb

        with psycopg.connect(**_kw(TEST_DB)) as conn:
            wid = int(
                conn.execute(
                    "INSERT INTO app_layer.workspaces "
                    "(workspace_name, status, patent_ids_json, settings_json) "
                    "VALUES (%s, %s, %s, %s) RETURNING workspace_id",
                    (
                        self._name(tag),
                        status,
                        Jsonb([int(v) for v in patent_ids]),
                        Jsonb({"created_by": f"vq_{self.run}"}),
                    ),
                ).fetchone()[0]
            )
            conn.commit()
        self._ws.append(wid)
        return wid

    def _make_lineage(self, workspace_id: int, sources: list[tuple[int, int]]) -> None:
        """灌 legacy_0021.workspace_compose_sources：(source_workspace_id, source_patent_count) 逐列。"""
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            for source_id, count in sources:
                conn.execute(
                    "INSERT INTO legacy_0021.workspace_compose_sources "
                    "(workspace_id, source_workspace_id, source_patent_count) VALUES (%s, %s, %s)",
                    (workspace_id, source_id, count),
                )
            conn.commit()

    def _list(self, **params):
        return client.get(f"{PREFIX}/workspaces", params=params)

    @staticmethod
    def _ids(body) -> list[int]:
        return [it["workspace_id"] for it in body["items"]]

    # ── 分頁與固定排序 ───────────────────────────────────
    def test_list_pagination_slices_and_order(self):
        """全量清單切片應與分頁一致，且順序全域符合 workspace_id DESC。"""
        full = self._list(limit=200, offset=0).json()
        full_ids = self._ids(full)
        # 分頁 = 全量切片（驗證 offset/limit 與排序穩定）。
        self.assertEqual(self._ids(self._list(limit=3, offset=0).json()), full_ids[0:3])
        if len(full_ids) >= 6:
            self.assertEqual(self._ids(self._list(limit=3, offset=3).json()), full_ids[3:6])
        # 全域單調遞減：workspace_id 非遞增（0021 已無 created_at，固定鍵為 workspace_id）。
        for earlier, later in zip(full_ids, full_ids[1:]):
            self.assertGreaterEqual(earlier, later)
        # 我方五筆的相對順序固定為 E, D, C, B, A（建立即遞增，DESC 反序）。
        mine = [wid for wid in full_ids if wid in self._ws]
        self.assertEqual(mine, [self.ws_e, self.ws_d, self.ws_c, self.ws_b, self.ws_a])
        # limit/offset 原樣回傳。
        self.assertEqual((full["limit"], full["offset"]), (200, 0))
        # 0021 清單投影不含 created_at。
        self.assertNotIn("created_at", full["items"][0])

    # ── status filter 與 total ───────────────────────────
    def test_status_filter_and_total(self):
        """status filter 只回該狀態，且 total 等於 DB 中同狀態總數。"""
        resp = self._list(status="archived", limit=200).json()
        self.assertTrue(all(it["status"] == "archived" for it in resp["items"]))
        self.assertIn(self.ws_d, self._ids(resp))          # 我方 archived 應在內
        self.assertNotIn(self.ws_a, self._ids(resp))       # active 不應洩漏
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            db_total = int(
                conn.execute(
                    "SELECT count(*) FROM app_layer.workspaces WHERE status='archived'"
                ).fetchone()[0]
            )
        self.assertEqual(resp["total"], db_total)
        # 無 filter 的 total 應 >= 有 filter 的 total。
        self.assertGreaterEqual(self._list(limit=1).json()["total"], db_total)

    # ── patent_count 與 is_composed ─────────────────────
    def test_patent_count_and_is_composed_in_list(self):
        """清單中一般 workspace is_composed=False、組合 workspace is_composed=True，件數正確。"""
        items = {it["workspace_id"]: it for it in self._list(limit=200).json()["items"]}
        self.assertEqual(items[self.ws_a]["patent_count"], 2)
        self.assertFalse(items[self.ws_a]["is_composed"])      # A 只是來源，非組合
        self.assertEqual(items[self.ws_b]["patent_count"], 3)
        self.assertFalse(items[self.ws_b]["is_composed"])
        self.assertEqual(items[self.ws_c]["patent_count"], 5)  # 聯集 5
        self.assertTrue(items[self.ws_c]["is_composed"])
        self.assertEqual(items[self.ws_e]["patent_count"], 0)

    # ── 一般 workspace 詳情 ─────────────────────────────
    def test_general_workspace_detail(self):
        """一般 workspace 詳情：欄位齊全、is_composed=False、compose_sources 為空陣列。"""
        body = client.get(f"{PREFIX}/workspaces/{self.ws_a}").json()
        self.assertEqual(body["workspace_id"], self.ws_a)
        self.assertEqual(body["status"], "active")
        self.assertEqual(body["patent_count"], 2)
        self.assertFalse(body["is_composed"])
        self.assertEqual(body["compose_sources"], [])
        # 不回 patent 明細。
        self.assertNotIn("patents", body)

    # ── 組合 workspace 的直接來源 ───────────────────────
    def test_composed_workspace_direct_sources(self):
        """組合 workspace 詳情：is_composed=True，compose_sources 依 source id 排序含兩來源。"""
        body = client.get(f"{PREFIX}/workspaces/{self.ws_c}").json()
        self.assertTrue(body["is_composed"])
        self.assertEqual(body["patent_count"], 5)
        srcs = body["compose_sources"]
        self.assertEqual([s["source_workspace_id"] for s in srcs], sorted([self.ws_a, self.ws_b]))
        by_id = {s["source_workspace_id"]: s for s in srcs}
        self.assertEqual(by_id[self.ws_a]["source_patent_count"], 2)
        self.assertEqual(by_id[self.ws_b]["source_patent_count"], 3)
        self.assertEqual(by_id[self.ws_a]["status"], "active")
        self.assertTrue(by_id[self.ws_a]["workspace_name"].startswith(f"vq_{self.run}"))
        # compose_sources 仍帶 created_at（來源自 legacy_0021 表）。
        self.assertIsNotNone(by_id[self.ws_a]["created_at"])

    # ── 不存在回 404 ────────────────────────────────────
    def test_detail_not_found_404(self):
        """不存在的 workspace_id 回 404。"""
        resp = client.get(f"{PREFIX}/workspaces/999999999")
        self.assertEqual(resp.status_code, 404)

    # ── 非法參數回 422 ──────────────────────────────────
    def test_invalid_query_params_422(self):
        """limit/offset/status 越界或非法值一律 422。"""
        self.assertEqual(self._list(limit=0).status_code, 422)     # ge=1
        self.assertEqual(self._list(limit=201).status_code, 422)   # le=200
        self.assertEqual(self._list(offset=-1).status_code, 422)   # ge=0
        self.assertEqual(self._list(status="bogus").status_code, 422)  # Literal


if __name__ == "__main__":
    unittest.main()
