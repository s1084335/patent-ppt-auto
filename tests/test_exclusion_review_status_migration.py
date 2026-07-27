"""0036 排除清單複核狀態欄位契約測試（獨立測試 DB，不碰正式庫 patent_ppt）。

db patent_ppt_exclreview → upgrade head，驗證：
- derived_layer.workspace_excluded_patents 新增 status / source / ai_verdict 三欄
  （reason 沿用既有欄，AI 理由與人工註記共用，不另開 ai_reason）。
- status CHECK 只收 'pending' | 'excluded'；預設 'excluded'（既有列與人工剔除語意不變）。
- source CHECK 只收 'manual' | 'ai'；預設 'manual'。
- 既有列（0035 期間寫入的人工剔除）升級後自動帶 status='excluded'、source='manual'，
  語意不變——這是「人工剔除也進不相干桶」能沿用同一張表的前提。
- (workspace_id, status) 部分索引存在：列待複核走索引，不全表掃。
- downgrade 可移除三欄且保留原表與原資料。

規格唯一來源：使用者 2026-07-27 定案（方案 A：擴充既有表，不另開待複核表）。
AI 判讀落 status='pending' 為草稿，需人工「保留／確定」裁決；只有 'excluded' 會被
分群成員子查詢扣除，'pending' 不影響分析——AI 不直接決定正式資料。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_exclreview"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

REVISION = "0036_exclusion_review_status"
PREV_REVISION = "0035_workspace_excluded_patents"

# 0035 最小口徑 + 本次三欄。reason 沿用，不另開 ai_reason。
EXPECTED_COLUMNS = {
    "workspace_id",
    "patent_id",
    "reason",
    "excluded_at",
    "status",
    "source",
    "ai_verdict",
}

PENDING_INDEX = "idx_workspace_excluded_patents_pending"


def _kw(dbname: str) -> dict:
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    pwd = os.getenv("PGPASSWORD")
    if pwd:
        kw["password"] = pwd
    return kw


def _alembic_cfg() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


class ExclusionReviewStatusMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        command.upgrade(_alembic_cfg(), "head")

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:
            pass

    def setUp(self):
        # 每個測試自清資料，避免互相污染（同一 DB 跨測試共用）。
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            c.execute("DELETE FROM derived_layer.workspace_excluded_patents")

    def _new_workspace(self, conn, name: str) -> int:
        return conn.execute(
            "INSERT INTO app_layer.workspaces (workspace_name) VALUES (%s) RETURNING workspace_id",
            (name,),
        ).fetchone()[0]

    def test_columns(self):
        """欄位定版：0035 四欄 + status / source / ai_verdict。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='derived_layer' AND table_name='workspace_excluded_patents'"
            ).fetchall()
        cols = {r[0]: r[1] for r in rows}
        self.assertEqual(set(cols), EXPECTED_COLUMNS)

    def test_status_defaults_to_excluded(self):
        """不帶 status 寫入時預設 'excluded'：人工剔除語意與 0035 完全一致。"""
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            ws = self._new_workspace(c, "ws-default-status")
            c.execute(
                "INSERT INTO derived_layer.workspace_excluded_patents "
                "(workspace_id, patent_id) VALUES (%s, %s)",
                (ws, 101),
            )
            row = c.execute(
                "SELECT status, source FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id=%s AND patent_id=%s",
                (ws, 101),
            ).fetchone()
        self.assertEqual(row, ("excluded", "manual"))

    def test_status_check_rejects_unknown(self):
        """status CHECK 白名單：擋掉 'pending'/'excluded' 以外的值。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._new_workspace(c, "ws-bad-status")
            c.commit()
            with self.assertRaises(psycopg.errors.CheckViolation):
                c.execute(
                    "INSERT INTO derived_layer.workspace_excluded_patents "
                    "(workspace_id, patent_id, status) VALUES (%s, %s, %s)",
                    (ws, 102, "maybe"),
                )
            c.rollback()

    def test_source_check_rejects_unknown(self):
        """source CHECK 白名單：擋掉 'manual'/'ai' 以外的值。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._new_workspace(c, "ws-bad-source")
            c.commit()
            with self.assertRaises(psycopg.errors.CheckViolation):
                c.execute(
                    "INSERT INTO derived_layer.workspace_excluded_patents "
                    "(workspace_id, patent_id, source) VALUES (%s, %s, %s)",
                    (ws, 103, "robot"),
                )
            c.rollback()

    def test_pending_and_excluded_both_accepted(self):
        """兩種 status 都可寫入，且 ai_verdict 可空（人工剔除無 AI 判讀）。"""
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            ws = self._new_workspace(c, "ws-both")
            c.execute(
                "INSERT INTO derived_layer.workspace_excluded_patents "
                "(workspace_id, patent_id, status, source, ai_verdict, reason) "
                "VALUES (%s, %s, 'pending', 'ai', 'irrelevant', 'AI 判定與主題無關')",
                (ws, 201),
            )
            c.execute(
                "INSERT INTO derived_layer.workspace_excluded_patents "
                "(workspace_id, patent_id, status, source) "
                "VALUES (%s, %s, 'excluded', 'manual')",
                (ws, 202),
            )
            rows = dict(
                c.execute(
                    "SELECT patent_id, status FROM derived_layer.workspace_excluded_patents "
                    "WHERE workspace_id=%s",
                    (ws,),
                ).fetchall()
            )
            verdict = c.execute(
                "SELECT ai_verdict FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id=%s AND patent_id=202",
                (ws,),
            ).fetchone()[0]
        self.assertEqual(rows, {201: "pending", 202: "excluded"})
        self.assertIsNone(verdict)

    def test_pending_index_exists(self):
        """(workspace_id, status) 部分索引：列待複核走索引，不全表掃。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            names = {
                r[0]
                for r in c.execute(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname='derived_layer' "
                    "AND tablename='workspace_excluded_patents'"
                ).fetchall()
            }
        self.assertIn(PENDING_INDEX, names)

    def test_existing_rows_backfilled_on_upgrade(self):
        """既有列升級後帶 status='excluded'、source='manual'：0035 期間的人工剔除語意不變。

        downgrade 到 0035 → 寫入一列（此時無新欄）→ upgrade 回 0036 → 該列必須被 backfill。
        """
        cfg = _alembic_cfg()
        command.downgrade(cfg, PREV_REVISION)
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            ws = self._new_workspace(c, "ws-legacy")
            c.execute(
                "INSERT INTO derived_layer.workspace_excluded_patents "
                "(workspace_id, patent_id, reason) VALUES (%s, %s, %s)",
                (ws, 301, "升級前的人工剔除"),
            )
        command.upgrade(cfg, REVISION)
        with psycopg.connect(**_kw(TEST_DB)) as c:
            row = c.execute(
                "SELECT status, source, reason FROM derived_layer.workspace_excluded_patents "
                "WHERE patent_id=301"
            ).fetchone()
        self.assertEqual(row, ("excluded", "manual", "升級前的人工剔除"))

    def test_downgrade_drops_columns_and_keeps_data(self):
        """downgrade 移除三欄，原表與原資料（含 reason）保留。"""
        cfg = _alembic_cfg()
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            ws = self._new_workspace(c, "ws-downgrade")
            c.execute(
                "INSERT INTO derived_layer.workspace_excluded_patents "
                "(workspace_id, patent_id, reason, status, source) "
                "VALUES (%s, %s, %s, 'excluded', 'manual')",
                (ws, 401, "保留這筆"),
            )
        command.downgrade(cfg, PREV_REVISION)
        try:
            with psycopg.connect(**_kw(TEST_DB)) as c:
                cols = {
                    r[0]
                    for r in c.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='derived_layer' "
                        "AND table_name='workspace_excluded_patents'"
                    ).fetchall()
                }
                reason = c.execute(
                    "SELECT reason FROM derived_layer.workspace_excluded_patents "
                    "WHERE patent_id=401"
                ).fetchone()[0]
            self.assertEqual(cols, {"workspace_id", "patent_id", "reason", "excluded_at"})
            self.assertEqual(reason, "保留這筆")
        finally:
            command.upgrade(cfg, REVISION)


if __name__ == "__main__":
    unittest.main()
