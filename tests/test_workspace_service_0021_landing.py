"""workspace_service.py 對 0021 3+3 schema 的落點契約（拋棄式 DB，絕不碰 patent_ppt）。

本檔驗 workspace_service 遷移到 0021 後的讀寫落點，與 runner.py／
PostgresTopicStateRepository 同源：

- workspace 成員 → app_layer.workspaces.patent_ids_json（app_layer.workspace_patents 已刪）。
- 候選 → derived_layer.topic_runs.topic_state_json->'candidates'
  （derived_layer.topic_candidates 已刪）。
- 正式主題 → topic_state_json->'topics'；指派 → derived_layer.topic_assignments
  （(run_id,patent_id) 一列、topic_key=topic_code）；derived_layer.topics 已刪。
- run 歸屬／狀態 → JOIN app_layer.workflow_runs（topic_runs 已無 workspace_id/status）。
- 併發安全 → 無列鎖可用，改以 append-only 新版本（新 topic_run，previous_run_id 指前一版）。

沿用 test_clustering_0021_persistence.py 的拋棄式 DB 模式（建庫 → alembic upgrade head → 種 fixture）。
不跑 BERTopic：只驗不需要模型 artifact 的讀取／寫回路徑。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_wssvc0021"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

WIPS = "wips_independent_claims"
EFFECT = "effect_summary"

# fixture ID 區段（與其他測試庫互不重疊）
WS_ID = 950001
WF_CALIBRATE = 951001
WF_FINALIZE = 951002
PATENT_IDS = (950101, 950102, 950103)

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組出連測試庫的 psycopg 參數（與其他 0021 測試同源）。"""
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
    """種三筆專利與兩筆 workflow_runs；workspace 由各測試自行建立。"""
    with psycopg.connect(**_kw(TEST_DB)) as c:
        for pid in PATENT_IDS:
            c.execute(
                "INSERT INTO core_layer.patents (id, title) VALUES (%s, 'wssvc0021 fixture')",
                (pid,),
            )
        c.commit()


def _reset():
    """每個測試前清掉 workspace 與分群寫入，讓各測試互不干擾。"""
    with psycopg.connect(**_kw(TEST_DB)) as c:
        c.execute("DELETE FROM derived_layer.topic_assignments")
        c.execute("DELETE FROM derived_layer.topic_runs")
        c.execute("DELETE FROM app_layer.workflow_runs")
        c.execute("DELETE FROM app_layer.workspaces")
        c.commit()


def _seed_workspace() -> int:
    """建 workspace（含三筆成員）與 calibrate/finalize 兩個 workflow_run。"""
    with psycopg.connect(**_kw(TEST_DB)) as c:
        c.execute(
            "INSERT INTO app_layer.workspaces (workspace_id, workspace_name, patent_ids_json) "
            "VALUES (%s, %s, %s)",
            (WS_ID, "wssvc0021_ws", psycopg.types.json.Jsonb(list(PATENT_IDS))),
        )
        for run_id, run_type, status in (
            (WF_CALIBRATE, "clustering_calibrate", "running"),
            (WF_FINALIZE, "clustering_finalize", "succeeded"),
        ):
            c.execute(
                "INSERT INTO app_layer.workflow_runs (run_id, workspace_id, run_type, status) "
                "VALUES (%s, %s, %s, %s)",
                (run_id, WS_ID, run_type, status),
            )
        c.commit()
    return WS_ID


def _seed_final_run() -> int:
    """種一個已定案 finalize topic_run：兩個 active 主題＋三筆指派，回傳 topic run_id。"""
    from backend.app.clustering.runner import create_topic_run, persist_final_topics

    run_id = create_topic_run(
        workflow_run_id=WF_FINALIZE,
        source_field=WIPS,
        state={"run_mode": "full", "input_doc_count": 3, "artifact_version": 1},
    )
    topics = [
        {"topic_id": 1, "topic_code": "T001", "topic_kind": "model", "status": "active",
         "label": "鋸切結構", "label_source": "fallback", "display_order": 1, "doc_count": 2,
         "model_topic_ids": [0], "keywords": [{"term": "鋸切", "weight": 1.0}],
         "representative_patent_ids": [PATENT_IDS[0], PATENT_IDS[1]]},
        {"topic_id": 2, "topic_code": "T002", "topic_kind": "model", "status": "active",
         "label": "進給機構", "label_source": "fallback", "display_order": 2, "doc_count": 1,
         "model_topic_ids": [1], "keywords": [{"term": "進給", "weight": 1.0}],
         "representative_patent_ids": [PATENT_IDS[2]]},
    ]
    assignments = [
        (PATENT_IDS[0], "T001", 0.1),
        (PATENT_IDS[1], "T001", 0.2),
        (PATENT_IDS[2], "T002", 0.3),
    ]
    persist_final_topics(
        run_id=run_id, topics=topics, assignments=assignments,
        metrics={"score": 0.9}, artifact_key="ws/950001/base.pkl",
    )
    return run_id


