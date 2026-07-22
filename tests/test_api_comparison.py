"""Contract tests for the case comparison API.

These tests cover job creation, lookup, target saving, understanding saving, and approval.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from uuid import uuid4

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.comparison.comparison_store import ComparisonStore
from backend.app.db import job_repository as jr


PREFIX = "/api/v1/comparisons"
TEST_DB = "patent_ppt_apicomparison"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

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
    from backend.app.db import connection
    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:
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
    except Exception as exc:
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB
    _reset_pool()

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    _seed_subject_patents()


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
    except Exception:
        pass


def _cleanup():
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        conn.execute(
            "DELETE FROM app_layer.workflow_runs WHERE request_json ? %s",
            ("_verify_marker_comparison",),
        )
        conn.commit()


# subject library 模式需要既有庫專利：灌少量可控 fixture（避開正式資料範圍）。
SUBJECT_PIDS = [930001, 930002, 930003]


def _seed_subject_patents():
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        for i, pid in enumerate(SUBJECT_PIDS):
            conn.execute(
                'INSERT INTO core_layer.patents (id, title, country_code, "授權公告號") '
                "VALUES (%s, %s, 'US', %s) ON CONFLICT (id) DO NOTHING",
                (pid, f"subject fixture {i}", f"US9300{pid}B"),
            )
        conn.commit()


class CreateCaseComparisonTests(unittest.TestCase):

    def tearDown(self):
        _cleanup()

    def test_create_case_comparison_returns_202(self):
        resp = client.post(
            PREFIX,
            json={
                "workspace_id": 1,
                "case_title": "US12345678 vs Product X",
                "case_text": "Claim 1: A device comprising...",
                "comparison_type": "claim_or_technical",
                "idempotency_key": "key-001",
            },
        )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertIn("job_id", body)
        self.assertEqual(body["job_type"], "case_comparison")
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["workspace_id"], 1)
        self.assertIn("current_stage", body)
        self.assertIn("progress_percent", body)
        self.assertEqual(body["current_stage"], "queued")
        self.assertEqual(body["progress_percent"], 0)

    def test_create_case_missing_fields_returns_422(self):
        resp = client.post(PREFIX, json={"case_title": "test"})
        self.assertEqual(resp.status_code, 422)

    def test_create_case_invalid_comparison_type_returns_422(self):
        resp = client.post(
            PREFIX,
            json={
                "workspace_id": 1,
                "case_title": "test",
                "case_text": "text",
                "comparison_type": "invalid",
                "idempotency_key": "key-002",
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_create_case_idempotent(self):
        body = {
            "workspace_id": 2,
            "case_title": "EP123 vs Product Y",
            "case_text": "Claim text...",
            "comparison_type": "claim_or_technical",
            "idempotency_key": "dup-key",
        }
        resp1 = client.post(PREFIX, json=body)
        self.assertEqual(resp1.status_code, 202)
        resp2 = client.post(PREFIX, json=body)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp1.json()["job_id"], resp2.json()["job_id"])
        self.assertIn("current_stage", resp2.json())
        self.assertIn("progress_percent", resp2.json())


class GetCaseComparisonTests(unittest.TestCase):

    def test_get_case_comparison_not_found_returns_404(self):
        resp = client.get(f"{PREFIX}/999999")
        self.assertEqual(resp.status_code, 404)
        detail = resp.json()["detail"]
        self.assertNotEqual(detail, "Not Found")

    def test_get_case_comparison_returns_job(self):
        create_resp = client.post(
            PREFIX,
            json={
                "workspace_id": 1,
                "case_title": "US1 vs Product Z",
                "case_text": "Claim 1...",
                "comparison_type": "claim_or_technical",
                "idempotency_key": "get-test-key",
            },
        )
        self.assertEqual(create_resp.status_code, 202)
        job_id = create_resp.json()["job_id"]

        resp = client.get(f"{PREFIX}/{job_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_id"], job_id)
        self.assertEqual(body["job_type"], "case_comparison")
        self.assertIn("current_stage", body)
        self.assertIn("progress_percent", body)
        self.assertIsNone(body.get("result"))

    def test_get_case_comparison_rejects_non_comparison_job(self):
        # Create a non-comparison job.。
        job = jr.create_job("clustering_calibrate", {"_verify": True})
        resp = client.get(f"{PREFIX}/{job.job_id}")
        self.assertEqual(resp.status_code, 404)

    def test_get_case_comparison_non_int_id_returns_422(self):
        resp = client.get(f"{PREFIX}/abc")
        self.assertEqual(resp.status_code, 422)



class SaveTargetTests(unittest.TestCase):

    PREFIX = PREFIX

    def setUp(self):
        resp = client.post(
            self.PREFIX,
            json={
                "workspace_id": 1,
                "case_title": "SaveTarget case",
                "case_text": "SaveTarget text",
                "comparison_type": "claim_or_technical",
                "idempotency_key": f"save-target-{uuid4().hex[:8]}",
            },
        )
        self.assertEqual(resp.status_code, 202)
        self.job_id = resp.json()["job_id"]

    def tearDown(self):
        _cleanup()

    def test_save_target_valid(self):
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/target",
            json={
                "target_type": "text",
                "title": "Product X",
                "description": "Product X is a widget",
                "simulated": True,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_id"], self.job_id)
        self.assertEqual(body["output_type"], "target")
        self.assertIsInstance(body["version"], int)
        self.assertGreaterEqual(body["version"], 1)

    def test_save_target_missing_simulated_returns_422(self):
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/target",
            json={"target_type": "text", "title": "X", "description": "Y"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_save_target_job_not_found_returns_404(self):
        resp = client.post(
            f"{self.PREFIX}/999999/target",
            json={"target_type": "text", "title": "X", "description": "Y", "simulated": True},
        )
        self.assertEqual(resp.status_code, 404)

    def test_save_target_non_comparison_job_returns_404(self):
        non_comp = jr.create_job("clustering_calibrate", {"_verify": True})
        resp = client.post(
            f"{self.PREFIX}/{non_comp.job_id}/target",
            json={"target_type": "text", "title": "X", "description": "Y", "simulated": True},
        )
        self.assertEqual(resp.status_code, 404)


class SaveUnderstandingTests(unittest.TestCase):

    PREFIX = PREFIX

    def setUp(self):
        resp = client.post(
            self.PREFIX,
            json={
                "workspace_id": 1,
                "case_title": "SaveUnderstanding case",
                "case_text": "SaveUnderstanding text",
                "comparison_type": "claim_or_technical",
                "idempotency_key": f"save-understanding-{uuid4().hex[:8]}",
            },
        )
        self.assertEqual(resp.status_code, 202)
        self.job_id = resp.json()["job_id"]

    def tearDown(self):
        _cleanup()

    def test_save_understanding_valid(self):
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/understanding",
            json={
                "features": ["feature A", "feature B"],
                "assumptions": ["assumption 1"],
                "source": "claude_cli",
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_id"], self.job_id)
        self.assertEqual(body["output_type"], "understanding")
        self.assertIsInstance(body["version"], int)
        self.assertGreaterEqual(body["version"], 1)

    def test_save_understanding_non_comparison_job_returns_404(self):
        non_comp = jr.create_job("clustering_calibrate", {"_verify": True})
        resp = client.post(
            f"{self.PREFIX}/{non_comp.job_id}/understanding",
            json={"features": ["A"], "assumptions": ["B"], "source": "manual"},
        )
        self.assertEqual(resp.status_code, 404)


class ApproveUnderstandingTests(unittest.TestCase):

    PREFIX = PREFIX

    def setUp(self):
        resp = client.post(
            self.PREFIX,
            json={
                "workspace_id": 1,
                "case_title": "ApproveUnderstanding case",
                "case_text": "ApproveUnderstanding text",
                "comparison_type": "claim_or_technical",
                "idempotency_key": f"approve-{uuid4().hex[:8]}",
            },
        )
        self.assertEqual(resp.status_code, 202)
        self.job_id = resp.json()["job_id"]
        store = ComparisonStore()
        self.understanding_version = store.save_understanding(self.job_id, {
            "features": ["feat 1"],
            "assumptions": ["assum 1"],
            "source": "manual",
        })

    def tearDown(self):
        _cleanup()

    def test_approve_understanding_valid(self):
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/understanding/approve",
            json={
                "understanding_version": self.understanding_version,
                "approved_by": "web-user",
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_id"], self.job_id)
        self.assertEqual(body["output_type"], "understanding_approval")
        self.assertIsInstance(body["version"], int)
        self.assertGreaterEqual(body["version"], 1)

    def test_approve_understanding_version_not_found_returns_409(self):
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/understanding/approve",
            json={"understanding_version": 9999, "approved_by": "web-user"},
        )
        self.assertEqual(resp.status_code, 409)

    def test_approve_understanding_missing_approved_by_returns_422(self):
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/understanding/approve",
            json={"understanding_version": self.understanding_version},
        )
        self.assertEqual(resp.status_code, 422)

    def test_approve_understanding_non_comparison_job_returns_404(self):
        non_comp = jr.create_job("clustering_calibrate", {"_verify": True})
        resp = client.post(
            f"{self.PREFIX}/{non_comp.job_id}/understanding/approve",
            json={"understanding_version": 1, "approved_by": "web-user"},
        )
        self.assertEqual(resp.status_code, 404)



class ElementAnalysisTests(unittest.TestCase):

    PREFIX = PREFIX

    def setUp(self):
        resp = client.post(
            self.PREFIX,
            json={
                "workspace_id": 1,
                "case_title": "ElementAnalysis case",
                "case_text": "Element analysis text",
                "comparison_type": "claim_or_technical",
                "idempotency_key": f"ea-{uuid4().hex[:8]}",
            },
        )
        self.assertEqual(resp.status_code, 202)
        self.job_id = resp.json()["job_id"]
        store = ComparisonStore()
        uv = store.save_understanding(self.job_id, {
            "features": ["motor control"],
            "assumptions": ["assum 1"],
            "source": "manual",
        })
        store.approve_understanding(self.job_id, understanding_version=uv, approved_by="web-user")

    def tearDown(self):
        _cleanup()

    def _valid_payload(self):
        return {
            "claims": [{
                "claim_number": "1",
                "elements": [{
                    "element_id": "1a",
                    "element_text": "a controller connected to a motor",
                    "status": "met",
                    "patent_evidence": "Claim 1 recites a controller",
                    "product_evidence": "Product X has a MCU",
                    "notes": "sample matched element",
                }],
            }],
        }

    def test_save_element_analysis_valid(self):
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/element-analysis",
            json=self._valid_payload(),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_id"], self.job_id)
        self.assertEqual(body["output_type"], "element_analysis")
        self.assertIsInstance(body["version"], int)
        self.assertGreaterEqual(body["version"], 1)

    def test_save_element_analysis_without_gate_returns_409(self):
        resp = client.post(
            self.PREFIX,
            json={
                "workspace_id": 1,
                "case_title": "NoGate case",
                "case_text": "No gate text",
                "comparison_type": "claim_or_technical",
                "idempotency_key": f"ea-nogate-{uuid4().hex[:8]}",
            },
        )
        self.assertEqual(resp.status_code, 202)
        no_gate_id = resp.json()["job_id"]
        store = ComparisonStore()
        store.save_understanding(no_gate_id, {"features": ["f1"], "source": "manual"})
        resp = client.post(
            f"{self.PREFIX}/{no_gate_id}/element-analysis",
            json=self._valid_payload(),
        )
        self.assertEqual(resp.status_code, 409)

    def test_save_element_analysis_invalid_status_returns_422(self):
        bad = {"claims": [{"claim_number": "1", "elements": [
            {"element_id": "1a", "status": "maybe"}]}]}
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/element-analysis",
            json=bad,
        )
        self.assertEqual(resp.status_code, 422)

    def test_save_element_analysis_non_comparison_job_returns_404(self):
        non_comp = jr.create_job("clustering_calibrate", {"_verify": True})
        resp = client.post(
            f"{self.PREFIX}/{non_comp.job_id}/element-analysis",
            json=self._valid_payload(),
        )
        self.assertEqual(resp.status_code, 404)

    def test_get_comparison_includes_element_analysis(self):
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/element-analysis",
            json=self._valid_payload(),
        )
        self.assertEqual(resp.status_code, 200)
        ea_version = resp.json()["version"]

        resp = client.get(f"{self.PREFIX}/{self.job_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("element_analysis_version", body)
        self.assertEqual(body["element_analysis_version"], ea_version)
        self.assertIn("element_analysis", body)
        self.assertEqual(body["element_analysis"]["claims"][0]["claim_number"], "1")


class SetSubjectLibraryTests(unittest.TestCase):
    """被比對來源 · library 模式：既有庫專利號選取綁定。"""

    PREFIX = PREFIX

    def setUp(self):
        resp = client.post(
            self.PREFIX,
            json={
                "workspace_id": 1,
                "case_title": "Subject library case",
                "case_text": "text",
                "comparison_type": "claim_or_technical",
                "idempotency_key": f"subj-lib-{uuid4().hex[:8]}",
            },
        )
        self.assertEqual(resp.status_code, 202)
        self.job_id = resp.json()["job_id"]

    def tearDown(self):
        _cleanup()

    def test_bind_library_valid(self):
        """綁定既有庫專利集合：回 subject 版本與去重後的 bound_patent_ids。"""
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/subject",
            json={"mode": "library", "patent_ids": [SUBJECT_PIDS[0], SUBJECT_PIDS[1]]},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["output_type"], "subject")
        self.assertEqual(body["mode"], "library")
        self.assertGreaterEqual(body["version"], 1)
        self.assertEqual(sorted(body["bound_patent_ids"]), sorted([SUBJECT_PIDS[0], SUBJECT_PIDS[1]]))

    def test_bind_library_dedup(self):
        """重複 patent_id 去重後綁定集合唯一。"""
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/subject",
            json={"mode": "library", "patent_ids": [SUBJECT_PIDS[0], SUBJECT_PIDS[0], SUBJECT_PIDS[1]]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["bound_patent_ids"]), 2)

    def test_bind_library_missing_patent_422(self):
        """有 patent_id 不存在於庫 → 422（列出缺的）。"""
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/subject",
            json={"mode": "library", "patent_ids": [SUBJECT_PIDS[0], 999_999_999]},
        )
        self.assertEqual(resp.status_code, 422)

    def test_bind_library_empty_422(self):
        """空集合（去重後為空）→ 422。"""
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/subject",
            json={"mode": "library", "patent_ids": []},
        )
        self.assertEqual(resp.status_code, 422)

    def test_bind_library_bad_type_422(self):
        """patent_ids 非 int 陣列 → 422。"""
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/subject",
            json={"mode": "library", "patent_ids": ["a", "b"]},
        )
        self.assertEqual(resp.status_code, 422)

    def test_invalid_mode_422(self):
        """非法 mode → 422。"""
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/subject",
            json={"mode": "bogus", "patent_ids": [SUBJECT_PIDS[0]]},
        )
        self.assertEqual(resp.status_code, 422)

    def test_subject_job_not_found_404(self):
        """job 不存在 → 404。"""
        resp = client.post(
            f"{self.PREFIX}/999999/subject",
            json={"mode": "library", "patent_ids": [SUBJECT_PIDS[0]]},
        )
        self.assertEqual(resp.status_code, 404)

    def test_subject_readable_via_store(self):
        """綁定後 get_latest_subject 取回被比對 patent_ids（供輪3 相似搜尋取用）。"""
        client.post(
            f"{self.PREFIX}/{self.job_id}/subject",
            json={"mode": "library", "patent_ids": [SUBJECT_PIDS[2]]},
        )
        subject = ComparisonStore().get_latest_subject(self.job_id)
        self.assertIsNotNone(subject)
        self.assertEqual(subject["data"]["mode"], "library")
        self.assertEqual(subject["data"]["patent_ids"], [SUBJECT_PIDS[2]])


class SetSubjectImportTests(unittest.TestCase):
    """被比對來源 · import 模式：綁定既有輪1 匯入 job（purpose=case_comparison）。"""

    PREFIX = PREFIX

    def setUp(self):
        resp = client.post(
            self.PREFIX,
            json={
                "workspace_id": 1,
                "case_title": "Subject import case",
                "case_text": "text",
                "comparison_type": "claim_or_technical",
                "idempotency_key": f"subj-imp-{uuid4().hex[:8]}",
            },
        )
        self.assertEqual(resp.status_code, 202)
        self.job_id = resp.json()["job_id"]

    def tearDown(self):
        _cleanup()

    def test_bind_import_valid(self):
        """綁定 case_comparison patent_import job 為被比對來源。"""
        import_job = jr.create_job(
            "patent_import",
            {"path": "x", "file_hash": "h", "purpose": "case_comparison"},
        )
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/subject",
            json={"mode": "import", "import_job_id": import_job.job_id},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["mode"], "import")
        self.assertEqual(body["import_job_id"], import_job.job_id)
        self.assertGreaterEqual(body["version"], 1)

    def test_bind_import_wrong_purpose_422(self):
        """引用的匯入 job 非 case_comparison 用途 → 422。"""
        import_job = jr.create_job(
            "patent_import",
            {"path": "x", "file_hash": "h", "purpose": "general"},
        )
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/subject",
            json={"mode": "import", "import_job_id": import_job.job_id},
        )
        self.assertEqual(resp.status_code, 422)

    def test_bind_import_non_import_job_422(self):
        """import_job_id 指向非 patent_import job → 422。"""
        other = jr.create_job("clustering_calibrate", {"_verify": True})
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/subject",
            json={"mode": "import", "import_job_id": other.job_id},
        )
        self.assertEqual(resp.status_code, 422)

    def test_bind_import_missing_id_422(self):
        """缺 import_job_id → 422。"""
        resp = client.post(
            f"{self.PREFIX}/{self.job_id}/subject",
            json={"mode": "import"},
        )
        self.assertEqual(resp.status_code, 422)


class EmbeddingsEnqueueOnCaseComparisonImportTests(unittest.TestCase):
    """案件比對匯入後觸發 embeddings：驗 handler enqueue 既有 embeddings job（gated，不真算）。"""

    def test_case_comparison_import_enqueues_embeddings(self):
        """purpose=case_comparison 且有 patent_ids 時，匯入 handler enqueue 一個 embeddings job。"""
        from contextlib import contextmanager
        from unittest import mock

        from backend.app.worker import handlers

        # mock 掉檔案驗證、實際匯入與 workspace 圈選，只驗案件比對匯入完成後有 enqueue embeddings。
        fake_ctx = mock.MagicMock()

        @contextmanager
        def _noop_keepalive(*_args, **_kwargs):
            yield

        fake_ctx.keepalive.side_effect = _noop_keepalive
        # 用真實 .xlsx 副檔名路徑通過白名單檢查（不需 mock Path.suffix）。
        payload = {
            "path": "imports/uuid/file.xlsx", "file_hash": "h",
            "purpose": "case_comparison", "new_workspace_name": "cc-ws",
        }
        with mock.patch.object(handlers, "is_within_imports_root", return_value=True), \
             mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch.object(handlers, "file_sha256", return_value="h"), \
             mock.patch.object(handlers, "import_wips_file",
                               return_value={"status": "ok", "patent_ids": [SUBJECT_PIDS[0]]}), \
             mock.patch.object(handlers, "_attach_import_workspace", return_value=None), \
             mock.patch.object(handlers, "_enqueue_case_comparison_embeddings") as enqueue_mock:
            enqueue_mock.return_value = mock.MagicMock(job_id=4242)
            summary = handlers.handle_patent_import(payload, fake_ctx)
        enqueue_mock.assert_called_once()
        self.assertEqual(summary["embeddings_job_id"], 4242)

    def test_embeddings_job_type_registered(self):
        """embeddings 為合法 job_type 且有對應 handler。"""
        from backend.app.worker import handlers

        self.assertIn("embeddings", jr.JOB_TYPES)
        self.assertIn("embeddings", handlers.HANDLERS)


if __name__ == "__main__":
    unittest.main()
