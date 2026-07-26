"""斷點 B regression：clustering candidates/finalize 在 0021 應讀 topic_state_json->'candidates'。

症狀：api/clustering.py 的 GET /clustering/runs/{id}/candidates 與
POST /clustering/runs/{id}/finalize 原本查 derived_layer.topic_candidates；0021 已移除該表。

落點修正（2026-07-23）：候選改讀 derived_layer.topic_runs.topic_state_json->'candidates'，
不走 legacy_0021.topic_candidates——該表 run_id FK 參照 legacy_0021.topic_runs 這個
0021 凍結的 archive，新 run 不在其中；要寫入必須先在 archive 補一列影子 topic_run，
等於復活已退役的表，且寫入端（runner._persist_calibration）不會這麼做，讀寫將不同源。
0021 migration 檔頭亦明示 topics/candidates/assignments 併入 topic_state_json。

本檔用拋棄式 DB patent_ppt_clustering_di（絕不碰正式庫 patent_ppt）＋0021 fixture：
在 topic_state_json 種候選，斷言 candidates 端點回候選、finalize 讀得到候選（回 200 建 job）。
沿用 test_postgres_topic_repository 的拋棄式 DB 模式。
"""
from __future__ import annotations

import os
import unittest
import warnings

import psycopg
from alembic import command
from alembic.config import Config

from pathlib import Path

from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=StarletteDeprecationWarning,
    module=r"fastapi\.testclient",
)

