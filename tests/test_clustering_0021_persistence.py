"""clustering runner/service 對 0021 3+3 schema 的落點契約（拋棄式 DB，絕不碰 patent_ppt）。

驗證 0021 併表後分群主流程的讀寫落點一致：
- 候選（calibration candidates）→ derived_layer.topic_runs.topic_state_json->'candidates'。
  不寫 legacy_0021.topic_candidates：該表 run_id FK 指向 legacy_0021.topic_runs（0021 凍結的
  archive，新 run 不會進去），新候選要寫進去必須先在 archive 補一列 12 個 NOT NULL 欄的影子
  topic_run，等於復活 0021 已退役的表；0021 檔頭亦明示 candidates 併入 topic_state_json。
- 正式主題（final topics）→ derived_layer.topic_runs.topic_state_json->'topics'，
  assignments → derived_layer.topic_assignments（(run_id,patent_id) 一列，topic_key=topic_code），
  與已驗收的 PostgresTopicStateRepository 讀取語意一致。
- topic_runs 已無 workspace_id/status：run 歸屬與狀態一律經 workflow_run_id
  JOIN app_layer.workflow_runs 取得；INSERT 必須帶 NOT NULL 的 workflow_run_id。

沿用 test_postgres_topic_repository.py 的拋棄式 DB 模式（建庫 → alembic upgrade head → 種 fixture）。
不跑 BERTopic：直接驗持久化函式，避免把重模型載進 DB 契約測試。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_clustering0021"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

WIPS = "wips_independent_claims"

# fixture ID 區段（與其他測試庫互不重疊）
WS_ID = 930001
WF_CALIBRATE = 931001   # calibrate job 的 workflow_run（＝job_id）
WF_FINALIZE = 931002    # finalize job 的 workflow_run
TOPIC_RUN = 932001
PATENT_IDS = (930101, 930102, 930103)

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組出連測試庫的 psycopg 參數（與 test_postgres_topic_repository 同源）。"""
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
    """建拋棄式 DB → upgrade head → 種 workspace/workflow_runs/patents；admin 不可用則 skip。"""
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"  # Windows localhost 走 IPv6 會慢（見 migration contract）
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB  # 受測程式走 get_connection_kwargs() 需連測試庫

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
    """種 workspace、兩筆 workflow_runs（calibrate/finalize job）與三筆專利。"""
    with psycopg.connect(**_kw(TEST_DB)) as c:
        for pid in PATENT_IDS:
            c.execute("INSERT INTO core_layer.patents (id, title) VALUES (%s, 'clustering0021 fixture')", (pid,))
        c.execute("INSERT INTO app_layer.workspaces (workspace_id, workspace_name) VALUES (%s, %s)",
                  (WS_ID, "clustering0021_ws"))
        for run_id, run_type in (
            (WF_CALIBRATE, "clustering_calibrate"),
            (WF_FINALIZE, "clustering_finalize"),
        ):
            c.execute(
                "INSERT INTO app_layer.workflow_runs (run_id, workspace_id, run_type, status) "
                "VALUES (%s, %s, %s, 'running')",
                (run_id, WS_ID, run_type),
            )
        c.commit()


def _reset_topic_rows():
    """每個測試前清掉分群寫入，讓 calibrate/finalize 可重跑。"""
    with psycopg.connect(**_kw(TEST_DB)) as c:
        c.execute("DELETE FROM derived_layer.topic_assignments")
        c.execute("DELETE FROM derived_layer.topic_runs")
        c.commit()


