"""TW legal_status curation OpenSpec contract tests."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.app.mappings import legal_status

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 2026-08-07 定版＋🔴 2026-08-11 修訂：「無效」移除——使用者要求複驗後，
# data/raw 全部 WIPS 檔（10 檔）實掃 `状态` 欄查無此值、人工登錄亦未用過（自加值）。
# 現值域＝實測 9 個 WIPS 本體詞＋「公開」（TW 人工登錄需要，原始欄為空）。
CURATED_TW_STATUSES = (
    "\u7533\u8acb",
    "\u516c\u958b",
    "\u5be9\u67e5\u4e2d",
    "\u5373\u5c07\u6388\u6b0a",
    "\u6388\u6b0a",
    "\u653e\u68c4",
    "\u64a4\u56de",
    "\u62d2\u7d55",
    "\u522a\u9664",
    "\u5230\u671f",
)


class TwLegalStatusMappingContractTests(unittest.TestCase):
    def test_allowed_statuses_are_the_curated_nine_values(self) -> None:
        self.assertEqual(legal_status.TW_LEGAL_STATUS_ALLOWED, CURATED_TW_STATUSES)

    def test_status_analysis_mapping_uses_four_buckets(self) -> None:
        expected = {
            "\u7533\u8acb": "pending",
            "\u516c\u958b": "pending",
            "\u5be9\u67e5\u4e2d": "pending",
            "\u5373\u5c07\u6388\u6b0a": "pending",
            "\u6388\u6b0a": "alive",
            "\u653e\u68c4": "dead",
            "\u64a4\u56de": "dead",
            "\u62d2\u7d55": "dead",
            "\u522a\u9664": "dead",
            "\u5230\u671f": "dead",
        }
        self.assertEqual(legal_status.TW_LEGAL_STATUS_ANALYSIS_MAP, expected)
        self.assertEqual(legal_status.normalize_tw_legal_status_for_analysis(None), "unknown")
        self.assertEqual(legal_status.normalize_tw_legal_status_for_analysis("  "), "unknown")
        self.assertEqual(legal_status.normalize_tw_legal_status_for_analysis("unknown-status"), "unknown")

    def test_frontend_does_not_own_a_second_status_list(self) -> None:
        html = (PROJECT_ROOT / "backend" / "app" / "static" / "index.html").read_text(encoding="utf-8")
        status_panel = html.split("function renderTwLegalStatusPending", 1)[-1].split(
            "async function saveTwLegalStatus",
            1,
        )[0]
        self.assertIn("data.allowed_statuses", status_panel)
        self.assertNotIn("CURATED_TW_STATUSES", status_panel)


class TwLegalStatusApiStaticContractTests(unittest.TestCase):
    def test_patent_router_exposes_pending_save_and_retry_endpoints(self) -> None:
        src = (PROJECT_ROOT / "backend" / "app" / "api" / "patents.py").read_text(encoding="utf-8")
        self.assertIn('@router.get("/patents/tw-legal-status/pending")', src)
        self.assertIn('@router.post("/patents/tw-legal-status/refresh")', src)
        self.assertIn('@router.post("/patents/{patent_id}/tw-legal-status")', src)
        self.assertLess(
            src.index('@router.post("/patents/tw-legal-status/refresh")'),
            src.index('@router.post("/patents/{patent_id}/tw-legal-status")'),
        )

    def test_router_does_not_write_sql_directly(self) -> None:
        src = (PROJECT_ROOT / "backend" / "app" / "api" / "patents.py").read_text(encoding="utf-8")
        self.assertNotIn("UPDATE core_layer.patents", src)
        self.assertNotIn("INSERT INTO core_layer", src)
        self.assertIn("list_pending_tw_legal_status_patents", src)

    def test_repository_uses_atomic_tw_blank_update_and_report_enqueue(self) -> None:
        src = (PROJECT_ROOT / "backend" / "app" / "app_layer" / "patent_queries.py").read_text(encoding="utf-8")
        self.assertIn("country_code = 'TW'", src)
        self.assertIn("NULLIF(BTRIM(p.legal_status), '') IS NULL", src)
        self.assertIn("jsonb_build_object", src)
        self.assertIn("legal_status = %(status)s::text", src)
        self.assertIn("'to_status', %(status)s::text", src)
        self.assertIn("report_generate", src)
        # ⚠ 2026-08-09 契約變更：原本刷新 `lifecycle`，該報表已由使用者裁決刪除，
        # 法律狀態改由 `country_distribution`（國別×法律狀態）承接。
        self.assertIn('"report_names": ["country_distribution"]', src)
        body = src.split("def register_tw_legal_status", 1)[-1].split("def search_patents", 1)[0]
        self.assertNotIn("clustering_", body)


class _FakeCursor:
    def __init__(self, *, fetchone_rows=None, fetchall_rows=None) -> None:
        self.fetchone_rows = list(fetchone_rows or [])
        self.fetchall_rows = list(fetchall_rows or [])
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None) -> None:
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetchone_rows.pop(0) if self.fetchone_rows else None

    def fetchall(self):
        return self.fetchall_rows.pop(0) if self.fetchall_rows else []


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self.cursor_obj = cursor
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def cursor(self, **_kwargs):
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _FakePool:
    def __init__(self, *connections: _FakeConnection) -> None:
        self.connections = list(connections)

    def connection(self):
        return self.connections.pop(0)


class TwLegalStatusRuntimeContractTests(unittest.TestCase):
    def test_api_maps_repository_errors_to_http_statuses(self) -> None:
        from fastapi import HTTPException

        from backend.app.api import patents
        from backend.app.app_layer import patent_queries

        request = patents.TwLegalStatusRequest(legal_status=CURATED_TW_STATUSES[0])
        cases = [
            (patent_queries.TwLegalStatusNotFoundError("missing"), 404),
            (patent_queries.TwLegalStatusCountryError("not tw"), 422),
            # 🔴 2026-08-07 反悔機制：已有值可改可清，409 conflict 已拆。
            (ValueError("bad"), 422),
        ]
        for exc, status in cases:
            with self.subTest(status=status), mock.patch.object(
                patents.patent_queries,
                "register_tw_legal_status",
                side_effect=exc,
            ):
                with self.assertRaises(HTTPException) as ctx:
                    patents.register_tw_legal_status(request, patent_id=1)
                self.assertEqual(ctx.exception.status_code, status)

    def test_api_pending_and_retry_delegate_to_repository(self) -> None:
        from backend.app.api import patents

        with mock.patch.object(
            patents.patent_queries,
            "list_pending_tw_legal_status_patents",
            return_value={"items": [], "allowed_statuses": []},
        ) as pending:
            self.assertEqual(
                patents.list_pending_tw_legal_status_patents(workspace_id=9, limit=5, offset=2),
                {"items": [], "allowed_statuses": []},
            )
            pending.assert_called_once_with(workspace_id=9, limit=5, offset=2)

        with mock.patch.object(
            patents.patent_queries,
            "enqueue_tw_legal_status_refresh",
            return_value={"refresh_status": "queued", "refresh_job_id": 11},
        ) as refresh:
            self.assertEqual(
                patents.retry_tw_legal_status_refresh(
                    patents.TwLegalStatusRefreshRequest(workspace_id=9)
                ),
                {"refresh_status": "queued", "refresh_job_id": 11},
            )
            refresh.assert_called_once_with(workspace_id=9)

    def test_repository_pending_query_returns_allowed_statuses(self) -> None:
        from backend.app.app_layer import patent_queries

        cursor = _FakeCursor(fetchone_rows=[{"total": 1, "pending_total": 1}], fetchall_rows=[[{"patent_id": 7}]])
        with mock.patch.object(
            patent_queries,
            "get_pool",
            return_value=_FakePool(_FakeConnection(cursor)),
        ):
            result = patent_queries.list_pending_tw_legal_status_patents(
                workspace_id=3,
                limit=10,
                offset=5,
            )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["pending_total"], 1)
        self.assertEqual(result["items"], [{"patent_id": 7}])
        self.assertEqual(tuple(result["allowed_statuses"]), CURATED_TW_STATUSES)
        self.assertEqual(cursor.executed[0][1], {"workspace_id": 3, "limit": 10, "offset": 5})

    def test_repository_register_commits_projection_and_enqueues_refresh(self) -> None:
        from backend.app.app_layer import patent_queries

        cursor = _FakeCursor(fetchone_rows=[{"patent_id": 7, "to_status": CURATED_TW_STATUSES[3]}])
        conn = _FakeConnection(cursor)
        with mock.patch.object(
            patent_queries, "get_pool", return_value=_FakePool(conn)
        ), mock.patch.object(
            patent_queries,
            "enqueue_tw_legal_status_refresh",
            return_value={"refresh_status": "queued", "refresh_job_id": 99},
        ) as enqueue:
            result = patent_queries.register_tw_legal_status(
                patent_id=7,
                legal_status=CURATED_TW_STATUSES[3],
                workspace_id=4,
            )
        self.assertTrue(conn.committed)
        self.assertEqual(result["refresh_job_id"], 99)
        self.assertEqual(result["legal_status"], CURATED_TW_STATUSES[3])
        enqueue.assert_called_once_with(workspace_id=4)
        self.assertIn("UPDATE derived_layer.report_patent_base", cursor.executed[1][0])

    def test_repository_register_keeps_saved_status_when_enqueue_fails(self) -> None:
        from backend.app.app_layer import patent_queries

        cursor = _FakeCursor(fetchone_rows=[{"patent_id": 8, "to_status": CURATED_TW_STATUSES[0]}])
        conn = _FakeConnection(cursor)
        with mock.patch.object(
            patent_queries, "get_pool", return_value=_FakePool(conn)
        ), mock.patch.object(
            patent_queries,
            "enqueue_tw_legal_status_refresh",
            side_effect=RuntimeError("queue down"),
        ):
            result = patent_queries.register_tw_legal_status(
                patent_id=8,
                legal_status=CURATED_TW_STATUSES[0],
            )
        self.assertTrue(conn.committed)
        self.assertEqual(result["refresh_status"], "enqueue_failed")
        self.assertIn("queue down", result["refresh_error"])

    def test_repository_register_rolls_back_and_classifies_empty_update(self) -> None:
        from backend.app.app_layer import patent_queries

        cursor = _FakeCursor(fetchone_rows=[None])
        conn = _FakeConnection(cursor)
        with mock.patch.object(
            patent_queries, "get_pool", return_value=_FakePool(conn)
        ), mock.patch.object(
            patent_queries,
            "_classify_tw_status_failure",
            side_effect=patent_queries.TwLegalStatusConflictError("exists"),
        ), self.assertRaises(patent_queries.TwLegalStatusConflictError):
            patent_queries.register_tw_legal_status(
                patent_id=8,
                legal_status=CURATED_TW_STATUSES[0],
            )
        self.assertTrue(conn.rolled_back)
        self.assertFalse(conn.committed)

    def test_repository_search_patents_uses_single_query(self) -> None:
        from backend.app.app_layer import patent_queries

        row = {"patent_id": 7, "title": "needle"}
        cursor = _FakeCursor(fetchall_rows=[[row]])
        with mock.patch.object(
            patent_queries,
            "get_pool",
            return_value=_FakePool(_FakeConnection(cursor)),
        ):
            result = patent_queries.search_patents(q=" needle ", limit=3)
        self.assertEqual(result, {"items": [row]})
        self.assertEqual(cursor.executed[0][1], {"q": "%needle%", "limit": 3})

    def test_repository_list_patents_attaches_workspace_membership(self) -> None:
        from backend.app.app_layer import patent_queries

        item = {"patent_id": 7, "title": "sample"}
        membership = {"patent_id": 7, "workspace_id": 2, "workspace_name": "W"}
        cursor = _FakeCursor(fetchone_rows=[{"total": 1}], fetchall_rows=[[item], [membership]])
        with mock.patch.object(
            patent_queries,
            "get_pool",
            return_value=_FakePool(_FakeConnection(cursor)),
        ), mock.patch.object(
            patent_queries,
            "_topic_labels_by_patent",
            return_value={},
        ):
            result = patent_queries.list_patents(
                keyword=" sample ",
                limit=5,
                offset=1,
                topic_workspace_id=None,
            )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["workspaces"], [{"workspace_id": 2, "workspace_name": "W"}])
        self.assertEqual(cursor.executed[0][1], {"kw": "%sample%"})
        self.assertEqual(cursor.executed[1][1], {"kw": "%sample%", "limit": 5, "offset": 1})
        self.assertEqual(cursor.executed[2][1], {"pids": [7]})

    def test_repository_failure_classifier_distinguishes_causes(self) -> None:
        from backend.app.app_layer import patent_queries

        # 🔴 2026-08-07 反悔機制：「已有狀態」不再是錯誤——TW 案一律可改可清，
        # classifier 只剩查無專利與非 TW 兩種未命中原因。
        cases = [
            (None, patent_queries.TwLegalStatusNotFoundError),
            ({"country_code": "US"}, patent_queries.TwLegalStatusCountryError),
        ]
        for row, error_type in cases:
            with self.subTest(error_type=error_type):
                cursor = _FakeCursor(fetchone_rows=[row])
                with mock.patch.object(
                    patent_queries,
                    "get_pool",
                    return_value=_FakePool(_FakeConnection(cursor)),
                ), self.assertRaises(error_type):
                    patent_queries._classify_tw_status_failure(1)

    def test_repository_get_patent_figure_handles_missing_and_bytes(self) -> None:
        from backend.app.app_layer import patent_queries

        missing = _FakeCursor(fetchone_rows=[None])
        with mock.patch.object(
            patent_queries,
            "get_pool",
            return_value=_FakePool(_FakeConnection(missing)),
        ):
            self.assertIsNone(patent_queries.get_patent_figure(1))

        present = _FakeCursor(fetchone_rows=[(bytearray(b"abc"),)])
        with mock.patch.object(
            patent_queries,
            "get_pool",
            return_value=_FakePool(_FakeConnection(present)),
        ):
            self.assertEqual(patent_queries.get_patent_figure(1), b"abc")

    def test_repository_enqueue_uses_legal_status_report_only(self) -> None:
        """⚠ 2026-08-09 契約變更：刷新目標由已刪除的 `lifecycle` 改為
        `country_distribution`；「只排法律狀態相關報表、不牽動分群」的意圖不變。"""
        from backend.app.app_layer import patent_queries

        job = SimpleNamespace(job_id=123)
        with mock.patch.object(
            patent_queries.job_repository,
            "create_job",
            return_value=job,
        ) as create_job:
            result = patent_queries.enqueue_tw_legal_status_refresh(workspace_id=5)
        self.assertEqual(result, {"refresh_status": "queued", "refresh_job_id": 123})
        create_job.assert_called_once_with(
            "report_generate",
            {"report_names": ["country_distribution"], "workspace_id": 5},
            workspace_id=5,
        )

    def test_mapping_rejects_unsupported_tw_status(self) -> None:
        with self.assertRaises(ValueError):
            legal_status.validate_tw_legal_status("unsupported")


class TwLegalStatusMigrationStaticContractTests(unittest.TestCase):
    def test_migration_adds_history_column_only(self) -> None:
        migrations = sorted((PROJECT_ROOT / "alembic" / "versions").glob("*tw_legal_status_history*.py"))
        self.assertEqual(len(migrations), 1)
        src = migrations[0].read_text(encoding="utf-8")
        self.assertIn("legal_status_history", src)
        self.assertIn("JSONB", src)
        self.assertIn("'[]'::jsonb", src)
        self.assertIn("nullable=False", src)
        self.assertIn("drop_column", src)
        self.assertNotIn("create_table", src)

    def test_migration_upgrade_and_downgrade_target_patents_only(self) -> None:
        path = PROJECT_ROOT / "alembic" / "versions" / "0047_tw_legal_status_history.py"
        spec = importlib.util.spec_from_file_location("tw_legal_status_history", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        calls = []

        class FakeOp:
            @staticmethod
            def add_column(table, column, schema=None):
                calls.append(("add_column", table, column.name, schema))

            @staticmethod
            def execute(sql):
                calls.append(("execute", sql))

            @staticmethod
            def drop_column(table, column, schema=None):
                calls.append(("drop_column", table, column, schema))

        with mock.patch.object(module, "op", FakeOp):
            module.upgrade()
            module.downgrade()
        self.assertIn(("add_column", "patents", "legal_status_history", "core_layer"), calls)
        self.assertIn(("drop_column", "patents", "legal_status_history", "core_layer"), calls)


if __name__ == "__main__":
    unittest.main()
