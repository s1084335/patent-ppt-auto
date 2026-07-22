"""斷點 B regression：clustering candidates/finalize 在 0021 應查 legacy_0021.topic_candidates。

症狀：api/clustering.py 的 GET /clustering/runs/{id}/candidates 與
POST /clustering/runs/{id}/finalize 查 derived_layer.topic_candidates；但 0021 已把
topic_candidates 以 SET SCHEMA 移到 legacy_0021（欄位完全相符，見 0021 migration），
derived_layer 下已無此表，故兩端點對正式庫壞。

本檔用拋棄式 DB patent_ppt_clustering_di（絕不碰正式庫 patent_ppt）＋0021 fixture：
在 legacy_0021.topic_candidates 種候選、derived_layer.topic_runs 種 run，斷言
candidates 端點回候選、finalize 讀得到候選（回 200 建 job）。沿用 test_postgres_topic_repository
的拋棄式 DB 模式。
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
RUN_ID = 941001
CANDIDATE_ID = 942001

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
    os.environ.pop("DATABASE_URL", None)
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


def _seed():
    """種 workspace + workflow_run + derived_layer.topic_run + legacy_0021.topic_candidates 候選。"""
    with psycopg.connect(**_kw(TEST_DB)) as c:
        c.execute("INSERT INTO app_layer.workspaces (workspace_id, workspace_name) "
                  "VALUES (940001, 'clustering_ws')")
        c.execute("INSERT INTO app_layer.workflow_runs (run_id, workspace_id, run_type, status) "
                  "VALUES (943001, 940001, 'clustering:wips_independent_claims', 'succeeded')")
        # candidates 端點的 run 存在檢查讀 derived_layer.topic_runs（0021 仍在 derived_layer）
        c.execute("INSERT INTO derived_layer.topic_runs "
                  "(run_id, workflow_run_id, source_field, topic_state_json) "
                  "VALUES (%s, 943001, %s, '{}'::jsonb)", (RUN_ID, WIPS))
        # legacy_0021.topic_candidates.run_id FK 參照 legacy_0021.topic_runs（隨 0021 SET SCHEMA 一起搬），
        # 故候選所屬 run 需先種進 legacy_0021.topic_runs（其餘欄皆有預設，只需 run_id/source_field）
        c.execute("INSERT INTO legacy_0021.topic_runs (run_id, source_field) "
                  "VALUES (%s, %s)", (RUN_ID, WIPS))
        # 候選在 0021 落點 legacy_0021.topic_candidates（欄位與舊 derived 版相符）
        c.execute(
            """
            INSERT INTO legacy_0021.topic_candidates
                (candidate_id, run_id, candidate_type, candidate_k,
                 coherence, diversity, balance, score, llm_explanation, is_selected)
            VALUES (%s, %s, 'balanced', 5, 0.5, 0.6, 0.7, 0.8, '候選說明', false)
            """,
            (CANDIDATE_ID, RUN_ID),
        )
        c.commit()


def _client():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    return TestClient(app)


class ClusteringCandidatesSchemaTests(unittest.TestCase):
    """candidates/finalize 需命中 legacy_0021.topic_candidates（修前查 derived_layer 會壞）。"""

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


if __name__ == "__main__":
    unittest.main()