class CreateTopicRunTests(unittest.TestCase):
    """建立 topic run：必須帶 workflow_run_id，且不得再寫 workspace_id/status。"""

    def setUp(self):
        _reset_topic_rows()

    def test_create_run_persists_with_workflow_run_id(self):
        from backend.app.clustering.runner import create_topic_run

        run_id = create_topic_run(
            workflow_run_id=WF_CALIBRATE,
            source_field=WIPS,
            state={"run_mode": "full", "input_doc_count": 3},
        )
        with psycopg.connect(**_kw(TEST_DB), row_factory=dict_row) as c:
            row = c.execute(
                "SELECT tr.run_id, tr.workflow_run_id, tr.source_field, tr.topic_state_json, "
                "       wr.workspace_id, wr.status "
                "FROM derived_layer.topic_runs tr "
                "JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id "
                "WHERE tr.run_id = %s",
                (run_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["workflow_run_id"], WF_CALIBRATE)
        # workspace_id/status 只能由 workflow_runs JOIN 取得
        self.assertEqual(row["workspace_id"], WS_ID)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["topic_state_json"]["input_doc_count"], 3)

    def test_run_scope_reads_workspace_and_status_via_join(self):
        from backend.app.clustering.runner import create_topic_run, load_run_scope

        run_id = create_topic_run(workflow_run_id=WF_CALIBRATE, source_field=WIPS, state={})
        scope = load_run_scope(run_id)
        self.assertEqual(scope["workspace_id"], WS_ID)
        self.assertEqual(scope["source_field"], WIPS)
        self.assertEqual(scope["status"], "running")


class CalibrationCandidateTests(unittest.TestCase):
    """候選落點：寫入 topic_state_json->'candidates'，讀回與 API endpoint 同源。"""

    def setUp(self):
        _reset_topic_rows()
        from backend.app.clustering.runner import create_topic_run

        self.run_id = create_topic_run(
            workflow_run_id=WF_CALIBRATE, source_field=WIPS, state={"input_doc_count": 3})

    def _candidates(self):
        from backend.app.clustering.runner import CandidateProfile, KScanResult

        return [
            CandidateProfile(
                candidate_type=ctype,
                result=KScanResult(
                    k=k, coherence=0.5 + k / 100, diversity=0.6, balance=0.7,
                    score=0.8 + k / 100, small_topic_ratio=0.1, topic_count=k,
                    elapsed_seconds=1.0, references=[],
                ),
            )
            for ctype, k in (("conservative", 10), ("balanced", 20), ("detailed", 30))
        ]

    def test_persist_calibration_writes_candidates_to_topic_state(self):
        from backend.app.clustering.runner import _persist_calibration

        persisted = _persist_calibration(
            run_id=self.run_id, scan_results=[c.result for c in self._candidates()],
            candidates=self._candidates(),
        )
        self.assertEqual(len(persisted), 3)
        with psycopg.connect(**_kw(TEST_DB), row_factory=dict_row) as c:
            row = c.execute(
                "SELECT topic_state_json FROM derived_layer.topic_runs WHERE run_id = %s",
                (self.run_id,),
            ).fetchone()
        rows = row["topic_state_json"]["candidates"]
        self.assertEqual([r["candidate_type"] for r in rows],
                         ["conservative", "balanced", "detailed"])
        self.assertEqual([r["candidate_k"] for r in rows], [10, 20, 30])
        # candidate_id 在 run 內穩定且唯一，供 finalize 指定
        self.assertEqual([r["candidate_id"] for r in rows], [1, 2, 3])

    def test_candidates_readable_by_api_endpoint(self):
        """寫入端與 backend/app/api/clustering.py 讀取端必須同源。"""
        from backend.app.api.clustering import get_candidates
        from backend.app.clustering.runner import _persist_calibration

        _persist_calibration(
            run_id=self.run_id, scan_results=[c.result for c in self._candidates()],
            candidates=self._candidates(),
        )
        payload = get_candidates(self.run_id)
        self.assertEqual(payload["run_id"], self.run_id)
        self.assertEqual(payload["workspace_id"], WS_ID)     # 經 workflow_runs JOIN
        self.assertEqual(payload["status"], "succeeded")     # calibrate 完成後的 job 狀態
        self.assertEqual([c["candidate_k"] for c in payload["candidates"]], [10, 20, 30])

    def test_load_run_and_candidate_reads_back(self):
        from backend.app.clustering.runner import _load_run_and_candidate, _persist_calibration

        persisted = _persist_calibration(
            run_id=self.run_id, scan_results=[c.result for c in self._candidates()],
            candidates=self._candidates(),
        )
        candidate_id = persisted[1]["candidate_id"]
        run_row, candidate_row = _load_run_and_candidate(
            run_id=self.run_id, candidate_id=candidate_id)
        self.assertEqual(run_row["workspace_id"], WS_ID)     # JOIN 而來
        self.assertEqual(run_row["source_field"], WIPS)
        self.assertEqual(candidate_row["candidate_k"], 20)