class WorkspaceMembershipTests(unittest.TestCase):
    """workspace 成員落點：workspaces.patent_ids_json，不再有 workspace_patents 表。"""

    def setUp(self):
        _reset()

    def test_create_workspace_writes_patent_ids_json(self):
        from backend.app.clustering.workspace_service import create_workspace

        workspace_id = create_workspace(
            workspace_name="建立測試", patent_ids=list(PATENT_IDS), created_by="tester")
        with psycopg.connect(**_kw(TEST_DB), row_factory=dict_row) as c:
            row = c.execute(
                "SELECT patent_ids_json FROM app_layer.workspaces WHERE workspace_id = %s",
                (workspace_id,),
            ).fetchone()
        self.assertEqual([int(v) for v in row["patent_ids_json"]], list(PATENT_IDS))

    def test_add_workspace_patents_appends_without_duplicates(self):
        from backend.app.clustering.workspace_service import (
            add_workspace_patents,
            create_workspace,
        )

        workspace_id = create_workspace(
            workspace_name="加入測試", patent_ids=[PATENT_IDS[0]], created_by="tester")
        # 重複加入既有成員不得產生重複項
        result = add_workspace_patents(
            workspace_id=workspace_id,
            patent_ids=[PATENT_IDS[0], PATENT_IDS[1], PATENT_IDS[2]],
            added_by="tester",
        )
        self.assertEqual(result["workspace_patent_count"], 3)
        with psycopg.connect(**_kw(TEST_DB), row_factory=dict_row) as c:
            row = c.execute(
                "SELECT patent_ids_json FROM app_layer.workspaces WHERE workspace_id = %s",
                (workspace_id,),
            ).fetchone()
        self.assertEqual([int(v) for v in row["patent_ids_json"]], list(PATENT_IDS))

    def test_dashboard_reads_members_from_patent_ids_json(self):
        from backend.app.clustering.workspace_service import (
            create_workspace,
            workspace_dashboard,
        )

        workspace_id = create_workspace(
            workspace_name="儀表板測試", patent_ids=list(PATENT_IDS), created_by="tester")
        payload = workspace_dashboard(workspace_id)
        self.assertEqual(
            [int(p["patent_id"]) for p in payload["patents"]], list(PATENT_IDS))
        # 尚無分群結果時，主題一律未分類，不得因缺表而炸開
        self.assertTrue(all(p["technical_topic"] == "未分類" for p in payload["patents"]))


