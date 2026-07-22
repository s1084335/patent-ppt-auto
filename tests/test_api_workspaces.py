"""段 2 驗收：POST /api/v1/workspaces/compose 與 compose 服務。

0021 對齊：以拋棄式 DB patent_ppt_apiwscompose（upgrade head）驗證，絕不碰正式庫
patent_ppt。來源 workspace 以 0021 種法直接灌（成員存 patent_ids_json bigint 陣列，
不再有 workspace_patents 明細表、不經 clustering.create_workspace）；compose lineage 寫
legacy_0021.workspace_compose_sources。覆蓋：兩來源、三來源、重複來源 ID、重複專利去重、
不存在來源、非 active 來源、rollback、來源不變、新 ws 可接續建 calibrate job。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from backend.app.main import app
from backend.app.app_layer import workspace_compose


PREFIX = "/api/v1"
TEST_DB = "patent_ppt_apiwscompose"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)
CREATED_BY = "_v_compose"

# fixture 專利 id（避開正式資料範圍；本測試只在拋棄式 DB 內灌這些）。
PIDS = [930001, 930002, 930003, 930004, 930005, 930006]

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
    """關閉並清空 lazy 連線池單例，讓 get_pool() 依目前 env 重建（避免綁到別庫）。"""
    from backend.app.db import connection

    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:  # noqa: BLE001
            pass
        connection._pool = None


def _seed_patents():
    """灌可控 core_layer.patents fixture（只需 id 供 patent_ids_json 成員存在與 calibrate 語料）。"""
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        for i, pid in enumerate(PIDS):
            conn.execute(
                "INSERT INTO core_layer.patents (id, title, country_code) VALUES (%s, %s, 'TW')",
                (pid, f"compose fixture {i}"),
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


class WorkspaceComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pids = PIDS

    _seq = 0

    def setUp(self):
        self._ws: list[int] = []

    def _uniq(self, base: str) -> str:
        type(self)._seq += 1
        return f"{base}_{type(self)._seq}"

    def tearDown(self):
        if not self._ws:
            return
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            conn.execute(
                "DELETE FROM legacy_0021.workspace_compose_sources "
                "WHERE workspace_id = ANY(%s) OR source_workspace_id = ANY(%s)",
                (self._ws, self._ws),
            )
            # 0021：佇列在 app_layer.workflow_runs（calibrate job 由此建立）。
            conn.execute(
                "DELETE FROM app_layer.workflow_runs WHERE workspace_id = ANY(%s)",
                (self._ws,),
            )
            conn.execute("DELETE FROM app_layer.workspaces WHERE workspace_id = ANY(%s)", (self._ws,))
            conn.commit()

    def _make_source(self, patent_ids: list[int]) -> int:
        """0021 種法：直接灌一個來源 workspace，成員存 patent_ids_json（去重 bigint 陣列）。"""
        unique = list(dict.fromkeys(int(v) for v in patent_ids))
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            wid = int(
                conn.execute(
                    "INSERT INTO app_layer.workspaces "
                    "(workspace_name, patent_ids_json, settings_json) "
                    "VALUES (%s, %s, %s) RETURNING workspace_id",
                    (self._uniq("_v_src"), Jsonb(unique), Jsonb({"created_by": CREATED_BY})),
                ).fetchone()[0]
            )
            conn.commit()
        self._ws.append(wid)
        return wid

    def _patent_count(self, workspace_id: int) -> int:
        """0021：成員件數＝patent_ids_json 陣列長度。"""
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            return int(
                conn.execute(
                    "SELECT jsonb_array_length(patent_ids_json) "
                    "FROM app_layer.workspaces WHERE workspace_id = %s",
                    (workspace_id,),
                ).fetchone()[0]
            )

    def _compose(self, source_ids: list[int], name: str | None = None):
        resp = client.post(
            f"{PREFIX}/workspaces/compose",
            json={
                "workspace_name": name or self._uniq("_v_composed"),
                "source_workspace_ids": source_ids,
                "created_by": CREATED_BY,
            },
        )
        if resp.status_code == 200:
            self._ws.append(resp.json()["workspace_id"])
        return resp

    # ── 聯集/去重 ─────────────────────────────────────────
    def test_two_sources_union_and_dedup(self):
        a = self._make_source([self.pids[0], self.pids[1], self.pids[2]])  # 1,2,3
        b = self._make_source([self.pids[2], self.pids[3], self.pids[4]])  # 3,4,5（3 重疊）
        resp = self._compose([a, b])
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["union_count"], 5)       # {1,2,3,4,5}
        self.assertEqual(body["duplicate_count"], 1)   # (3+3)-5
        self.assertEqual(
            {s["source_workspace_id"]: s["patent_count"] for s in body["source_counts"]},
            {a: 3, b: 3},
        )
        # 新 ws 實際成員數 = 聯集數
        self.assertEqual(self._patent_count(body["workspace_id"]), 5)
        # 來源完全不動
        self.assertEqual(self._patent_count(a), 3)
        self.assertEqual(self._patent_count(b), 3)
        # lineage 兩列
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            n = conn.execute(
                "SELECT count(*) FROM legacy_0021.workspace_compose_sources WHERE workspace_id=%s",
                (body["workspace_id"],),
            ).fetchone()[0]
        self.assertEqual(n, 2)

    def test_three_sources(self):
        a = self._make_source([self.pids[0], self.pids[1], self.pids[2]])  # 1,2,3
        b = self._make_source([self.pids[2], self.pids[3], self.pids[4]])  # 3,4,5
        c = self._make_source([self.pids[4], self.pids[5]])                # 5,6
        body = self._compose([a, b, c]).json()
        self.assertEqual(body["union_count"], 6)       # {1..6}
        self.assertEqual(body["duplicate_count"], 2)   # (3+3+2)-6

    def test_duplicate_source_ids_deduped(self):
        a = self._make_source([self.pids[0], self.pids[1], self.pids[2]])
        b = self._make_source([self.pids[2], self.pids[3], self.pids[4]])
        body = self._compose([a, a, b]).json()  # 重複來源 ID
        self.assertEqual(body["union_count"], 5)
        self.assertEqual(len(body["source_counts"]), 2)  # 去重成 2 個來源
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            n = conn.execute(
                "SELECT count(*) FROM legacy_0021.workspace_compose_sources WHERE workspace_id=%s",
                (body["workspace_id"],),
            ).fetchone()[0]
        self.assertEqual(n, 2)

    def test_empty_source_union_boundary(self):
        """空來源不貢獻成員：一空一非空聯集＝非空來源件數；兩空聯集為 0。"""
        a = self._make_source([])                                # 0 件
        b = self._make_source([self.pids[0], self.pids[1]])      # 2 件
        body = self._compose([a, b]).json()
        self.assertEqual(body["union_count"], 2)
        self.assertEqual(self._patent_count(body["workspace_id"]), 2)
        c = self._make_source([])
        d = self._make_source([])
        body2 = self._compose([c, d]).json()
        self.assertEqual(body2["union_count"], 0)
        self.assertEqual(self._patent_count(body2["workspace_id"]), 0)

    # ── 驗證錯誤 ─────────────────────────────────────────
    def test_missing_source_404(self):
        a = self._make_source([self.pids[0], self.pids[1]])
        resp = self._compose([a, 9_999_999])
        self.assertEqual(resp.status_code, 404)

    def test_inactive_source_409(self):
        a = self._make_source([self.pids[0], self.pids[1]])
        b = self._make_source([self.pids[2], self.pids[3]])
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            conn.execute(
                "UPDATE app_layer.workspaces SET status='archived' WHERE workspace_id=%s", (b,)
            )
            conn.commit()
        resp = self._compose([a, b])
        self.assertEqual(resp.status_code, 409)

    def test_less_than_two_distinct_422(self):
        a = self._make_source([self.pids[0], self.pids[1]])
        # 兩個相同來源 → 去重後 1 個 → 422（pydantic 過關但服務層擋）
        resp = self._compose([a, a])
        self.assertEqual(resp.status_code, 422)

    def test_single_source_422_by_pydantic(self):
        a = self._make_source([self.pids[0]])
        resp = client.post(
            f"{PREFIX}/workspaces/compose",
            json={"workspace_name": "_v_one", "source_workspace_ids": [a]},
        )
        self.assertEqual(resp.status_code, 422)  # min_length=2

    def test_duplicate_workspace_name_409(self):
        a = self._make_source([self.pids[0], self.pids[1]])
        b = self._make_source([self.pids[2], self.pids[3]])
        # 用一個已存在 workspace 的名稱去 compose → workspace_name 唯一衝突 → 409
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            existing_name = conn.execute(
                "SELECT workspace_name FROM app_layer.workspaces WHERE workspace_id=%s", (a,)
            ).fetchone()[0]
        resp = self._compose([a, b], name=existing_name)
        self.assertEqual(resp.status_code, 409)

    # ── 單一 transaction rollback ─────────────────────────
    def test_rollback_leaves_no_partial_workspace(self):
        a = self._make_source([self.pids[0], self.pids[1]])
        b = self._make_source([self.pids[2], self.pids[3]])
        with mock.patch.object(workspace_compose, "_insert_lineage", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                workspace_compose.compose_workspaces(
                    workspace_name="_v_rollback", source_workspace_ids=[a, b], created_by=CREATED_BY
                )
        # 名稱為 _v_rollback 的 workspace 不應存在（workspace insert 已 rollback）
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            n = conn.execute(
                "SELECT count(*) FROM app_layer.workspaces WHERE workspace_name='_v_rollback'"
            ).fetchone()[0]
        self.assertEqual(n, 0)

    # ── 新 ws 可接續建分群 job ────────────────────────────
    def test_composed_ws_can_create_calibrate_job(self):
        a = self._make_source([self.pids[0], self.pids[1], self.pids[2]])
        b = self._make_source([self.pids[2], self.pids[3], self.pids[4]])
        new_ws = self._compose([a, b]).json()["workspace_id"]
        resp = client.post(
            f"{PREFIX}/workspaces/{new_ws}/clustering/calibrate",
            json={"source_field": "wips_independent_claims"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_type"], "clustering_calibrate")
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["workspace_id"], new_ws)


if __name__ == "__main__":
    unittest.main()