class FinalTopicPersistenceTests(unittest.TestCase):
    """正式主題落點：topics 進 topic_state_json、assignments 進 derived_layer.topic_assignments。"""

    def setUp(self):
        _reset_topic_rows()
        from backend.app.clustering.runner import create_topic_run

        self.run_id = create_topic_run(
            workflow_run_id=WF_FINALIZE, source_field=WIPS, state={"input_doc_count": 3})

    def _persist(self):
        from backend.app.clustering.runner import persist_final_topics

        topics = [
            {"topic_code": "T001", "model_topic_ids": [0], "topic_kind": "model",
             "doc_count": 2, "label": "鋸切結構", "label_source": "fallback",
             "status": "active", "display_order": 1, "keywords": [{"term": "鋸切", "weight": 1.0}],
             "representative_patent_ids": [PATENT_IDS[0]]},
            {"topic_code": "T002", "model_topic_ids": [1], "topic_kind": "model",
             "doc_count": 1, "label": "進給機構", "label_source": "fallback",
             "status": "active", "display_order": 2, "keywords": [{"term": "進給", "weight": 1.0}],
             "representative_patent_ids": [PATENT_IDS[2]]},
            {"topic_code": "UNCLASSIFIED", "topic_kind": "unclassified", "doc_count": 0,
             "label": "未分類", "label_source": "fallback", "status": "active",
             "display_order": 3},
        ]
        assignments = [
            (PATENT_IDS[0], "T001", 0.1),
            (PATENT_IDS[1], "T001", 0.2),
            (PATENT_IDS[2], "T002", 0.3),
        ]
        return persist_final_topics(
            run_id=self.run_id, topics=topics, assignments=assignments,
            metrics={"score": 0.9}, artifact_key="ws/930001/run.pkl",
        )

    def test_topics_land_in_topic_state_json(self):
        topic_count, assignment_count = self._persist()
        self.assertEqual((topic_count, assignment_count), (3, 3))
        with psycopg.connect(**_kw(TEST_DB), row_factory=dict_row) as c:
            row = c.execute(
                "SELECT topic_state_json, artifact_key FROM derived_layer.topic_runs WHERE run_id = %s",
                (self.run_id,),
            ).fetchone()
        codes = [t["topic_code"] for t in row["topic_state_json"]["topics"]]
        self.assertEqual(codes, ["T001", "T002", "UNCLASSIFIED"])
        self.assertEqual(row["artifact_key"], "ws/930001/run.pkl")

    def test_assignments_land_in_derived_topic_assignments(self):
        self._persist()
        with psycopg.connect(**_kw(TEST_DB), row_factory=dict_row) as c:
            rows = c.execute(
                "SELECT patent_id, topic_key FROM derived_layer.topic_assignments "
                "WHERE run_id = %s ORDER BY patent_id",
                (self.run_id,),
            ).fetchall()
        self.assertEqual([(r["patent_id"], r["topic_key"]) for r in rows],
                         [(PATENT_IDS[0], "T001"), (PATENT_IDS[1], "T001"), (PATENT_IDS[2], "T002")])

    def test_topic_state_repository_reads_same_data(self):
        """與已驗收的 PostgresTopicStateRepository 語意一致：同一份資料同一個落點。"""
        from backend.app.repositories.topic_state_repository import PostgresTopicStateRepository

        self._persist()
        state = PostgresTopicStateRepository().get_latest_topic_state(WS_ID, WIPS)
        self.assertEqual(state["run_id"], self.run_id)
        by_code = {t["topic_code"]: t for t in state["topics"]}
        self.assertEqual(set(by_code), {"T001", "T002", "UNCLASSIFIED"})
        self.assertEqual(by_code["T001"]["patent_ids"], [PATENT_IDS[0], PATENT_IDS[1]])
        self.assertEqual(by_code["T002"]["patent_ids"], [PATENT_IDS[2]])
        self.assertEqual(by_code["T001"]["label"], "鋸切結構")


if __name__ == "__main__":
    unittest.main()
