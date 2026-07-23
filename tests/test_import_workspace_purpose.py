"""完整匯入機制輪1：匯入圈 workspace（C）與用途標籤（D）契約。

2026-07-22 定案：匯入帶 workspace（新建成員＝這次匯入專利、既有 union 去重）與用途標籤
（general／case_comparison，落 workspace settings_json，供專利總覽過濾/顯示）。

- DB 整合部分用拋棄式 DB patent_ppt_impws（upgrade head），絕不碰正式庫 patent_ppt。
- import_wips_file 回 patent_ids：以受控 CSV 匯入拋棄式 DB（search_path 讓裸表名可用）驗證。
- handler 圈 workspace／purpose：以拋棄式 DB 灌 patent 後，mock import_wips_file 回可控
  patent_ids，走 handler 真實建/併 workspace，讀 app_layer.workspaces.patent_ids_json 與
  settings_json.purpose 驗證。
- API 參數：mock create_job，斷言 payload 帶 workspace_id/new_workspace_name/purpose。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.types.json import Jsonb

TEST_DB = "patent_ppt_impws"
HEAD_REV = "head"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 拋棄式 DB 內可控 patent id（避開正式資料範圍）。
PIDS = [940001, 940002, 940003, 940004, 940005]
_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.environ["PGPASSWORD"]
    return kw


def _rw(dbname: str) -> dict:
    kw = _kw(dbname)
    kw["options"] = "-c search_path=raw_layer,core_layer,public"
    return kw


def _reset_pool():
    """關閉 lazy 連線池，讓 app_layer 依目前 env 重連拋棄式 DB。"""
    from backend.app.db import connection
    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:  # noqa: BLE001
            pass
        connection._pool = None


def setUpModule():
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"
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
    command.upgrade(cfg, HEAD_REV)
    with psycopg.connect(**_kw(TEST_DB)) as c:
        for i, pid in enumerate(PIDS):
            c.execute("INSERT INTO core_layer.patents (id, title, country_code) VALUES (%s, %s, 'TW')",
                      (pid, f"ws fixture {i}"))
        c.commit()


def tearDownModule():
    _reset_pool()
    for k, v in _prev_env.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    except Exception:  # noqa: BLE001
        pass


def _members(workspace_id: int) -> list[int]:
    with psycopg.connect(**_kw(TEST_DB)) as c:
        row = c.execute(
            "SELECT patent_ids_json FROM app_layer.workspaces WHERE workspace_id=%s",
            (workspace_id,)).fetchone()
    return sorted(int(v) for v in (row[0] or []))


def _purpose(workspace_id: int):
    with psycopg.connect(**_kw(TEST_DB)) as c:
        row = c.execute(
            "SELECT settings_json->>'purpose' FROM app_layer.workspaces WHERE workspace_id=%s",
            (workspace_id,)).fetchone()
    return row[0]


class ImportReturnsPatentIdsTests(unittest.TestCase):
    """import_wips_file 回傳這次涉及的 patent_ids（新建＋命中既有，去重）。"""

    def test_import_returns_touched_patent_ids(self):
        from backend.app.importers.wips_importer import import_wips_file
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ws.csv"
            # 兩列不同申請號 → 兩筆新建；第三列與第一列同申請號 → 命中既有、去重後不重覆。
            p.write_text(
                "申请号,标题,申请日\n"
                "TWWS001,甲,2020-01-01\n"
                "TWWS002,乙,2020-02-02\n"
                "TWWS001,甲改,2020-01-01\n",
                encoding="utf-8")
            # importer 裸表名需 search_path；用 DATABASE_URL 指到拋棄式 DB 的 search_path 版。
            summary = import_wips_file(p, dry_run=False)
        self.assertEqual(summary["status"], "imported")
        self.assertIn("patent_ids", summary)
        # 去重：三列但只兩個相異專利。
        self.assertEqual(len(summary["patent_ids"]), 2)
        self.assertEqual(len(set(summary["patent_ids"])), 2)
        self.assertTrue(all(isinstance(v, int) for v in summary["patent_ids"]))
        # 清掉本測資料，避免污染後續（同 DB 拋棄式，但保持乾淨）。
        with psycopg.connect(**_kw(TEST_DB)) as c:
            c.execute("DELETE FROM core_layer.patents WHERE id = ANY(%s)", (summary["patent_ids"],))
            c.commit()

    # importer 用 get_connection_kwargs()；模組層已把 PGDATABASE 指到拋棄式 DB，
    # 且未設 DATABASE_URL，故預設 search_path=core_layer,raw_layer,public（importer 裸表名可用）。


class ImportHandlerWorkspaceTests(unittest.TestCase):
    """patent_import handler 接受 workspace 參數：新建（成員＝匯入專利）或既有（union 去重）。"""

    def _run_handler(self, payload_extra, patent_ids):
        from backend.app.worker import handlers
        summary = {"status": "imported", "records": len(patent_ids),
                   "inserted": len(patent_ids), "matched_existing": 0, "updated": 0,
                   "skipped": 0, "patent_ids": list(patent_ids)}
        base = {"blob_id": 1, "original_filename": "x.csv", "file_hash": "h"}
        base.update(payload_extra)
        # 繞過來源檔取回與驗證（本測聚焦圈 workspace 行為）：mock import_wips_file 回可控
        # summary，並讓 blob 取回／刪除變成 no-op（2026-07-23 起來源檔改由 DB blob 取得）。
        with mock.patch.object(handlers, "import_wips_file", return_value=summary), \
             mock.patch.object(handlers.import_blob_store, "write_blob_to_path"), \
             mock.patch.object(handlers.import_blob_store, "delete_blob"):
            return handlers.handle_patent_import(base, mock.MagicMock())

    def test_new_workspace_members_are_imported_patents(self):
        result = self._run_handler(
            {"new_workspace_name": self._uniq("圈-new")}, [PIDS[0], PIDS[1], PIDS[2]])
        wid = result["workspace_id"]
        self._ws.append(wid)
        self.assertEqual(_members(wid), sorted([PIDS[0], PIDS[1], PIDS[2]]))

    def test_existing_workspace_union_dedup(self):
        # 先建一個既有 workspace（成員 1,2）。
        from backend.app.app_layer.workspace_create import create_workspace
        created = create_workspace(
            workspace_name=self._uniq("圈-exist"), patent_ids=[PIDS[0], PIDS[1]])
        wid = created["workspace_id"]
        self._ws.append(wid)
        # 匯入帶入 2,3,4（2 重疊）→ union 去重成 1,2,3,4。
        result = self._run_handler({"workspace_id": wid}, [PIDS[1], PIDS[2], PIDS[3]])
        self.assertEqual(result["workspace_id"], wid)
        self.assertEqual(_members(wid), sorted([PIDS[0], PIDS[1], PIDS[2], PIDS[3]]))

    def test_no_workspace_param_skips_workspace(self):
        result = self._run_handler({}, [PIDS[0]])
        self.assertIsNone(result.get("workspace_id"))

    def test_purpose_stored_on_new_workspace(self):
        result = self._run_handler(
            {"new_workspace_name": self._uniq("圈-purpose"), "purpose": "case_comparison"},
            [PIDS[0], PIDS[4]])
        wid = result["workspace_id"]
        self._ws.append(wid)
        self.assertEqual(_purpose(wid), "case_comparison")

    def test_purpose_defaults_general(self):
        result = self._run_handler(
            {"new_workspace_name": self._uniq("圈-default")}, [PIDS[0]])
        wid = result["workspace_id"]
        self._ws.append(wid)
        self.assertEqual(_purpose(wid), "general")

    # ── fixtures ─────────────────────────────────────────
    _seq = 0

    def setUp(self):
        self._ws: list[int] = []

    def _uniq(self, base: str) -> str:
        type(self)._seq += 1
        return f"{base}_{type(self)._seq}"

    def tearDown(self):
        if not self._ws:
            return
        with psycopg.connect(**_kw(TEST_DB)) as c:
            c.execute("DELETE FROM app_layer.workspaces WHERE workspace_id = ANY(%s)", (self._ws,))
            c.commit()


class PurposeQueryableTests(unittest.TestCase):
    """用途標籤要能被專利總覽查詢用來過濾/顯示：list_workspaces 投影 purpose 並支援 purpose filter。"""

    def setUp(self):
        from backend.app.app_layer.workspace_create import create_workspace
        self._ws: list[int] = []
        self.general_ws = create_workspace(
            workspace_name=self._uniq("q-general"), patent_ids=[PIDS[0]], purpose="general"
        )["workspace_id"]
        self.case_ws = create_workspace(
            workspace_name=self._uniq("q-case"), patent_ids=[PIDS[1]], purpose="case_comparison"
        )["workspace_id"]
        self._ws += [self.general_ws, self.case_ws]

    _seq = 0

    def _uniq(self, base: str) -> str:
        type(self)._seq += 1
        return f"{base}_{type(self)._seq}"

    def tearDown(self):
        if not self._ws:
            return
        with psycopg.connect(**_kw(TEST_DB)) as c:
            c.execute("DELETE FROM app_layer.workspaces WHERE workspace_id = ANY(%s)", (self._ws,))
            c.commit()

    def _by_id(self, items):
        return {it["workspace_id"]: it for it in items}

    def test_list_projects_purpose(self):
        from backend.app.app_layer import workspace_queries
        items = self._by_id(workspace_queries.list_workspaces(limit=200)["items"])
        self.assertEqual(items[self.general_ws]["purpose"], "general")
        self.assertEqual(items[self.case_ws]["purpose"], "case_comparison")

    def test_filter_by_purpose_case_comparison(self):
        from backend.app.app_layer import workspace_queries
        result = workspace_queries.list_workspaces(limit=200, purpose="case_comparison")
        ids = {it["workspace_id"] for it in result["items"]}
        self.assertIn(self.case_ws, ids)
        self.assertNotIn(self.general_ws, ids)

    def test_filter_general_includes_legacy_without_purpose_key(self):
        """purpose='general' 應含舊 workspace（settings_json 無 purpose 鍵）。"""
        from backend.app.app_layer import workspace_queries
        with psycopg.connect(**_kw(TEST_DB)) as c:
            legacy = int(c.execute(
                "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json, settings_json) "
                "VALUES (%s, %s, %s) RETURNING workspace_id",
                (self._uniq("q-legacy"), Jsonb([PIDS[2]]), Jsonb({"created_by": "x"})),
            ).fetchone()[0])
            c.commit()
        self._ws.append(legacy)
        result = workspace_queries.list_workspaces(limit=200, purpose="general")
        ids = {it["workspace_id"] for it in result["items"]}
        self.assertIn(legacy, ids, "無 purpose 鍵的舊 workspace 應被 general 命中")
        self.assertNotIn(self.case_ws, ids)


class ImportApiWorkspacePurposeTests(unittest.TestCase):
    """POST /api/v1/imports 收 workspace（workspace_id 既有 or new_workspace_name 新建）與 purpose。"""

    def setUp(self):
        from fastapi.testclient import TestClient
        from backend.app.main import app
        self.client = TestClient(app)
        # 上傳內容走拋棄式 DB 的 app_layer.import_blobs（真實路徑，不 mock）。

    def tearDown(self):
        # 本測只驗 payload 參數，blob 不交給 worker；清掉避免累積在拋棄式 DB。
        with psycopg.connect(**_kw(TEST_DB)) as c:
            c.execute("DELETE FROM app_layer.import_blobs")
            c.commit()

    def _post(self, params, content=b"col\nv\n"):
        from types import SimpleNamespace
        from backend.app.api import imports as imports_api
        captured = {}

        def fake_create_job(job_type, payload, *, workspace_id=None, **kw):
            captured.update(job_type=job_type, payload=payload, workspace_id=workspace_id)
            return SimpleNamespace(
                job_id=1, job_type=job_type, status="queued", workspace_id=workspace_id,
                payload_json=payload, result_json=None, progress_percent=0,
                current_stage=None, attempt_count=0, max_attempts=3, error_message=None)

        with mock.patch.object(imports_api.job_repository, "create_job", side_effect=fake_create_job):
            resp = self.client.post("/api/v1/imports", params=params, content=content)
        return resp, captured

    def test_new_workspace_name_and_purpose_flow_into_payload(self):
        resp, captured = self._post(
            {"filename": "a.csv", "new_workspace_name": "案件比對批", "purpose": "case_comparison"})
        self.assertEqual(resp.status_code, 200)
        payload = captured["payload"]
        self.assertEqual(payload.get("new_workspace_name"), "案件比對批")
        self.assertEqual(payload.get("purpose"), "case_comparison")

    def test_existing_workspace_id_flows_into_payload(self):
        resp, captured = self._post({"filename": "b.csv", "workspace_id": 777})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["payload"].get("workspace_id"), 777)

    def test_default_purpose_general_when_omitted(self):
        resp, captured = self._post({"filename": "c.csv"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["payload"].get("purpose"), "general")

    def test_reject_both_workspace_id_and_new_name(self):
        resp, _ = self._post(
            {"filename": "d.csv", "workspace_id": 1, "new_workspace_name": "x"})
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
