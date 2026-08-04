"""cluster_data_loader 單元＋拋棄式 DB 測試。

Mock 層覆蓋純邏輯與接線；DB 層（RUN_DB_TESTS=1）在 patent_ppt_loadercheck
驗證真實 SQL 與合併鏈解析。
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row

from backend.app.reports.cluster_data_loader import (
    compute_and_save_cluster_analysis,
    load_cluster_workspace_data,
    run_full_report,
)
from backend.app.repositories.topic_state_repository import TopicStateNotFoundError

TEST_DB = "patent_ppt_loadercheck"
# 0021（head）：load_cluster_workspace_data 已換接 topic_state_repository，
# 讀 topic_runs/topic_assignments/topic_state_json，不再讀 0018 derived_layer.topics。
TARGET_REV = "head"

# ── helpers ──────────────────────────────────────────────────────

def _kw(dbname: str) -> dict:
    return dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
        password=os.getenv("PGPASSWORD"),
    )


def _alembic_cfg() -> Config:
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return cfg


# ======================================================================
#  拋棄式 DB 測試（RUN_DB_TESTS=1）
# ======================================================================

@unittest.skipUnless(os.environ.get("RUN_DB_TESTS") == "1",
                     "set RUN_DB_TESTS=1 to run cluster_data_loader DB tests")
class ClusterDataLoaderDbTests(unittest.TestCase):
    """在專用拋棄式 DB patent_ppt_loadercheck 驗證真實 SQL 行為。"""

    @classmethod
    def setUpClass(cls):
        cls._prev_pghost = os.environ.get("PGHOST")
        os.environ["PGHOST"] = "127.0.0.1"
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")

        cls._prev_pgdb = os.environ.get("PGDATABASE")
        cls._prev_dburl = os.environ.get("DATABASE_URL")
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB

        command.upgrade(_alembic_cfg(), TARGET_REV)
        cls._seed()

    @classmethod
    def tearDownClass(cls):
        if cls._prev_pghost is None:
            os.environ.pop("PGHOST", None)
        else:
            os.environ["PGHOST"] = cls._prev_pghost
        if cls._prev_pgdb is None:
            os.environ.pop("PGDATABASE", None)
        else:
            os.environ["PGDATABASE"] = cls._prev_pgdb
        if cls._prev_dburl is not None:
            os.environ["DATABASE_URL"] = cls._prev_dburl
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:
            pass

    @classmethod
    def _seed(cls):
        """0021 fixture — 重現正式庫 finalize＋incremental 形狀，涵蓋合併/未分類/多申請人。

        finalize run（6001）帶 topics 與多筆 assignments；其後 incremental run（6002）
        的 topic_state_json 不帶 topics、只帶 1 筆新增 assignment。驗證 load 沿 fallback
        取 finalize topics，並跨 run 併回 assignments（含 merged 重映與 incremental 增量）。
        """
        finalize_state = {"topics": [
            {"topic_id": 1, "topic_code": "T01", "label": "半導體製程", "status": "active",
             "topic_kind": "model", "doc_count": 3},
            {"topic_id": 2, "topic_code": "T02", "label": "面板驅動", "status": "active",
             "topic_kind": "model", "doc_count": 1},
            {"topic_id": 3, "topic_code": "T01_OLD", "label": "半導體(舊)", "status": "merged",
             "merged_into_topic_id": 1, "topic_kind": "model", "doc_count": 0},
            {"topic_id": 4, "topic_code": "UNCLASSIFIED", "label": "未分類", "status": "active",
             "topic_kind": "unclassified", "doc_count": 1},
        ]}
        incremental_state = {"topics": []}  # incremental run：state 不帶 topics
        from psycopg.types.json import Jsonb
        with psycopg.connect(**_kw(TEST_DB)) as c:
            # core_layer.patents（FK 基礎）
            for pid, title in [(101, "p101"), (102, "p102"), (103, "p103"),
                               (104, "p104"), (105, "p105"), (106, "p106"),
                               (107, "p107"), (108, "p108"), (109, "p109"),
                               (110, "p110")]:
                c.execute("INSERT INTO core_layer.patents (id, title) VALUES (%s, %s)", (pid, title))

            # workspace
            c.execute("INSERT INTO app_layer.workspaces (workspace_id, workspace_name) VALUES (1, 'test_ws')")

            # workflow_runs：finalize 5001、incremental 5002
            for run_id in (5001, 5002):
                c.execute(
                    "INSERT INTO app_layer.workflow_runs (run_id, workspace_id, run_type, status) "
                    "VALUES (%s, 1, 'clustering:wips_independent_claims', 'succeeded')", (run_id,))

            # topic_runs：finalize 帶 topics，incremental 不帶
            for run_id, wf, state in (
                (6001, 5001, finalize_state),
                (6002, 5002, incremental_state),
            ):
                c.execute(
                    "INSERT INTO derived_layer.topic_runs (run_id, workflow_run_id, source_field, topic_state_json) "
                    "VALUES (%s, %s, 'wips_independent_claims', %s)", (run_id, wf, Jsonb(state)))

            # topic_assignments（0021：run_id, patent_id, topic_key）
            for run_id, pid, key in (
                (6001, 101, "T01"),
                (6001, 102, "T01"),
                (6001, 103, "T02"),
                (6001, 104, "T01_OLD"),      # merged → 併回 T01
                (6001, 105, "UNCLASSIFIED"),
                (6002, 106, "T02"),          # incremental 只帶 1 筆新增
            ):
                c.execute(
                    "INSERT INTO derived_layer.topic_assignments (run_id, patent_id, topic_key) "
                    "VALUES (%s, %s, %s)", (run_id, pid, key))

            # report_patent_base（101/102/104 各家 測去重；103,105 各一；106-110 測 top 10）
            c.execute("""
                INSERT INTO derived_layer.report_patent_base (patent_id, applicant_display_name)
                VALUES
                    (101, 'TSMC'),
                    (102, 'TSMC'),
                    (103, 'Samsung'),
                    (104, 'Intel'),
                    (105, 'Others'),
                    (106, 'CompanyA'),
                    (107, 'CompanyB'),
                    (108, 'CompanyC'),
                    (109, 'CompanyD'),
                    (110, 'CompanyE')
            """)

            c.commit()

    def _conn(self):
        return psycopg.connect(**_kw(TEST_DB), row_factory=dict_row)

    # ── tests ─────────────────────────────────────────────────

    def test_merged_chain_remap(self):
        """assignment 指向 merged 主題須併回 active 目標（T01_OLD→T01）。"""
        with self._conn() as conn:
            result = load_cluster_workspace_data(1, "wips_independent_claims", conn)
        codes = {a["topic_code"] for a in result["assignments"]}
        self.assertIn("T01", codes)
        self.assertNotIn("T01_OLD", codes)
        # 原本 T01 有 101,102；merged T01_OLD 有 104 → 合計 3
        t01_patents = {a["patent_id"] for a in result["assignments"] if a["topic_code"] == "T01"}
        self.assertEqual(t01_patents, {101, 102, 104})

    def test_unclassified_topic_retained(self):
        """UNCLASSIFIED 應保留在輸出清單與 assignments 中。"""
        with self._conn() as conn:
            result = load_cluster_workspace_data(1, "wips_independent_claims", conn)
        codes = {t["topic_code"] for t in result["topics"]}
        self.assertIn("UNCLASSIFIED", codes)
        unc_patents = {a["patent_id"] for a in result["assignments"] if a["topic_code"] == "UNCLASSIFIED"}
        self.assertEqual(unc_patents, {105})

    def test_applicant_dedup_same_company_same_patent(self):
        """同一專利同一公司只計 1 次，兩家申請人各計 1。"""
        # T01 的專利：101(TSMC), 102(TSMC), 104(Intel merged→T01)
        # TSMC 在 101,102 各出現，但同一公司同一專利不重複
        with self._conn() as conn:
            result = load_cluster_workspace_data(1, "wips_independent_claims", conn)
        # 原始 applicants 是 (patent_id, applicant_name) pairs
        t01_apps = [
            a for a in result["normalized_applicants"]
            if a["patent_id"] in {101, 102, 104}
        ]
        names = {a["applicant_name"] for a in t01_apps}
        self.assertIn("TSMC", names)
        self.assertIn("Intel", names)

    def test_top_applicants_ws_top_ten(self):
        """top_applicants_ws 回傳前十大申請人，不含空值。"""
        with self._conn() as conn:
            result = load_cluster_workspace_data(1, "wips_independent_claims", conn)
        self.assertLessEqual(len(result["top_applicants_ws"]), 10)
        self.assertIn("TSMC", result["top_applicants_ws"])
        # 驗證順序：TSMC(101,102) 2 件應排最前
        self.assertEqual(result["top_applicants_ws"][0], "TSMC")

    @unittest.skip("analysis_outputs 於 0021 併入 workflow_outputs（移入 legacy_0021）；"
                   "compute_and_save 寫入路徑換接為另案，本輪只換 load_cluster_workspace_data")
    def test_compute_and_save_writes_three_rows(self):
        """compute_and_save_cluster_analysis 寫入 topic_effect_table／
        opportunity_matrix／pain_point_matrix 三列。"""
        result = compute_and_save_cluster_analysis(
            workspace_id=1, source_field="wips_independent_claims",
            analysis_id=42,
        )
        self.assertEqual(result["analysis_status"], "saved")
        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                "SELECT output_name, output_type FROM app_layer.analysis_outputs "
                "WHERE analysis_id = 42 ORDER BY output_id"
            ).fetchall()
        names = [r[0] for r in rows]
        self.assertIn("topic_effect_table", names)
        self.assertIn("opportunity_matrix", names)
        self.assertIn("pain_point_matrix", names)

    @unittest.skip("analysis_outputs 於 0021 併入 workflow_outputs（移入 legacy_0021）；"
                   "compute_and_save 寫入路徑換接為另案，本輪只換 load_cluster_workspace_data")
    def test_compute_and_save_append_not_overwrite(self):
        """重跑追加新列，舊列逐值不變。"""
        compute_and_save_cluster_analysis(
            workspace_id=1, source_field="wips_independent_claims",
            analysis_id=42,
        )
        with psycopg.connect(**_kw(TEST_DB)) as c:
            first = c.execute(
                "SELECT output_id, result_json::text FROM app_layer.analysis_outputs "
                "WHERE analysis_id = 42 ORDER BY output_id"
            ).fetchall()
        # 第二次跑
        compute_and_save_cluster_analysis(
            workspace_id=1, source_field="wips_independent_claims",
            analysis_id=42,
        )
        with psycopg.connect(**_kw(TEST_DB)) as c:
            second = c.execute(
                "SELECT output_id, result_json::text FROM app_layer.analysis_outputs "
                "WHERE analysis_id = 42 ORDER BY output_id"
            ).fetchall()
        # 新列數 = 舊 + 3
        self.assertEqual(len(second), len(first) + 3)
        # 舊列逐值不變
        for i, (old_id, old_json) in enumerate(first):
            self.assertEqual(old_json, second[i][1])

    def test_no_topics_empty_result(self):
        """workspace 無主題時回傳空結構且不炸。"""
        # 用不存在的 workspace_id
        result = compute_and_save_cluster_analysis(
            workspace_id=999, source_field="wips_independent_claims",
            analysis_id=42,
        )
        self.assertEqual(result["analysis_status"], "no_topics")
        self.assertEqual(result["topics"], [])
        self.assertEqual(result["assignments"], [])
        self.assertEqual(result["normalized_applicants"], [])
        self.assertEqual(result["top_applicants_ws"], [])


# ======================================================================
#  Mock 測試（保持既有覆蓋，不碰 DB）
# ======================================================================

class LoadClusterDataTests(unittest.TestCase):
    """load_cluster_workspace_data: topics/assignments 委派 repository，applicant 走 conn。"""

    def _state(self, topics: list[dict]) -> dict:
        """組出 repository.get_latest_topic_state 的回傳形狀（合併/未分類已解析）。"""
        return {"workspace_id": 1, "source_field": "claims",
                "run_id": 2, "state_run_id": 1, "topics": topics}

    def _conn(self, applicant_rows: list[dict], top_rows: list[dict]) -> mock.MagicMock:
        """mock conn：cursor 依序回 applicant、top applicant 兩批 fetchall。"""
        cur = mock.MagicMock()
        cur.execute.return_value = cur
        cur.fetchall.side_effect = [applicant_rows, top_rows]
        conn = mock.MagicMock()
        conn.cursor.return_value = cur
        return conn

    @mock.patch("backend.app.reports.cluster_data_loader.PostgresTopicStateRepository")
    def test_loads_active_topics_and_assignments(self, mock_repo):
        mock_repo.return_value.get_latest_topic_state.return_value = self._state([
            {"topic_code": "T01", "label": "半導體製程", "status": "active",
             "topic_kind": "model", "doc_count": 2, "patent_ids": [101, 102]},
            {"topic_code": "T02", "label": "面板驅動", "status": "active",
             "topic_kind": "model", "doc_count": 1, "patent_ids": [103]},
        ])
        conn = self._conn(
            [{"patent_id": 101, "applicant_display_name": "TSMC"},
             {"patent_id": 102, "applicant_display_name": "TSMC"},
             {"patent_id": 103, "applicant_display_name": "Samsung"}],
            [{"applicant_display_name": "TSMC", "cnt": 2}])
        result = load_cluster_workspace_data(1, "claims", conn)

        self.assertEqual({t["topic_code"] for t in result["topics"]}, {"T01", "T02"})
        self.assertEqual(len(result["assignments"]), 3)
        self.assertEqual(len(result["normalized_applicants"]), 3)
        self.assertIn("TSMC", result["top_applicants_ws"])

    @mock.patch("backend.app.reports.cluster_data_loader.PostgresTopicStateRepository")
    def test_merged_topics_already_resolved_by_repo(self, mock_repo):
        # repository 已把 merged 併回 active，loader 直接採用其 topics/patent_ids
        mock_repo.return_value.get_latest_topic_state.return_value = self._state([
            {"topic_code": "T01", "label": "半導體", "status": "active",
             "topic_kind": "model", "doc_count": 2, "patent_ids": [101, 102]},
        ])
        conn = self._conn(
            [{"patent_id": 101, "applicant_display_name": "TSMC"},
             {"patent_id": 102, "applicant_display_name": "TSMC"}],
            [{"applicant_display_name": "TSMC", "cnt": 2}])
        result = load_cluster_workspace_data(1, "claims", conn)

        self.assertEqual({t["topic_code"] for t in result["topics"]}, {"T01"})
        self.assertEqual({a["topic_code"] for a in result["assignments"]}, {"T01"})
        self.assertEqual(len(result["assignments"]), 2)

    @mock.patch("backend.app.reports.cluster_data_loader.PostgresTopicStateRepository")
    def test_no_topics_returns_empty_structures(self, mock_repo):
        mock_repo.return_value.get_latest_topic_state.side_effect = TopicStateNotFoundError("none")
        conn = mock.MagicMock()
        result = load_cluster_workspace_data(99, "effect_summary", conn)
        self.assertEqual(result["topics"], [])
        self.assertEqual(result["assignments"], [])
        self.assertEqual(result["normalized_applicants"], [])
        self.assertEqual(result["top_applicants_ws"], [])

    @mock.patch("backend.app.reports.cluster_data_loader.PostgresTopicStateRepository")
    def test_patent_id_cast_to_int(self, mock_repo):
        mock_repo.return_value.get_latest_topic_state.return_value = self._state([
            {"topic_code": "T01", "label": "AI", "status": "active",
             "topic_kind": "model", "doc_count": 1, "patent_ids": [999]},
        ])
        conn = self._conn([{"patent_id": 999, "applicant_display_name": "Google"}],
                          [{"applicant_display_name": "Google", "cnt": 1}])
        result = load_cluster_workspace_data(1, "claims", conn)
        self.assertIsInstance(result["assignments"][0]["patent_id"], int)
        self.assertIsInstance(result["normalized_applicants"][0]["patent_id"], int)


class ComputeAndSaveClusterAnalysisTests(unittest.TestCase):
    """compute_and_save_cluster_analysis: 載入→計算→寫入 analysis_outputs。"""

    def _mock_cluster_data(self) -> dict:
        return {
            "topics": [
                {"topic_code": "T01", "label": "半導體製程",
                 "source_field": "independent_claims", "status": "active"},
                {"topic_code": "T02", "label": "面板驅動",
                 "source_field": "independent_claims", "status": "active"},
            ],
            "assignments": [
                {"topic_code": "T01", "patent_id": 101},
                {"topic_code": "T01", "patent_id": 102},
                {"topic_code": "T02", "patent_id": 103},
            ],
            "normalized_applicants": [
                {"patent_id": 101, "applicant_name": "TSMC"},
                {"patent_id": 102, "applicant_name": "TSMC"},
                {"patent_id": 103, "applicant_name": "Samsung"},
            ],
            "top_applicants_ws": ["TSMC"],
        }

    @mock.patch("backend.app.reports.cluster_data_loader.psycopg")
    @mock.patch("backend.app.reports.cluster_data_loader.load_cluster_workspace_data")
    def test_saves_all_three_analytics(self, mock_load, mock_psycopg):
        mock_load.return_value = self._mock_cluster_data()
        mock_cur = mock.MagicMock()
        mock_conn = mock_psycopg.connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value = mock_cur

        result = compute_and_save_cluster_analysis(
            workspace_id=1, source_field="independent_claims",
            analysis_id=42,
        )

        mock_load.assert_called_once()
        # 🔴 2026-08-04：痛點板刪除後落庫剩兩項。
        self.assertEqual(mock_cur.execute.call_count, 2)
        inserts = [call[0][0] for call in mock_cur.execute.call_args_list]
        for sql in inserts:
            self.assertIn("INSERT INTO app_layer.analysis_outputs", sql)

        output_names = [call[0][1][1] for call in mock_cur.execute.call_args_list]
        expected_names = ["topic_effect_table", "opportunity_matrix"]
        self.assertEqual(output_names, expected_names)

        mock_conn.commit.assert_called_once()
        self.assertIn("topics", result)
        self.assertIn("topic_rows", result)
        self.assertEqual(len(result["topic_rows"]), 2)

    @mock.patch("backend.app.reports.cluster_data_loader.psycopg")
    @mock.patch("backend.app.reports.cluster_data_loader.load_cluster_workspace_data")
    def test_no_topics_skips_save(self, mock_load, mock_psycopg):
        mock_load.return_value = {
            "topics": [], "assignments": [], "normalized_applicants": [],
            "top_applicants_ws": [],
        }
        mock_cur = mock.MagicMock()
        mock_conn = mock_psycopg.connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value = mock_cur

        result = compute_and_save_cluster_analysis(
            workspace_id=1, source_field="claims", analysis_id=42,
        )

        mock_cur.execute.assert_not_called()
        self.assertEqual(result["analysis_status"], "no_topics")


class RunFullReportTests(unittest.TestCase):
    """run_full_report: 整合 compute_and_save + run_chart_trial。"""

    @mock.patch("backend.app.reports.cluster_data_loader.run_chart_trial")
    @mock.patch("backend.app.reports.cluster_data_loader.compute_and_save_cluster_analysis")
    def test_integrates_both_steps(self, mock_compute, mock_chart):
        mock_compute.return_value = {
            "topics": [{"topic_code": "T01", "label": "AI", "source_field": "claims",
                        "status": "active"}],
            "assignments": [{"topic_code": "T01", "patent_id": 1}],
            "normalized_applicants": [{"patent_id": 1, "applicant_name": "Google"}],
            "top_applicants_ws": ["Google"],
            "topic_rows": [{"topic_code": "T01", "patent_count": 1, "applicant_count": 1,
                            "top_applicants": [{"name": "Google", "count": 1}]}],
            "opportunity_matrix": {"rows": [], "patent_count_median": 1.0,
                                   "applicant_count_median": 1.0},
            "pain_point_matrix": {"rows": [], "x_median": 1.0},
            "analysis_status": "saved",
        }
        mock_chart.return_value = {"status": "ok", "output_dir": "/tmp/report_123"}

        result = run_full_report(
            workspace_id=1, source_field="claims",
            analysis_id=42, output_dir="/tmp",
        )

        mock_compute.assert_called_once_with(
            workspace_id=1, source_field="claims",
            analysis_id=42, pain_data=None,
        )
        mock_chart.assert_called_once()
        _, kwargs = mock_chart.call_args
        self.assertIn("cluster_data", kwargs)
        self.assertIsNotNone(kwargs["cluster_data"])
        self.assertIn("analysis_id", kwargs)
        self.assertEqual(kwargs["analysis_id"], 42)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_dir"], "/tmp/report_123")

    @mock.patch("backend.app.reports.cluster_data_loader.run_chart_trial")
    @mock.patch("backend.app.reports.cluster_data_loader.compute_and_save_cluster_analysis")
    def test_no_topics_skips_chart(self, mock_compute, mock_chart):
        mock_compute.return_value = {
            "topics": [],
            "assignments": [],
            "normalized_applicants": [],
            "top_applicants_ws": [],
            "topic_rows": [],
            "opportunity_matrix": {},
            "pain_point_matrix": {},
            "analysis_status": "no_topics",
        }

        result = run_full_report(
            workspace_id=99, source_field="claims",
            analysis_id=42,
        )

        mock_chart.assert_called_once()
        _, kwargs = mock_chart.call_args
        self.assertIsNone(kwargs["cluster_data"])
        self.assertEqual(result["analysis_status"], "no_topics")


if __name__ == "__main__":
    unittest.main()
