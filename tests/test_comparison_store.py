"""案件比對存取契約（拋棄式 DB patent_ppt_comparison，絕不碰 patent_ppt）。

沿用 test_workflow_repositories.py 的拋棄式 DB 模式：建庫 → alembic upgrade head。
比對案件走 app_layer.workflow_runs（run_type='comparison'），產出走
PostgresWorkflowOutputsRepository 版本化。驗證人工閘門 guard、AI 原稿與人工覆核分存不覆蓋、
四態/verdict 值寫入層再驗。case 由 create_case 建立，無需額外 fixture。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_comparison"
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
    """建拋棄式 DB → upgrade head；admin 不可用則整組 skip。"""
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


def _scalar(sql: str, params=()):
    with psycopg.connect(**_kw(TEST_DB)) as c:
        row = c.execute(sql, params).fetchone()
    return row[0] if row else None


def _store():
    from backend.app.comparison.comparison_store import ComparisonStore
    return ComparisonStore()


def _draft():
    return {"source_fields": ["所有權利要求"],
            "independent_claims": [{"claim_number": "1",
                                    "elements": [{"text": "一種裝置", "explanation": "整體"}]}],
            "dependent_claims": [], "key_terms": []}


def _element_analysis():
    return {"claims": [{"claim_number": "1", "elements": [
        {"element_id": "1a", "status": "met", "patent_evidence": "p", "product_evidence": "q",
         "explanation": "對應"}]}]}


def _verdict():
    return {"claims": [{"claim_number": "1", "status": "possibly_established", "inferred": False}]}


class ComparisonCaseTests(unittest.TestCase):
    """比對案件建立與產出版本化。"""

    def test_create_case_inserts_comparison_run(self):
        run_id = _store().create_case("TWI123456", "某電動工具，含底座與滑軌", "web-user")
        self.assertEqual(_scalar(
            "SELECT run_type FROM app_layer.workflow_runs WHERE run_id=%s", (run_id,)), "comparison")
        self.assertEqual(_scalar(
            "SELECT request_json->>'patent_number' FROM app_layer.workflow_runs WHERE run_id=%s",
            (run_id,)), "TWI123456")

    def test_understanding_versioned_not_overwritten(self):
        store = _store()
        run_id = store.create_case("P1", "target", "u")
        v1 = store.save_understanding(run_id, {"source_fields": ["所有權利要求"], "note": "v1"})
        v2 = store.save_understanding(run_id, {"source_fields": ["所有權利要求"], "note": "v2"})
        self.assertEqual((v1, v2), (1, 2))
        from backend.app.repositories.workflow_outputs_repository import PostgresWorkflowOutputsRepository
        repo = PostgresWorkflowOutputsRepository()
        self.assertEqual(repo.get_output(run_id, "understanding", version=1)["data_json"]["note"], "v1")
        self.assertEqual(repo.get_output(run_id, "understanding")["data_json"]["note"], "v2")


class HumanGateTests(unittest.TestCase):
    """人工理解閘門 guard。"""

    def test_element_analysis_without_approval_rejected(self):
        from backend.app.comparison.comparison_store import GateNotApprovedError
        store = _store()
        run_id = store.create_case("P2", "target", "u")
        store.save_understanding(run_id, _draft())
        with self.assertRaises(GateNotApprovedError):
            store.save_element_analysis(run_id, _element_analysis())
        self.assertEqual(_scalar(
            "SELECT count(*) FROM app_layer.workflow_outputs WHERE run_id=%s AND output_type='element_analysis'",
            (run_id,)), 0)

    def test_verdict_without_approval_rejected(self):
        from backend.app.comparison.comparison_store import GateNotApprovedError
        store = _store()
        run_id = store.create_case("P3", "target", "u")
        store.save_understanding(run_id, _draft())
        with self.assertRaises(GateNotApprovedError):
            store.save_verdict(run_id, _verdict())

    def test_approval_referencing_missing_version_rejected(self):
        from backend.app.comparison.comparison_store import GateNotApprovedError
        store = _store()
        run_id = store.create_case("P4", "target", "u")
        store.save_understanding(run_id, _draft())  # 只有 v1
        with self.assertRaises(GateNotApprovedError):
            store.approve_understanding(run_id, understanding_version=99, approved_by="rex")

    def test_approve_then_write_downstream_ok(self):
        store = _store()
        run_id = store.create_case("P5", "target", "u")
        uv = store.save_understanding(run_id, _draft())
        av = store.approve_understanding(run_id, understanding_version=uv, approved_by="rex")
        self.assertEqual((uv, av), (1, 1))
        ea = store.save_element_analysis(run_id, _element_analysis())
        vd = store.save_verdict(run_id, _verdict())
        self.assertEqual((ea, vd), (1, 1))
        # AI 原稿與人工覆核分存：兩 output_type 並存，understanding 未被覆蓋
        self.assertEqual(_scalar(
            "SELECT count(*) FROM app_layer.workflow_outputs WHERE run_id=%s AND output_type='understanding'",
            (run_id,)), 1)
        self.assertEqual(_scalar(
            "SELECT data_json->>'approved_by' FROM app_layer.workflow_outputs "
            "WHERE run_id=%s AND output_type='understanding_approval'", (run_id,)), "rex")


class IllustrationsTests(unittest.TestCase):
    """illustrations 為素材，不受 understanding_approval 閘門限制；版本化不覆蓋。"""

    def test_illustrations_versioned_without_gate(self):
        store = _store()
        run_id = store.create_case("P7", "target", "u")
        # 無核准也能寫（素材非判斷產出）
        v1 = store.save_illustrations(run_id, ["TWI123/aa/page_001.png"])
        v2 = store.save_illustrations(run_id, ["TWI123/aa/page_001.png", "TWI123/aa/page_002.png"])
        self.assertEqual((v1, v2), (1, 2))
        from backend.app.repositories.workflow_outputs_repository import PostgresWorkflowOutputsRepository
        repo = PostgresWorkflowOutputsRepository()
        self.assertEqual(repo.get_output(run_id, "illustrations", version=1)["data_json"]["figure_paths"],
                         ["TWI123/aa/page_001.png"])
        self.assertEqual(len(repo.get_output(run_id, "illustrations")["data_json"]["figure_paths"]), 2)


class WriteLayerValidationTests(unittest.TestCase):
    """縱深防禦：四態/verdict 值在寫入層再驗，非法拒寫。"""

    def _approved_case(self):
        store = _store()
        run_id = store.create_case("P6", "target", "u")
        uv = store.save_understanding(run_id, _draft())
        store.approve_understanding(run_id, understanding_version=uv, approved_by="rex")
        return store, run_id

    def test_illegal_element_status_rejected(self):
        from backend.app.comparison.verdict import VerdictError
        store, run_id = self._approved_case()
        bad = {"claims": [{"claim_number": "1", "elements": [{"element_id": "1a", "status": "maybe"}]}]}
        with self.assertRaises(VerdictError):
            store.save_element_analysis(run_id, bad)
        self.assertEqual(_scalar(
            "SELECT count(*) FROM app_layer.workflow_outputs WHERE run_id=%s AND output_type='element_analysis'",
            (run_id,)), 0)

    def test_illegal_claim_status_rejected(self):
        from backend.app.comparison.verdict import VerdictError
        store, run_id = self._approved_case()
        bad = {"claims": [{"claim_number": "1", "status": "definitely_infringes", "inferred": False}]}
        with self.assertRaises(VerdictError):
            store.save_verdict(run_id, bad)


if __name__ == "__main__":
    unittest.main()
