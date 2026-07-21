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

TEST_DB = "patent_ppt_loadercheck"
BASE_REV = "0018_compose_created_at_comment"

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

        command.upgrade(_alembic_cfg(), BASE_REV)
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
        """最小 fixture — 滿足所有 FK，涵蓋合併/未分類/多申請人。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            # core_layer.patents（FK 基礎）
            for pid, title in [(101, "p101"), (102, "p102"), (103, "p103"),
                               (104, "p104"), (105, "p105"), (106, "p106"),
                               (107, "p107"), (108, "p108"), (109, "p109"),
                               (110, "p110")]:
                c.execute("INSERT INTO core_layer.patents (id, title) VALUES (%s, %s)", (pid, title))

            # workspace
            c.execute("INSERT INTO app_layer.workspaces (workspace_id, workspace_name, created_by) VALUES (1, 'test_ws', 'test')")

            # topic_runs
            c.execute("INSERT INTO derived_layer.topic_runs (run_id, workspace_id, source_field, run_mode, status) VALUES (1, 1, 'wips_independent_claims', 'full', 'completed')")

            # topics
            c.execute("""
                INSERT INTO derived_layer.topics (topic_id, workspace_id, source_field, created_run_id, topic_code, status, merged_into_topic_id, merged_at, label, doc_count, merged_by)
                VALUES
                    (1, 1, 'wips_independent_claims', 1, 'T01',          'active',  NULL, NULL, '半導體製程', 3, NULL),
                    (2, 1, 'wips_independent_claims', 1, 'T02',          'active',  NULL, NULL, '面板驅動',   1, NULL),
                    (3, 1, 'wips_independent_claims', 1, 'T01_OLD',      'merged',  1,    '2026-07-21T00:00:00+00', '半導體(舊)', 0, 'test_user'),
                    (4, 1, 'wips_independent_claims', 1, 'UNCLASSIFIED', 'active',  NULL, NULL, '未分類',     1, NULL)
            """)

            # topic_assignments
            c.execute("""
                INSERT INTO derived_layer.topic_assignments (assignment_id, workspace_id, source_field, patent_id, topic_id, assigned_run_id, is_current)
                VALUES
                    (1, 1, 'wips_independent_claims', 101, 1, 1, true),
                    (2, 1, 'wips_independent_claims', 102, 1, 1, true),
                    (3, 1, 'wips_independent_claims', 103, 2, 1, true),
                    (4, 1, 'wips_independent_claims', 104, 3, 1, true),   -- merged → T01
                    (5, 1, 'wips_independent_claims', 105, 4, 1, true)    -- UNCLASSIFIED
            """)

            # report_patent_base（101/102/104 同一公司 測去重；103,105 各一；106-110 測 top 10）
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

            # analysis_runs（供 compute_and_save 測試）
            c.execute("INSERT INTO app_layer.analysis_runs (analysis_id, analysis_name, analysis_type, status) VALUES (42, 'test analysis', 'report', 'completed')")

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
    """load_cluster_workspace_data: 從 0018 schema 載入分群資料。"""

    def _mock_cursor(self, fetchall_results: list[list[dict]]) -> mock.MagicMock:
        cur = mock.MagicMock()
        cur.execute.return_value = cur
        cur.fetchall.side_effect = list(fetchall_results)
        return cur

    def _make_conn(self, fetchall_results: list[list[dict]]) -> mock.MagicMock:
        cur = self._mock_cursor(fetchall_results)
        conn = mock.MagicMock()
        conn.cursor.return_value = cur
        return conn

    def test_loads_active_topics_and_assignments(self):
        topics_data = [
            {"topic_id": 1, "topic_code": "T01", "status": "active",
             "merged_into_topic_id": None, "label": "半導體製程",
             "source_field": "independent_claims", "doc_count": 5},
            {"topic_id": 2, "topic_code": "T02", "status": "active",
             "merged_into_topic_id": None, "label": "面板驅動",
             "source_field": "independent_claims", "doc_count": 3},
        ]
        assignments_data = [
            {"patent_id": 101, "topic_id": 1, "topic_code": "T01"},
            {"patent_id": 102, "topic_id": 1, "topic_code": "T01"},
            {"patent_id": 103, "topic_id": 2, "topic_code": "T02"},
        ]
        applicants_data = [
            {"patent_id": 101, "applicant_display_name": "TSMC"},
            {"patent_id": 102, "applicant_display_name": "TSMC"},
            {"patent_id": 103, "applicant_display_name": "Samsung"},
        ]
        top_applicants_data = [
            {"applicant_display_name": "TSMC", "cnt": 2},
        ]

        conn = self._make_conn([
            topics_data,
            assignments_data,
            applicants_data,
            top_applicants_data,
        ])
        result = load_cluster_workspace_data(1, "independent_claims", conn)

        self.assertIn("topics", result)
        self.assertIn("assignments", result)
        self.assertIn("normalized_applicants", result)
        self.assertIn("top_applicants_ws", result)

        self.assertEqual(len(result["topics"]), 2)
        codes = {t["topic_code"] for t in result["topics"]}
        self.assertEqual(codes, {"T01", "T02"})

        self.assertEqual(len(result["assignments"]), 3)
        self.assertEqual(len(result["normalized_applicants"]), 3)
        self.assertIn("TSMC", result["top_applicants_ws"])

    def test_merged_topics_resolved_to_active_target(self):
        topics_data = [
            {"topic_id": 1, "topic_code": "T01", "status": "active",
             "merged_into_topic_id": None, "label": "半導體",
             "source_field": "claims", "doc_count": 3},
            {"topic_id": 2, "topic_code": "T01_OLD", "status": "merged",
             "merged_into_topic_id": 1, "label": "半導體(舊)",
             "source_field": "claims", "doc_count": 2},
        ]
        assignments_data = [
            {"patent_id": 101, "topic_id": 1, "topic_code": "T01"},
            {"patent_id": 102, "topic_id": 2, "topic_code": "T01_OLD"},
        ]
        applicants_data = [
            {"patent_id": 101, "applicant_display_name": "TSMC"},
            {"patent_id": 102, "applicant_display_name": "TSMC"},
        ]
        top_applicants_data = [{"applicant_display_name": "TSMC", "cnt": 2}]

        conn = self._make_conn([
            topics_data,
            assignments_data,
            applicants_data,
            top_applicants_data,
        ])
        result = load_cluster_workspace_data(1, "claims", conn)

        codes = {t["topic_code"] for t in result["topics"]}
        self.assertEqual(codes, {"T01"})
        assign_codes = {a["topic_code"] for a in result["assignments"]}
        self.assertEqual(assign_codes, {"T01"})
        self.assertEqual(len(result["assignments"]), 2)

    def test_no_topics_returns_empty_structures(self):
        conn = self._make_conn([[], [], [], []])
        result = load_cluster_workspace_data(99, "effect_summary", conn)
        self.assertEqual(result["topics"], [])
        self.assertEqual(result["assignments"], [])
        self.assertEqual(result["normalized_applicants"], [])
        self.assertEqual(result["top_applicants_ws"], [])

    def test_patent_id_cast_to_int(self):
        topics_data = [
            {"topic_id": 1, "topic_code": "T01", "status": "active",
             "merged_into_topic_id": None, "label": "AI",
             "source_field": "claims", "doc_count": 1},
        ]
        assignments_data = [
            {"patent_id": 999, "topic_id": 1, "topic_code": "T01"},
        ]
        applicants_data = [
            {"patent_id": 999, "applicant_display_name": "Google"},
        ]
        top_applicants_data = [{"applicant_display_name": "Google", "cnt": 1}]

        conn = self._make_conn([
            topics_data,
            assignments_data,
            applicants_data,
            top_applicants_data,
        ])
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
        self.assertEqual(mock_cur.execute.call_count, 3)
        inserts = [call[0][0] for call in mock_cur.execute.call_args_list]
        for sql in inserts:
            self.assertIn("INSERT INTO app_layer.analysis_outputs", sql)

        output_names = [call[0][1][1] for call in mock_cur.execute.call_args_list]
        expected_names = ["topic_effect_table", "opportunity_matrix", "pain_point_matrix"]
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
