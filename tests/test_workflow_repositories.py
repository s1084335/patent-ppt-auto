"""0021 3+3 schema 的 repository 邊界契約（拋棄式 DB patent_ppt_repocheck，不碰 patent_ppt）。

三組契約：
1. TopicStateRepository：按 workspace_id/source_field 讀最新正式主題狀態
   （合併／改名後、不回候選、保留未分類、assignments 併回正式 topic_code）。
2. WorkflowOutputsRepository：data_json 版本化寫入／讀取，新版本不覆蓋舊值；
   artifact_manifest_json 只准圖檔/PPT。
3. company_aliases 變體補登：已知唯一 WIPS code 補新變體並沿用既有正規化名稱；
   未知/衝突 code 進人工確認；不動原始專利值。
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_repocheck"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

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


def setUpModule():
    """建拋棄式 DB → upgrade head → 種 3+3 fixture；admin 不可用則整組 skip。"""
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"  # Windows localhost 走 IPv6 會慢，見 migration contract 註解
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
    """直接以 3+3 目標 schema 種資料（不經 legacy 搬移），涵蓋最新/舊 run、合併、未分類、候選與跨 workspace 隔離。"""
    state_912002 = {
        "topics": [
            {"topic_id": 913001, "topic_code": "T01", "label": "鋸切結構", "status": "active",
             "topic_kind": "model", "label_source": "manual", "doc_count": 2},
            {"topic_id": 913002, "topic_code": "T02", "label": "舊主題", "status": "merged",
             "merged_into_topic_id": 913001, "topic_kind": "model", "doc_count": 1},
            {"topic_id": 913003, "topic_code": "U00", "label": "未分類", "status": "active",
             "topic_kind": "unclassified", "doc_count": 1},
        ],
        "candidates": [{"candidate_id": 1, "candidate_type": "balanced", "candidate_k": 5}],
    }
    state_912001 = {"topics": [{"topic_id": 913000, "topic_code": "T01", "label": "舊標籤",
                                "status": "active", "topic_kind": "model", "doc_count": 1}]}
    state_912003 = {"topics": [{"topic_id": 913004, "topic_code": "E01", "label": "省力效果",
                                "status": "active", "topic_kind": "model", "doc_count": 1}]}
    state_912004 = {"topics": [{"topic_id": 913005, "topic_code": "X01", "label": "別家主題",
                                "status": "active", "topic_kind": "model", "doc_count": 1}]}
    # incremental 形狀（重現正式庫 ws164）：finalize run 帶 topics＋多筆 assignments；
    # 其後 incremental run 的 state 不帶 topics、只帶增量 assignment。此處以最小筆數
    # 重現該形狀（正式庫為 200＋1），驗證讀取須沿 run_id fallback 取 topics、跨 run 併指派。
    state_912005 = {"topics": [
        {"topic_id": 913006, "topic_code": "T01", "label": "主結構", "status": "active",
         "topic_kind": "model", "doc_count": 2},
        {"topic_id": 913007, "topic_code": "T02", "label": "傳動", "status": "active",
         "topic_kind": "model", "doc_count": 1},
    ]}
    state_912006 = {"topics": []}  # incremental run：state 不帶 topics
    with psycopg.connect(**_kw(TEST_DB)) as c:
        for pid in (910001, 910002, 910003, 910004, 910005, 910006):
            c.execute("INSERT INTO core_layer.patents (id, title) VALUES (%s, 'repo fixture')", (pid,))
        # 原始專利值：alias 變體補登不得改動
        c.execute("INSERT INTO core_layer.patent_people (patent_id, \"申請人\") VALUES (910001, 'REXON IND.')")
        c.execute("INSERT INTO app_layer.workspaces (workspace_id, workspace_name) VALUES (910001, 'repo_ws')")
        c.execute("INSERT INTO app_layer.workspaces (workspace_id, workspace_name) VALUES (910002, 'repo_ws_other')")
        c.execute("INSERT INTO app_layer.workspaces (workspace_id, workspace_name) VALUES (910005, 'repo_ws_incr')")
        for run_id, ws, rt in (
            (911001, 910001, "clustering:wips_independent_claims"),
            (911002, 910001, "clustering:wips_independent_claims"),
            (911003, 910001, "clustering:effect_summary"),
            (911004, 910002, "clustering:wips_independent_claims"),
            (911005, 910005, "clustering:wips_independent_claims"),  # finalize
            (911006, 910005, "clustering:wips_independent_claims"),  # incremental
        ):
            c.execute(
                "INSERT INTO app_layer.workflow_runs (run_id, workspace_id, run_type, status) "
                "VALUES (%s, %s, %s, 'succeeded')", (run_id, ws, rt))
        for run_id, wf, sf, state in (
            (912001, 911001, "wips_independent_claims", state_912001),
            (912002, 911002, "wips_independent_claims", state_912002),
            (912003, 911003, "effect_summary", state_912003),
            (912004, 911004, "wips_independent_claims", state_912004),
            (912005, 911005, "wips_independent_claims", state_912005),  # finalize：帶 topics
            (912006, 911006, "wips_independent_claims", state_912006),  # incremental：無 topics
        ):
            c.execute(
                "INSERT INTO derived_layer.topic_runs (run_id, workflow_run_id, source_field, topic_state_json) "
                "VALUES (%s, %s, %s, %s)", (run_id, wf, sf, Jsonb(state)))
        for run_id, pid, key in (
            (912001, 910001, "T01"),   # 舊 run：不得被回傳
            (912002, 910001, "T01"),
            (912002, 910002, "T02"),   # 已合併主題：須併回 T01
            (912002, 910003, "U00"),   # 未分類：保留
            (912003, 910001, "E01"),
            (912004, 910001, "X01"),
            (912005, 910004, "T01"),   # finalize run 的既有指派
            (912005, 910005, "T01"),
            (912005, 910006, "T02"),
            (912006, 910005, "T02"),   # incremental：910005 由 T01 覆蓋成 T02（驗「取最新一筆」）
        ):
            c.execute(
                "INSERT INTO derived_layer.topic_assignments (run_id, patent_id, topic_key) "
                "VALUES (%s, %s, %s)", (run_id, pid, key))
        # alias 對照表：C001 唯一名稱＋既有別稱；C002 衝突（兩個公司名稱）
        for code, name, alias in (
            ("C001", "力山工業股份有限公司", "REXON INDUSTRIAL CORP"),
            ("C002", "甲公司", "A ALIAS ONE"),
            ("C002", "乙公司", "A ALIAS TWO"),
        ):
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼", "公司名稱", "別稱", source_file) '
                "VALUES (%s, %s, %s, 'seed')", (code, name, alias))
        c.commit()


def _scalar(sql: str, params=()):
    with psycopg.connect(**_kw(TEST_DB)) as c:
        row = c.execute(sql, params).fetchone()
    return row[0] if row else None


class TopicStateRepositoryTests(unittest.TestCase):
    """契約：最新正式主題狀態（合併/改名後、無候選、保留未分類）。"""

    def _repo(self):
        from backend.app.repositories.topic_state_repository import PostgresTopicStateRepository
        return PostgresTopicStateRepository()

    def _latest(self, ws=910001, sf="wips_independent_claims"):
        return self._repo().get_latest_topic_state(ws, sf)

    def test_latest_run_and_renamed_label(self):
        """回傳最新 run（912002 非 912001），label 為改名後值。"""
        state = self._latest()
        self.assertEqual(state["run_id"], 912002)
        self.assertEqual(state["workspace_id"], 910001)
        by_code = {t["topic_code"]: t for t in state["topics"]}
        self.assertEqual(by_code["T01"]["label"], "鋸切結構")

    def test_no_candidates_and_no_merged_topics(self):
        """不回候選方案；已合併主題不作為獨立主題出現。"""
        state = self._latest()
        codes = {t["topic_code"] for t in state["topics"]}
        self.assertEqual(codes, {"T01", "U00"})
        self.assertNotIn("candidates", state)

    def test_merged_assignment_remapped_to_target(self):
        """指到已合併主題（T02）的 assignment 併回目標主題 T01。"""
        by_code = {t["topic_code"]: t for t in self._latest()["topics"]}
        self.assertEqual(by_code["T01"]["patent_ids"], [910001, 910002])

    def test_unclassified_preserved(self):
        """未分類主題保留且帶自己的 assignments。"""
        by_code = {t["topic_code"]: t for t in self._latest()["topics"]}
        self.assertEqual(by_code["U00"]["topic_kind"], "unclassified")
        self.assertEqual(by_code["U00"]["patent_ids"], [910003])

    def test_effect_summary_supported(self):
        """支援 effect_summary 通道。"""
        state = self._latest(sf="effect_summary")
        self.assertEqual(state["run_id"], 912003)
        self.assertEqual(state["topics"][0]["topic_code"], "E01")
        self.assertEqual(state["topics"][0]["patent_ids"], [910001])

    def test_workspace_isolation(self):
        """不同 workspace 不互漏。"""
        state = self._latest(ws=910002)
        self.assertEqual({t["topic_code"] for t in state["topics"]}, {"X01"})

    def test_incremental_run_falls_back_to_finalize_topics(self):
        """incremental run（state 無 topics）後：topics 沿 fallback 取 finalize run，
        assignments 跨 run 每 patent 取最新一筆，run_id/state_run_id 語意分明。"""
        state = self._latest(ws=910005)
        # topics 來源＝最新「有 topics」的 run（finalize 912005），非最新 run
        self.assertEqual(state["state_run_id"], 912005)
        # assignments 基準 run＝該 ws/field 最新 run（incremental 912006）
        self.assertEqual(state["run_id"], 912006)
        by_code = {t["topic_code"]: t for t in state["topics"]}
        self.assertEqual(set(by_code), {"T01", "T02"})  # 不因 incremental 無 topics 回空
        # 910005 finalize 指到 T01、incremental 覆蓋成 T02 → 取最新一筆歸 T02
        self.assertEqual(by_code["T01"]["patent_ids"], [910004])
        self.assertEqual(by_code["T02"]["patent_ids"], [910005, 910006])

    def test_not_found_and_invalid_source_field(self):
        """無 run 拋 TopicStateNotFoundError；非法 source_field 拋 ValueError。"""
        from backend.app.repositories.topic_state_repository import TopicStateNotFoundError
        with self.assertRaises(TopicStateNotFoundError):
            self._latest(ws=999999)
        with self.assertRaises(ValueError):
            self._latest(sf="bogus_field")


class WorkflowOutputsRepositoryTests(unittest.TestCase):
    """契約：版本化寫入不覆蓋；artifact manifest 只准圖檔/PPT。"""

    RUN = 911001

    def _repo(self):
        from backend.app.repositories.workflow_outputs_repository import PostgresWorkflowOutputsRepository
        return PostgresWorkflowOutputsRepository()

    def test_versioned_append_and_read(self):
        repo = self._repo()
        v1 = repo.append_output(self.RUN, "chart:applicant_ranking", {"rows": [1]})
        v2 = repo.append_output(self.RUN, "chart:applicant_ranking", {"rows": [2]})
        self.assertEqual((v1, v2), (1, 2))
        # 新版本不得覆蓋舊值：兩版皆在且值各自正確
        self.assertEqual(repo.get_output(self.RUN, "chart:applicant_ranking", version=1)["data_json"], {"rows": [1]})
        latest = repo.get_output(self.RUN, "chart:applicant_ranking")
        self.assertEqual((latest["version"], latest["data_json"]), (2, {"rows": [2]}))
        self.assertEqual(_scalar(
            "SELECT count(*) FROM app_layer.workflow_outputs "
            "WHERE run_id=%s AND output_type='chart:applicant_ranking'", (self.RUN,)), 2)

    def test_artifact_manifest_only_chart_or_ppt(self):
        repo = self._repo()
        ok_png = repo.append_artifact_output(self.RUN, "artifact:opportunity_matrix",
                                             {"artifact_key": "charts/matrix.png", "sha256": "abc"})
        ok_ppt = repo.append_artifact_output(self.RUN, "artifact:report_ppt",
                                             {"artifact_key": "ppt/report.pptx", "sha256": "def"})
        self.assertEqual((ok_png, ok_ppt), (1, 1))
        with self.assertRaises(ValueError):
            repo.append_artifact_output(self.RUN, "artifact:bad", {"artifact_key": "data/rows.csv"})
        self.assertEqual(_scalar(
            "SELECT count(*) FROM app_layer.workflow_outputs WHERE run_id=%s AND output_type='artifact:bad'",
            (self.RUN,)), 0, "被拒的 manifest 不得留下任何列")

    def test_get_missing_returns_none(self):
        self.assertIsNone(self._repo().get_output(self.RUN, "chart:nonexistent"))


class CompanyAliasVariantTests(unittest.TestCase):
    """契約：已知唯一 code 補變體沿用既有名稱；未知/衝突進人工確認；不改原始專利值。"""

    def test_register_variants(self):
        from backend.app.derived.company_alias_importer import register_known_code_variants
        summary = register_known_code_variants([
            ("C001", "Rexon Industrial Corp., Ltd."),  # 已知唯一 code 的新變體 → 補入
            ("C001", "REXON INDUSTRIAL CORP"),         # 既有別稱 → 跳過
            ("C999", "新公司股份有限公司"),               # 未知 code → 人工確認
            ("C002", "B VARIANT"),                     # 衝突 code（兩個公司名稱）→ 人工確認
        ])
        self.assertEqual(summary["inserted"], 1)
        self.assertEqual(summary["skipped_existing"], 1)
        reasons = {(m["company_code"], m["reason"]) for m in summary["manual_review"]}
        self.assertEqual(reasons, {("C999", "unknown_code"), ("C002", "conflicting_code")})
        # 新變體沿用既有正規化公司名稱
        self.assertEqual(_scalar(
            'SELECT "公司名稱" FROM derived_layer.company_aliases '
            "WHERE \"申請人代碼\"='C001' AND \"別稱\"=%s", ("Rexon Industrial Corp., Ltd.",)),
            "力山工業股份有限公司")
        # 未知/衝突不得寫表
        self.assertEqual(_scalar(
            "SELECT count(*) FROM derived_layer.company_aliases WHERE \"申請人代碼\"='C999'"), 0)
        self.assertEqual(_scalar(
            "SELECT count(*) FROM derived_layer.company_aliases WHERE \"申請人代碼\"='C002'"), 2)
        # 原始專利值不得被改動
        self.assertEqual(_scalar(
            'SELECT "申請人" FROM core_layer.patent_people WHERE patent_id=910001'), "REXON IND.")


class ProductionReadOnlySmokeTests(unittest.TestCase):
    """正式庫唯讀煙囪：對 patent_ppt ws164 呼叫 get_latest_topic_state，斷言 incremental
    修正後數字（topics=12、assignments=201、state_run_id=58）。

    紅線：只 SELECT，session 強制 default_transaction_read_only=on，任何寫入直接報錯；
    連不上正式庫時 skip（不視為失敗）。數字對應 2026-07-21 正式庫快照。
    """

    def _ro_repo(self):
        from backend.app.repositories.topic_state_repository import PostgresTopicStateRepository
        kw = dict(
            host=os.getenv("PGHOST", "127.0.0.1"),
            port=int(os.getenv("PGPORT", "5433")),
            user=os.getenv("PGUSER", "postgres"),
            dbname="patent_ppt",
            options="-c default_transaction_read_only=on",  # 紅線：整個 session 唯讀
        )
        password = os.getenv("PGPASSWORD")
        if password:
            kw["password"] = password
        return PostgresTopicStateRepository(kw)

    def test_ws164_incremental_state_readonly(self):
        try:
            with psycopg.connect(
                host=os.getenv("PGHOST", "127.0.0.1"),
                port=int(os.getenv("PGPORT", "5433")),
                user=os.getenv("PGUSER", "postgres"),
                dbname="patent_ppt",
                connect_timeout=3,
            ) as probe:
                if probe.execute(
                    "SELECT 1 FROM app_layer.workspaces WHERE workspace_id = 164"
                ).fetchone() is None:
                    self.skipTest("patent_ppt has no workspace 164")
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"patent_ppt unavailable: {exc}")

        state = self._ro_repo().get_latest_topic_state(164, "wips_independent_claims")
        n_assign = sum(len(t["patent_ids"]) for t in state["topics"])
        self.assertEqual(len(state["topics"]), 12)
        self.assertEqual(n_assign, 201)
        self.assertEqual(state["state_run_id"], 58)


if __name__ == "__main__":
    unittest.main()