class CandidateJsonLandingTests(unittest.TestCase):
    """候選落點：topic_state_json->'candidates'，讀寫與 runner._persist_calibration 同源。"""

    def setUp(self):
        _reset()
        _seed_workspace()
        from backend.app.clustering.runner import create_topic_run

        self.run_id = create_topic_run(
            workflow_run_id=WF_CALIBRATE, source_field=WIPS,
            state={"input_doc_count": 3})

    def _persist_candidates(self):
        from backend.app.clustering.runner import (
            CandidateProfile,
            KScanResult,
            _persist_calibration,
        )

        candidates = [
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
        return _persist_calibration(
            run_id=self.run_id,
            scan_results=[c.result for c in candidates],
            candidates=candidates,
        )

    def test_candidate_review_payload_reads_topic_state_json(self):
        from backend.app.clustering.workspace_service import candidate_review_payload

        self._persist_candidates()
        payload = candidate_review_payload(self.run_id)
        self.assertEqual(payload["run_id"], self.run_id)
        # workspace_id 只能經 workflow_runs JOIN 取得
        self.assertEqual(payload["workspace_id"], WS_ID)
        self.assertEqual([c["k"] for c in payload["candidates"]], [10, 20, 30])
        self.assertEqual(payload["document_count"], 3)

    def test_apply_candidate_explanations_writes_back_to_topic_state_json(self):
        from backend.app.clustering.workspace_service import (
            apply_candidate_explanations,
            candidate_review_payload,
        )

        persisted = self._persist_candidates()
        candidate_id = int(persisted[1]["candidate_id"])
        result = apply_candidate_explanations(
            run_id=self.run_id,
            explanations=[{"candidate_id": candidate_id, "explanation": "主題數適中"}],
        )
        self.assertEqual(result["updated_count"], 1)
        # 寫回落點必須與讀取端同源
        payload = candidate_review_payload(self.run_id)
        by_id = {c["candidate_id"]: c for c in payload["candidates"]}
        self.assertEqual(by_id[candidate_id]["existing_explanation"], "主題數適中")
        # 其他候選不得被波及
        others = [c for cid, c in by_id.items() if cid != candidate_id]
        self.assertTrue(all(c["existing_explanation"] is None for c in others))


class TopicLabelJsonLandingTests(unittest.TestCase):
    """主題標籤落點：topic_state_json->'topics'，與 TopicStateRepository 讀取一致。"""

    def setUp(self):
        _reset()
        _seed_workspace()
        self.run_id = _seed_final_run()

    def test_topic_labeling_payload_reads_topics_from_topic_state_json(self):
        from backend.app.clustering.workspace_service import topic_labeling_payload

        payload = topic_labeling_payload(workspace_id=WS_ID, source_field=WIPS)
        codes = [t["topic_code"] for t in payload["topics"]]
        self.assertEqual(codes, ["T001", "T002"])
        self.assertTrue(all(t["current_label_source"] == "fallback" for t in payload["topics"]))

    def test_apply_topic_labels_updates_latest_run_topic_state(self):
        from backend.app.clustering.workspace_service import apply_topic_labels
        from backend.app.repositories.topic_state_repository import (
            PostgresTopicStateRepository,
        )

        result = apply_topic_labels(
            workspace_id=WS_ID,
            source_field=WIPS,
            labels=[{"topic_code": "T001", "label": "鋸切總成", "summary": "鋸切相關結構"}],
        )
        self.assertEqual(result["updated_count"], 1)
        # 讀寫同源：改完後 repository 必須讀到新標籤，且指派不受影響
        state = PostgresTopicStateRepository().get_latest_topic_state(WS_ID, WIPS)
        by_code = {t["topic_code"]: t for t in state["topics"]}
        self.assertEqual(by_code["T001"]["label"], "鋸切總成")
        self.assertEqual(by_code["T001"]["patent_ids"], [PATENT_IDS[0], PATENT_IDS[1]])
        self.assertEqual(by_code["T002"]["label"], "進給機構")

    def test_apply_topic_labels_does_not_override_manual(self):
        """manual 標籤只能由 rename endpoint 寫入，AI 通道不得覆蓋。"""
        from backend.app.clustering.workspace_service import apply_topic_labels

        apply_topic_labels(
            workspace_id=WS_ID, source_field=WIPS,
            labels=[{"topic_code": "T001", "label": "人工命名", "source": "fallback"}])
        # 先手動把 label_source 改成 manual，再嘗試以 llm 覆蓋
        with psycopg.connect(**_kw(TEST_DB)) as c:
            c.execute(
                """
                UPDATE derived_layer.topic_runs
                SET topic_state_json = jsonb_set(
                    topic_state_json, '{topics,0,label_source}', '"manual"'::jsonb)
                WHERE run_id = %s
                """,
                (self.run_id,),
            )
            c.commit()
        result = apply_topic_labels(
            workspace_id=WS_ID, source_field=WIPS,
            labels=[{"topic_code": "T001", "label": "AI 命名"}])
        self.assertEqual(result["updated_count"], 0)

    def test_refresh_topic_counts_recomputes_from_assignments(self):
        from backend.app.clustering.workspace_service import refresh_topic_counts
        from backend.app.repositories.topic_state_repository import (
            PostgresTopicStateRepository,
        )

        refresh_topic_counts(workspace_id=WS_ID, source_field=WIPS)
        state = PostgresTopicStateRepository().get_latest_topic_state(WS_ID, WIPS)
        by_code = {t["topic_code"]: t for t in state["topics"]}
        self.assertEqual(by_code["T001"]["doc_count"], 2)
        self.assertEqual(by_code["T002"]["doc_count"], 1)


class NoLegacyTableReferenceTests(unittest.TestCase):
    """靜態檢查：模組不得再引用 0021 已刪除的表。"""

    def test_source_has_no_dropped_table_references(self):
        """只看實際 SQL（FROM/JOIN/INTO/UPDATE 後接表名），註解提到舊表名不算違規。"""
        import re

        source = (
            PROJECT_ROOT / "backend" / "app" / "clustering" / "workspace_service.py"
        ).read_text(encoding="utf-8")
        for dropped in (
            "app_layer.workspace_patents",
            "derived_layer.topics",
            "derived_layer.topic_candidates",
        ):
            pattern = re.compile(
                rf"\b(FROM|JOIN|INTO|UPDATE)\s+{re.escape(dropped)}\b", re.IGNORECASE)
            self.assertIsNone(
                pattern.search(source), f"still queries dropped table: {dropped}")

    def test_source_keeps_chinese_comments(self):
        """遷移必須保留既有中文註解（防 CP950 round-trip 破壞）。"""
        source = (
            PROJECT_ROOT / "backend" / "app" / "clustering" / "workspace_service.py"
        ).read_text(encoding="utf-8")
        self.assertIn("workspace 分群應用服務", source)
        self.assertIn("不複製或修改核心專利值", source)


if __name__ == "__main__":
    unittest.main()