TEST_DB = "patent_ppt_clustering_di"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
WIPS = "wips_independent_claims"
EFFECT = "effect_summary"
RUN_ID = 941001
CANDIDATE_ID = 942001
# 同 workspace＋同通道的較新 run（驗「最新 run 解析」不會回舊的 RUN_ID）
NEWER_RUN_ID = 941002
NEWER_CANDIDATE_ID = 942002
# 另一通道的 run（驗 source_field 有真的過濾，不會跨通道拿錯）
EFFECT_RUN_ID = 941003
EFFECT_CANDIDATE_ID = 942003
WORKSPACE_ID = 940001
EMPTY_WORKSPACE_ID = 940002
# 已定案（completed）的 run：重複按「採用」應被 API 擋成 409，不得再建 job
# 讓 worker 拋 ValueError("topic run cannot be finalized from status=completed")。
# 種在專屬 workspace，避免搶走既有「最新 run 解析」測試的 run。
COMPLETED_RUN_ID = 941004
COMPLETED_CANDIDATE_ID = 942004
COMPLETED_WORKSPACE_ID = 940003

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組出連測試庫的 psycopg 參數。"""
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


def _test_database_url() -> str:
    """組出指向拋棄式測試庫的 DATABASE_URL（與 _kw 同一組連線參數）。"""
    kw = _kw(TEST_DB)
    auth = kw["user"]
    if kw.get("password"):
        auth = f"{auth}:{kw['password']}"
    return f"postgresql://{auth}@{kw['host']}:{kw['port']}/{TEST_DB}"


def setUpModule():
    """建拋棄式 DB → upgrade head → 種 0021 fixture；admin 不可用則整組 skip。"""
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    # 安全關鍵：不能只 pop DATABASE_URL。backend.app.clustering.runner 於 import 時
    # load_dotenv(專案根/.env, override=False)，會把 .env 內的正式庫（Supabase）
    # DATABASE_URL 灌回環境，測試將打到正式庫。明確設成測試庫，override=False 便無法蓋掉。
    os.environ["DATABASE_URL"] = _test_database_url()
    os.environ["PGDATABASE"] = TEST_DB

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


def _state(candidate_id: int, run_id: int, k: int, status: str = "needs_review") -> str:
    """組 topic_state_json：候選落 'candidates'，欄名沿用舊 topic_candidates。"""
    import json

    return json.dumps(
        {
            "status": status,
            "candidates": [
                {"candidate_id": candidate_id, "run_id": run_id, "candidate_type": "balanced",
                 "candidate_k": k, "coherence": 0.5, "diversity": 0.6, "balance": 0.7,
                 "score": 0.8, "llm_explanation": "候選說明", "is_selected": False},
            ],
        },
        ensure_ascii=False,
    )


def _seed():
    """種 workspace + workflow_run + derived_layer.topic_run（候選在 topic_state_json）。

    種三個 run：同 workspace 同通道的舊／新兩個（驗最新解析）、另一通道一個
    （驗 source_field 過濾），外加一個沒有 run 的空 workspace（驗 404）。
    """
    with psycopg.connect(**_kw(TEST_DB)) as c:
        c.execute("INSERT INTO app_layer.workspaces (workspace_id, workspace_name) "
                  "VALUES (%s, 'clustering_ws'), (%s, 'clustering_ws_empty'), "
                  "       (%s, 'clustering_ws_completed')",
                  (WORKSPACE_ID, EMPTY_WORKSPACE_ID, COMPLETED_WORKSPACE_ID))
        c.execute("INSERT INTO app_layer.workflow_runs (run_id, workspace_id, run_type, status) "
                  "VALUES (943001, %s, 'clustering:wips_independent_claims', 'succeeded'), "
                  "       (943002, %s, 'clustering:wips_independent_claims', 'succeeded'), "
                  "       (943003, %s, 'clustering:effect_summary', 'succeeded'), "
                  "       (943004, %s, 'clustering:effect_summary', 'succeeded')",
                  (WORKSPACE_ID, WORKSPACE_ID, WORKSPACE_ID, COMPLETED_WORKSPACE_ID))
        c.execute("INSERT INTO derived_layer.topic_runs "
                  "(run_id, workflow_run_id, source_field, topic_state_json) "
                  "VALUES (%s, 943001, %s, %s::jsonb), "
                  "       (%s, 943002, %s, %s::jsonb), "
                  "       (%s, 943003, %s, %s::jsonb), "
                  "       (%s, 943004, %s, %s::jsonb)",
                  (RUN_ID, WIPS, _state(CANDIDATE_ID, RUN_ID, 5),
                   NEWER_RUN_ID, WIPS, _state(NEWER_CANDIDATE_ID, NEWER_RUN_ID, 8),
                   EFFECT_RUN_ID, EFFECT, _state(EFFECT_CANDIDATE_ID, EFFECT_RUN_ID, 3),
                   COMPLETED_RUN_ID, EFFECT,
                   _state(COMPLETED_CANDIDATE_ID, COMPLETED_RUN_ID, 4, status="completed")))
        c.commit()


def _client():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    return TestClient(app)


class ClusteringCandidatesSchemaTests(unittest.TestCase):
    """candidates/finalize 需命中 topic_state_json->'candidates'（修前查 derived_layer 會壞）。"""

    def test_get_candidates_returns_seeded_candidate(self):
        r = _client().get(f"/api/v1/clustering/runs/{RUN_ID}/candidates")
        self.assertEqual(r.status_code, 200, r.text)  # 修前查錯 schema → 500/壞
        body = r.json()
        self.assertEqual(body["run_id"], RUN_ID)
        ids = [c["candidate_id"] for c in body["candidates"]]
        self.assertIn(CANDIDATE_ID, ids)
        cand = next(c for c in body["candidates"] if c["candidate_id"] == CANDIDATE_ID)
        self.assertEqual(cand["candidate_k"], 5)
        self.assertEqual(cand["llm_explanation"], "候選說明")

    def test_finalize_finds_candidate_and_creates_job(self):
        # 驗查詢命中即可：finalize 讀得到候選 → 建 clustering_finalize job 回 200
        r = _client().post(
            f"/api/v1/clustering/runs/{RUN_ID}/finalize",
            json={"candidate_id": CANDIDATE_ID, "selected_by": "tester"},
        )
        self.assertEqual(r.status_code, 200, r.text)  # 修前候選查錯 schema → 422/壞
        body = r.json()
        self.assertEqual(body["job_type"], "clustering_finalize")
        self.assertEqual(body["payload"]["run_id"], RUN_ID)
        self.assertEqual(body["payload"]["candidate_id"], CANDIDATE_ID)

    def test_finalize_unknown_candidate_422(self):
        # 候選查詢命中 schema 後，不存在的 candidate 仍應正確回 422（非 schema 錯）
        r = _client().post(
            f"/api/v1/clustering/runs/{RUN_ID}/finalize",
            json={"candidate_id": -1, "selected_by": "tester"},
        )
        self.assertEqual(r.status_code, 422, r.text)

    def test_finalize_already_completed_run_409(self):
        """已定案的 run 再按「採用」應在 API 端擋成 409，不得再建 job。

        修前：API 只檢查候選存在就建 job，worker 才在
        `_load_run_and_candidate` 拋 ValueError("...from status=completed")，
        使用者只看到「clustering_finalize failed」而不知原因。
        """
        r = _client().post(
            f"/api/v1/clustering/runs/{COMPLETED_RUN_ID}/finalize",
            json={"candidate_id": COMPLETED_CANDIDATE_ID, "selected_by": "tester"},
        )
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("completed", r.json()["detail"])


class LatestCandidatesByWorkspaceTests(unittest.TestCase):
    """GET /clustering/candidates?workspace_id=&source_field=：後端自解析最新 run。

    前端原本要拉全域 /tasks?limit=100 再自己過濾找 run_id；/tasks 不分 workspace，
    多 workspace 併用時舊 workspace 會被擠出視窗，前端誤報「尚未跑過分群」。
    此端點讓後端直接以 workspace_id + source_field 解析最新 run。
    """

    def test_resolves_latest_run_for_workspace_and_source(self):
        r = _client().get(
            "/api/v1/clustering/candidates",
            params={"workspace_id": WORKSPACE_ID, "source_field": WIPS},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        # 同 workspace 同通道有兩個 run，必須回較新的那個（不得回 RUN_ID）
        self.assertEqual(body["run_id"], NEWER_RUN_ID)
        self.assertEqual(body["workspace_id"], WORKSPACE_ID)
        self.assertEqual(body["source_field"], WIPS)
        ids = [c["candidate_id"] for c in body["candidates"]]
        self.assertEqual(ids, [NEWER_CANDIDATE_ID])

    def test_source_field_filters_channel(self):
        # 通道要真的過濾：功效通道只能拿到功效 run，不得拿到技術通道的最新 run
        r = _client().get(
            "/api/v1/clustering/candidates",
            params={"workspace_id": WORKSPACE_ID, "source_field": EFFECT},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["run_id"], EFFECT_RUN_ID)
        self.assertEqual([c["candidate_id"] for c in body["candidates"]], [EFFECT_CANDIDATE_ID])

    def test_workspace_without_run_returns_404(self):
        # 真的沒跑過分群 → 404，讓前端能與「查詢失敗」區分
        r = _client().get(
            "/api/v1/clustering/candidates",
            params={"workspace_id": EMPTY_WORKSPACE_ID, "source_field": WIPS},
        )
        self.assertEqual(r.status_code, 404, r.text)

    def test_unknown_source_field_422(self):
        # source_field 白名單沿用既有驗證
        r = _client().get(
            "/api/v1/clustering/candidates",
            params={"workspace_id": WORKSPACE_ID, "source_field": "not_a_channel"},
        )
        self.assertEqual(r.status_code, 422, r.text)

    def test_existing_run_id_endpoint_contract_unchanged(self):
        # 既有 by-run_id 契約不得被新端點破壞（路由順序／型別都要仍可用）
        r = _client().get(f"/api/v1/clustering/runs/{RUN_ID}/candidates")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["run_id"], RUN_ID)
        self.assertEqual([c["candidate_id"] for c in body["candidates"]], [CANDIDATE_ID])
        r404 = _client().get("/api/v1/clustering/runs/99999999/candidates")
        self.assertEqual(r404.status_code, 404, r404.text)


if __name__ == "__main__":
    unittest.main()
