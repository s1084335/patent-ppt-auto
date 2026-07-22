"""Market evidence workflow run repository 測試。"""

from __future__ import annotations

import unittest
from unittest import mock

from backend.app.db import job_repository
from backend.app.market import evidence_runs
from backend.app.worker import runner


class _Cursor:
    """模擬 psycopg cursor，保留 SQL 與參數供測試檢查。"""

    def __init__(self) -> None:
        """初始化查詢紀錄與固定回傳列。"""
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> "_Cursor":
        """支援 with conn.cursor(...) as cur。"""
        return self

    def __exit__(self, *args: object) -> None:
        """測試用 context manager 不需清理資源。"""
        return None

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        """保存 SQL 與參數，模擬 DB execute。"""
        self.executed.append((sql, params))

    def fetchone(self) -> dict[str, object]:
        """回傳 workflow_runs INSERT 後的固定資料列。"""
        return {
            "run_id": 41,
            "run_type": evidence_runs.MARKET_EVIDENCE_RUN_TYPE,
            "status": evidence_runs.MARKET_EVIDENCE_TASK_STATUS,
            "workspace_id": 9,
            "request_json": {"scope": "robot mower"},
        }


class _Connection:
    """模擬 psycopg connection，讓 repository 測試不碰真 DB。"""

    def __init__(self, cursor: _Cursor) -> None:
        """保存共用 cursor 並記錄 commit 次數。"""
        self.cursor_obj = cursor
        self.commits = 0

    def __enter__(self) -> "_Connection":
        """支援 with get_pool().connection() as conn。"""
        return self

    def __exit__(self, *args: object) -> None:
        """測試用 context manager 不需清理資源。"""
        return None

    def cursor(self, **_: object) -> _Cursor:
        """回傳固定 cursor；忽略 row_factory 等 psycopg 參數。"""
        return self.cursor_obj

    def commit(self) -> None:
        """記錄 repository 是否明確提交交易。"""
        self.commits += 1


class _Pool:
    """模擬 connection pool。"""

    def __init__(self, conn: _Connection) -> None:
        """保存固定 connection。"""
        self.conn = conn

    def connection(self) -> _Connection:
        """回傳固定 connection context manager。"""
        return self.conn


class MarketEvidenceRunRepositoryTests(unittest.TestCase):
    """驗證 market evidence run 只建立追蹤列，不進一般 worker job type。"""

    def test_market_evidence_run_type_is_not_worker_job_type(self) -> None:
        """market evidence research 是追蹤 run，不得被一般 worker 認領。"""
        self.assertNotIn(evidence_runs.MARKET_EVIDENCE_RUN_TYPE, job_repository.JOB_TYPES)
        self.assertNotIn(evidence_runs.MARKET_EVIDENCE_RUN_TYPE, runner.DEFAULT_WORKER_JOB_TYPES)

    def test_create_market_evidence_run_inserts_tracking_workflow_run(self) -> None:
        """建立 market evidence run 時，寫入 workflow_runs 並回傳 run_id。"""
        cursor = _Cursor()
        conn = _Connection(cursor)
        with mock.patch.object(evidence_runs, "get_pool", return_value=_Pool(conn)):
            result = evidence_runs.create_market_evidence_run(
                task_payload={"scope": "robot mower"},
                workspace_id=9,
            )

        sql, params = cursor.executed[0]
        self.assertIn("INSERT INTO app_layer.workflow_runs", sql)
        self.assertEqual(params[0], evidence_runs.MARKET_EVIDENCE_RUN_TYPE)
        self.assertEqual(params[1], evidence_runs.MARKET_EVIDENCE_TASK_STATUS)
        self.assertEqual(params[2], 9)
        self.assertEqual(result["run_id"], 41)
        self.assertEqual(conn.commits, 1)

    def test_create_market_evidence_run_rejects_non_dict_payload(self) -> None:
        """task_payload 必須是 dict，避免不可序列化資料進 workflow_runs。"""
        with self.assertRaises(ValueError):
            evidence_runs.create_market_evidence_run(task_payload=[])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
