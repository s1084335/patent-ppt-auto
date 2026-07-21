"""PostgresTopicRepository 契約（拋棄式 DB patent_ppt_topicrepo，絕不碰 patent_ppt）。

沿用 test_workflow_repositories.py 的拋棄式 DB 模式：建庫 → alembic upgrade head →
種 3+3 fixture（app_layer.workspaces/workflow_runs、derived_layer.topic_runs/topic_assignments），
涵蓋六方法 happy path、404/409 邊界、request_key 冪等，以及 rename 後 TopicState 讀得到新 label。

另含一組 API 整合測試：FastAPI dependency_overrides 換上真 adapter，六 endpoints
對同一拋棄式 DB 走通（不動 test_api_topics_contract.py）。

讀取面驗證重點：queue_merge/queue_unmerge 只 enqueue 一筆 queued workflow_run，不執行實際合併，
因此主題狀態在整組測試間維持穩定，各測試以自建 run_id 過濾歷史，避免相互汙染。
"""
from __future__ import annotations

import os
import unittest

import psycopg
from psycopg.types.json import Jsonb
from alembic import command
from alembic.config import Config

from pathlib import Path

TEST_DB = "patent_ppt_topicrepo"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組出連測試庫的 psycopg 參數（與 test_workflow_repositories 同源）。"""
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


def setUpModule():
    """建拋棄式 DB → upgrade head → 種 3+3 fixture；admin 不可用則整組 skip。"""
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"  # 見 migration contract：Windows localhost 走 IPv6 會慢
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB  # repository 走 get_connection_kwargs() 需連測試庫

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    _seed()


def tearDownModule():
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


def _seed():
    """以 3+3 目標 schema 種資料：讀取 ws(920001)、隔離 ws(920002)、可變動 ws(920003)。"""
    # 讀取 ws 最新 wips run：T01 改名後標籤、T02 active、U00 未分類；舊 run 不得外洩
    state_read_new = {"topics": [
        {"topic_id": 923101, "topic_code": "T01", "label": "鋸切結構", "status": "active",
         "topic_kind": "model", "label_source": "model", "doc_count": 2},
        {"topic_id": 923102, "topic_code": "T02", "label": "進給機構", "status": "active",
         "topic_kind": "model", "label_source": "model", "doc_count": 1},
        {"topic_id": 923103, "topic_code": "U00", "label": "未分類", "status": "active",
         "topic_kind": "unclassified", "doc_count": 0},
    ], "candidates": [{"candidate_id": 1, "candidate_type": "balanced", "candidate_k": 5}]}
    state_read_old = {"topics": [{"topic_id": 923100, "topic_code": "T01", "label": "舊標籤",
                                  "status": "active", "topic_kind": "model", "doc_count": 1}]}
    state_read_effect = {"topics": [{"topic_id": 923104, "topic_code": "E01", "label": "省力效果",
                                     "status": "active", "topic_kind": "model", "doc_count": 1}]}
    state_other = {"topics": [{"topic_id": 923105, "topic_code": "X01", "label": "別家主題",
                               "status": "active", "topic_kind": "model", "doc_count": 1}]}
    # 可變動 ws：M01/M02 兩 active 主題供 merge/unmerge/rename，MU0 未分類
    state_mutate = {"topics": [
        {"topic_id": 923106, "topic_code": "M01", "label": "合併源A", "status": "active",
         "topic_kind": "model", "label_source": "model", "doc_count": 1},
        {"topic_id": 923107, "topic_code": "M02", "label": "合併源B", "status": "active",
         "topic_kind": "model", "label_source": "model", "doc_count": 1},
        {"topic_id": 923108, "topic_code": "MU0", "label": "未分類", "status": "active",
         "topic_kind": "unclassified", "doc_count": 0},
    ]}
    with psycopg.connect(**_kw(TEST_DB)) as c:
        for pid in range(920001, 920007):
            c.execute("INSERT INTO core_layer.patents (id, title) VALUES (%s, 'topicrepo fixture')", (pid,))
        for ws, name in ((920001, "read_ws"), (920002, "other_ws"), (920003, "mutate_ws")):
            c.execute("INSERT INTO app_layer.workspaces (workspace_id, workspace_name) VALUES (%s, %s)",
                      (ws, name))
        for run_id, ws, rt in (
            (921001, 920001, "clustering:wips_independent_claims"),  # 舊 run
            (921002, 920001, "clustering:wips_independent_claims"),  # 最新 run
            (921003, 920001, "clustering:effect_summary"),
            (921004, 920002, "clustering:wips_independent_claims"),
            (921005, 920003, "clustering:wips_independent_claims"),
        ):
            c.execute("INSERT INTO app_layer.workflow_runs (run_id, workspace_id, run_type, status) "
                      "VALUES (%s, %s, %s, 'succeeded')", (run_id, ws, rt))
        for run_id, wf, sf, state in (
            (922001, 921001, "wips_independent_claims", state_read_old),
            (922002, 921002, "wips_independent_claims", state_read_new),
            (922003, 921003, "effect_summary", state_read_effect),
            (922004, 921004, "wips_independent_claims", state_other),
            (922005, 921005, "wips_independent_claims", state_mutate),
        ):
            c.execute("INSERT INTO derived_layer.topic_runs (run_id, workflow_run_id, source_field, topic_state_json) "
                      "VALUES (%s, %s, %s, %s)", (run_id, wf, sf, Jsonb(state)))
        for run_id, pid, key in (
            (922001, 920001, "T01"),   # 舊 run：不得回傳
            (922002, 920001, "T01"),
            (922002, 920002, "T01"),
            (922002, 920003, "T02"),
            (922003, 920001, "E01"),
            (922004, 920004, "X01"),
            (922005, 920005, "M01"),
            (922005, 920006, "M02"),
        ):
            c.execute("INSERT INTO derived_layer.topic_assignments (run_id, patent_id, topic_key) "
                      "VALUES (%s, %s, %s)", (run_id, pid, key))
        c.commit()


def _scalar(sql: str, params=()):
    with psycopg.connect(**_kw(TEST_DB)) as c:
        row = c.execute(sql, params).fetchone()
    return row[0] if row else None


WIPS = "wips_independent_claims"


def _repo():
    from backend.app.repositories.postgres_topic_repository import PostgresTopicRepository
    return PostgresTopicRepository()


class ListReadTests(unittest.TestCase):
    """讀取面：list_topics / list_merge_suggestions（合併/改名後 active＋未分類，候選不外洩）。"""

    def test_list_topics_latest_run_only(self):
        result = _repo().list_topics(920001, WIPS)
        self.assertEqual(result["run_id"], 922002)  # 最新 run，非 922001
        self.assertEqual(result["workspace_id"], 920001)
        by_key = {t["topic_key"]: t for t in result["topics"]}
        self.assertEqual(set(by_key), {"T01", "T02", "U00"})
        self.assertEqual(by_key["T01"]["label"], "鋸切結構")
        self.assertEqual(by_key["T01"]["doc_count"], 2)  # 併回後實際 assignment 數
        self.assertEqual(by_key["T01"]["status"], "active")

    def test_list_topics_workspace_not_found(self):
        from backend.app.repositories.topic_repository import WorkspaceNotFoundError
        with self.assertRaises(WorkspaceNotFoundError):
            _repo().list_topics(999999, WIPS)

    def test_list_topics_workspace_without_run_returns_empty(self):
        # ws 存在但該通道無 run（other_ws 只有 wips，這裡查 effect）→ 空清單而非 404
        result = _repo().list_topics(920002, "effect_summary")
        self.assertEqual(result["topics"], [])

    def test_list_merge_suggestions_empty_but_workspace_checked(self):
        result = _repo().list_merge_suggestions(920001, WIPS)
        self.assertEqual(result["workspace_id"], 920001)
        self.assertEqual(result["suggestions"], [])

    def test_list_merge_suggestions_workspace_not_found(self):
        from backend.app.repositories.topic_repository import WorkspaceNotFoundError
        with self.assertRaises(WorkspaceNotFoundError):
            _repo().list_merge_suggestions(999999, WIPS)


class MergeTests(unittest.TestCase):
    """queue_merge：驗證兩不同 active key、enqueue queued run、request_key 冪等。"""

    def test_queue_merge_happy(self):
        r = _repo().queue_merge(920003, WIPS, ["M01", "M02"], None, "web-user", None)
        self.assertEqual(r["operation"], "topic_merge")
        self.assertEqual(r["status"], "queued")
        self.assertEqual(r["workspace_id"], 920003)
        self.assertEqual(_scalar(
            "SELECT run_type FROM app_layer.workflow_runs WHERE run_id=%s", (r["run_id"],)),
            "topic_merge")

    def test_queue_merge_duplicate_keys_invalid(self):
        from backend.app.repositories.topic_repository import InvalidTopicOperationError
        with self.assertRaises(InvalidTopicOperationError):
            _repo().queue_merge(920003, WIPS, ["M01", "M01"], None, "web-user", None)

    def test_queue_merge_nonactive_key_invalid(self):
        from backend.app.repositories.topic_repository import InvalidTopicOperationError
        with self.assertRaises(InvalidTopicOperationError):
            _repo().queue_merge(920003, WIPS, ["M01", "NOPE"], None, "web-user", None)

    def test_queue_merge_request_key_idempotent(self):
        rk = "merge-idem-key-001"
        r1 = _repo().queue_merge(920003, WIPS, ["M01", "M02"], None, "web-user", rk)
        r2 = _repo().queue_merge(920003, WIPS, ["M01", "M02"], None, "web-user", rk)
        self.assertEqual(r1["run_id"], r2["run_id"])
        self.assertEqual(_scalar(
            "SELECT count(*) FROM app_layer.workflow_runs WHERE request_key=%s", (rk,)), 1)


class UnmergeAndHistoryTests(unittest.TestCase):
    """queue_unmerge + list_merge_history：以自建 merge run 過濾，避免測試間汙染。"""

    def test_unmerge_happy_and_history_can_unmerge_flips(self):
        repo = _repo()
        merged = repo.queue_merge(920003, WIPS, ["M01", "M02"], "合併後主題", "web-user",
                                  "history-flow-001")
        run_id = merged["run_id"]
        # 尚未 unmerge：歷史顯示 can_unmerge=True
        hist = {h["merge_run_id"]: h for h in repo.list_merge_history(920003, WIPS)}
        self.assertIn(run_id, hist)
        self.assertEqual(hist[run_id]["source_topics"], ["M01", "M02"])
        self.assertTrue(hist[run_id]["can_unmerge"])
        # unmerge 後：queued topic_unmerge run，且歷史 can_unmerge 轉 False
        u = repo.queue_unmerge(920003, WIPS, run_id, "web-user", "unmerge-flow-001")
        self.assertEqual(u["operation"], "topic_unmerge")
        self.assertEqual(u["status"], "queued")
        hist2 = {h["merge_run_id"]: h for h in repo.list_merge_history(920003, WIPS)}
        self.assertFalse(hist2[run_id]["can_unmerge"])

    def test_unmerge_unknown_merge_run_invalid(self):
        from backend.app.repositories.topic_repository import InvalidTopicOperationError
        with self.assertRaises(InvalidTopicOperationError):
            _repo().queue_unmerge(920003, WIPS, 987654, "web-user", None)


class RenameTests(unittest.TestCase):
    """rename_topic：強制 label_source=manual，且 TopicState 讀得到新 label。"""

    def test_rename_persists_and_state_repo_reads_new_label(self):
        from backend.app.repositories.topic_state_repository import PostgresTopicStateRepository
        r = _repo().rename_topic(920003, "M01", "人工命名主題", "web-user")
        self.assertEqual(r["label_source"], "manual")
        self.assertEqual(r["label"], "人工命名主題")
        state = PostgresTopicStateRepository().get_latest_topic_state(920003, WIPS)
        by_code = {t["topic_code"]: t for t in state["topics"]}
        self.assertEqual(by_code["M01"]["label"], "人工命名主題")

    def test_rename_unknown_topic_not_found(self):
        from backend.app.repositories.topic_repository import TopicNotFoundError
        with self.assertRaises(TopicNotFoundError):
            _repo().rename_topic(920003, "ZZZ", "新名", "web-user")

    def test_rename_empty_label_invalid(self):
        from backend.app.repositories.topic_repository import InvalidTopicOperationError
        with self.assertRaises(InvalidTopicOperationError):
            _repo().rename_topic(920003, "M02", "   ", "web-user")


class ApiIntegrationTests(unittest.TestCase):
    """FastAPI dependency_overrides 換真 adapter，六 endpoints 對拋棄式 DB 走通。"""

    def setUp(self):
        # 避免 TestClient 匯入時的 Starlette deprecation warning 汙染 -W error
        import warnings
        from starlette.exceptions import StarletteDeprecationWarning
        warnings.filterwarnings(
            "ignore",
            message="Using `httpx` with `starlette.testclient` is deprecated",
            category=StarletteDeprecationWarning,
            module=r"fastapi\.testclient",
        )
        from fastapi.testclient import TestClient
        from backend.app.main import app
        from backend.app.api.topics import get_topic_repository
        from backend.app.repositories.postgres_topic_repository import PostgresTopicRepository
        self.app = app
        self.client = TestClient(app)
        app.dependency_overrides[get_topic_repository] = lambda: PostgresTopicRepository()

    def tearDown(self):
        self.app.dependency_overrides.clear()

    def test_six_endpoints_against_real_db(self):
        base = f"/api/v1/workspaces/920001/topics"
        # 1. GET topics
        r = self.client.get(f"{base}?source_field={WIPS}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual({t["topic_key"] for t in r.json()["topics"]}, {"T01", "T02", "U00"})
        # 2. GET merge-suggestions
        r = self.client.get(f"{base}/merge-suggestions?source_field={WIPS}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["suggestions"], [])
        # 3. POST merge（用 mutate_ws）
        mbase = f"/api/v1/workspaces/920003/topics"
        r = self.client.post(f"{mbase}/merge", json={
            "source_field": WIPS, "topic_keys": ["M01", "M02"], "label": None,
            "requested_by": "web-user", "request_key": "api-merge-001"})
        self.assertEqual(r.status_code, 202)
        merge_run_id = r.json()["run_id"]
        self.assertEqual(r.json()["operation"], "topic_merge")
        # 4. GET merge-history
        r = self.client.get(f"{mbase}/merge-history?source_field={WIPS}")
        self.assertEqual(r.status_code, 200)
        self.assertIn(merge_run_id, [h["merge_run_id"] for h in r.json()])
        # 5. POST unmerge
        r = self.client.post(f"{mbase}/unmerge", json={
            "source_field": WIPS, "merge_run_id": merge_run_id,
            "requested_by": "web-user", "request_key": "api-unmerge-001"})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()["operation"], "topic_unmerge")
        # 6. PATCH rename
        r = self.client.patch(f"{mbase}/M02", json={"label": "API 改名", "renamed_by": "web-user"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["label_source"], "manual")
        self.assertEqual(r.json()["label"], "API 改名")

    def test_api_workspace_not_found_404(self):
        r = self.client.get(f"/api/v1/workspaces/888888/topics?source_field={WIPS}")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
