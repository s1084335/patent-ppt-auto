"""MCP 批次一工具契約：save_workflow_output／refresh_derived_data。

⚠ 2026-08-13：原第三顆 generate_report_ppt 已隨 PPT 交付線刪除，相關三支測試同步退場
（見下方註解）。

單元測試以 mock 取代被包裝的既有函式，驗 guard、scope 路由與回傳；DB 測試走拋棄式
0021 庫（save 只碰 app_layer，安全）；refresh 因 0021 後 report_* 已為 VIEW、既有
refresh 函式寫 legacy_0021 實體表，故其真跑 DB 測試以 RUN_DB_TESTS 閘控（屬另案 consolidation）。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

import psycopg
from alembic import command
from alembic.config import Config

from backend.app.mcp_server import tools_reporting

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.getenv("PGPASSWORD")
    return kw


class SaveWorkflowOutputUnitTests(unittest.TestCase):
    """save_workflow_output：guard（空／'ai:' 前綴）＋ artifact_key 路由（純 mock）。"""

    def _patch_repo(self):
        return mock.patch(
            "backend.app.repositories.workflow_outputs_repository.PostgresWorkflowOutputsRepository")

    def test_rejects_ai_prefix(self):
        with self.assertRaises(ValueError):
            tools_reporting.save_workflow_output(1, "ai:narrative:x", {"text": "a"})

    def test_rejects_empty_output_type(self):
        with self.assertRaises(ValueError):
            tools_reporting.save_workflow_output(1, "  ", {"rows": [1]})

    def test_plain_output_routes_to_append_output(self):
        with self._patch_repo() as repo_cls:
            repo_cls.return_value.append_output.return_value = 3
            r = tools_reporting.save_workflow_output(7, "chart:applicant_ranking", {"rows": [1]})
        repo_cls.return_value.append_output.assert_called_once_with(7, "chart:applicant_ranking", {"rows": [1]})
        repo_cls.return_value.append_artifact_output.assert_not_called()
        self.assertEqual(r["version"], 3)

    def test_artifact_payload_routes_to_append_artifact_output(self):
        manifest = {"artifact_key": "ppt/report.pptx", "sha256": "abc"}
        with self._patch_repo() as repo_cls:
            repo_cls.return_value.append_artifact_output.return_value = 1
            r = tools_reporting.save_workflow_output(7, "artifact:report_ppt", manifest)
        repo_cls.return_value.append_artifact_output.assert_called_once_with(7, "artifact:report_ppt", manifest)
        self.assertEqual(r["version"], 1)


class RefreshDerivedDataUnitTests(unittest.TestCase):
    """refresh_derived_data：scope 白名單＋路由（mock 既有 refresh 函式，驗呼叫組合與順序）。"""

    def _patch(self):
        base = mock.patch("backend.app.derived.refresh_report_patent_base.refresh_report_patent_base",
                          return_value={"status": "refreshed", "report_patent_base_rows": 5})
        fam = mock.patch("backend.app.derived.refresh_report_family_country.refresh_report_family_country",
                         return_value={"status": "refreshed", "rows": 9})
        return base, fam

    def test_rejects_unknown_scope(self):
        with self.assertRaises(ValueError):
            tools_reporting.refresh_derived_data("everything")

    def test_aliases_runs_base_only(self):
        base, fam = self._patch()
        with base as b, fam as f:
            r = tools_reporting.refresh_derived_data("aliases")
        b.assert_called_once()
        f.assert_not_called()
        self.assertEqual([s["step"] for s in r["steps"]], ["report_patent_base"])
        self.assertIn("elapsed_ms", r["steps"][0])

    def test_report_views_runs_family_only(self):
        base, fam = self._patch()
        with base as b, fam as f:
            r = tools_reporting.refresh_derived_data("report_views")
        b.assert_not_called()
        f.assert_called_once()
        self.assertEqual([s["step"] for s in r["steps"]], ["report_family_country"])

    def test_all_runs_both_in_order(self):
        base, fam = self._patch()
        with base as b, fam as f:
            r = tools_reporting.refresh_derived_data("all")
        b.assert_called_once()
        f.assert_called_once()
        self.assertEqual([s["step"] for s in r["steps"]],
                         ["report_patent_base", "report_family_country"])


# ⚠ 2026-08-13 移除 GenerateReportPptUnitTests 與 Batch1DbTests 的同名測試：
# 受測工具 generate_report_ppt 已刪除（PPT 交付線 2026-08-10 退場）。它「回報成功
# 卻排了一筆重產全部報表的 job」，而這兩支測試守的正是那個錯誤契約——payload 帶
# version／artifact 兩鍵，兩鍵下游都不消費。守著它等於把錯誤行為當成規格保護起來。


class Batch1DbTests(unittest.TestCase):
    """拋棄式 0021 庫：save_workflow_output 真 append（只碰 app_layer）。"""

    TEST_DB = "patent_ppt_mcpbatch1"
    _prev: dict[str, str | None] = {}

    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{cls.TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{cls.TEST_DB}"')
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = cls.TEST_DB
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        command.upgrade(cfg, "head")
        with psycopg.connect(**_kw(cls.TEST_DB)) as c:
            cls.run_id = c.execute(
                "INSERT INTO app_layer.workflow_runs (run_type, status, request_json) "
                "VALUES ('report_generate', 'succeeded', '{}'::jsonb) RETURNING run_id"
            ).fetchone()[0]
            c.commit()
        # get_pool 單例：確保連到本拋棄式庫（create_job 走 pool）。
        from backend.app.db import connection
        connection._pool = None

    @classmethod
    def tearDownClass(cls):
        from backend.app.db import connection
        connection._pool = None
        for k, v in cls._prev.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{cls.TEST_DB}" WITH (FORCE)')
        except Exception:  # noqa: BLE001
            pass

    def test_save_workflow_output_appends_versioned(self):
        r1 = tools_reporting.save_workflow_output(self.run_id, "chart:applicant_ranking", {"rows": [1]})
        r2 = tools_reporting.save_workflow_output(self.run_id, "chart:applicant_ranking", {"rows": [2]})
        self.assertEqual((r1["version"], r2["version"]), (1, 2))  # append-only 不覆蓋
        with psycopg.connect(**_kw(self.TEST_DB)) as c:
            n = c.execute(
                "SELECT count(*) FROM app_layer.workflow_outputs "
                "WHERE run_id=%s AND output_type='chart:applicant_ranking'", (self.run_id,)
            ).fetchone()[0]
        self.assertEqual(n, 2)

    def test_save_workflow_output_rejects_ai_prefix_before_db(self):
        with self.assertRaises(ValueError):
            tools_reporting.save_workflow_output(self.run_id, "ai:narrative:x", {"text": "a"})


@unittest.skipUnless(os.environ.get("RUN_DB_TESTS") == "1",
                     "refresh 寫 legacy_0021 實體表；0021 後 report_* 為 VIEW，真跑另案，RUN_DB_TESTS 閘控")
class RefreshDerivedDataDbTests(unittest.TestCase):
    """refresh_derived_data 真跑（gated）：0021 consolidation 完成後再開。"""

    def test_all_scope_runs(self):  # pragma: no cover - gated
        result = tools_reporting.refresh_derived_data("all")
        self.assertEqual(result["scope"], "all")
        self.assertEqual(len(result["steps"]), 2)


if __name__ == "__main__":
    unittest.main()
