"""0035 不相干專利排除清單表契約測試（獨立測試 DB，不碰正式庫 patent_ppt）。

db patent_ppt_exclpat → upgrade head，驗證：
- derived_layer.workspace_excluded_patents 欄位定版（最小口徑：workspace_id + patent_id
  + reason + excluded_at，能推導的不存）。
- 複合 PK (workspace_id, patent_id)：天然去重，同一 ws 同一專利只留一列。
- workspace FK ON DELETE CASCADE（workspace 刪除時排除紀錄一併清）。
- 同一 patent_id 可在不同 workspace 各自被排除（排除是 workspace 級、非專利級）。
- downgrade 可移除該表。

規格唯一來源：irrelevant-patent-filter-spec.md 第 66-74 行。
⚠ 落點依專案慣例放 derived_layer（與 0034 其他 derived 表同 layer）。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_exclpat"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 最小口徑：workspace_id + patent_id（複合 PK）+ reason + excluded_at。
# 不含 topic_key（規格「待評估」，能推導的先不存）、不含代理主鍵。
EXPECTED_COLUMNS = {
    "workspace_id",
    "patent_id",
    "reason",
    "excluded_at",
}


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


class WorkspaceExcludedPatentsMigrationTests(unittest.TestCase):
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

    def _new_workspace(self, conn, name: str) -> int:
        return conn.execute(
            "INSERT INTO app_layer.workspaces (workspace_name) VALUES (%s) RETURNING workspace_id",
            (name,),
        ).fetchone()[0]

    def test_columns(self):
        """0035 最小口徑四欄仍在；excluded_at 為 timestamptz。

        ⚠ 這裡驗子集不驗相等：0036 另加 status/source/ai_verdict 三欄（複核狀態），
        全表欄位定版由 test_exclusion_review_status_migration.py 負責。本測試只確保
        0035 的最小口徑不被後續 migration 移除或改型。
        """
        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='derived_layer' AND table_name='workspace_excluded_patents'"
            ).fetchall()
        cols = {r[0]: r[1] for r in rows}
        self.assertTrue(EXPECTED_COLUMNS.issubset(set(cols)))
        self.assertEqual(cols["excluded_at"], "timestamp with time zone")

    def test_composite_pk_dedupes(self):
        """複合 PK (workspace_id, patent_id)：同 ws 同專利重複插入被擋（天然去重）。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._new_workspace(c, "ws-pk")
            c.execute(
                "INSERT INTO derived_layer.workspace_excluded_patents "
                "(workspace_id, patent_id, reason) VALUES (%s, %s, %s)",
                (ws, 12345, "AI 判定不相干"),
            )
            c.commit()
            with self.assertRaises(psycopg.errors.UniqueViolation):
                c.execute(
                    "INSERT INTO derived_layer.workspace_excluded_patents "
                    "(workspace_id, patent_id, reason) VALUES (%s, %s, %s)",
                    (ws, 12345, "再次排除"),
                )
            c.rollback()

    def test_same_patent_excluded_in_different_workspaces(self):
        """排除是 workspace 級：同一 patent_id 可在不同 workspace 各自被排除。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws_a = self._new_workspace(c, "ws-a")
            ws_b = self._new_workspace(c, "ws-b")
            for ws in (ws_a, ws_b):
                c.execute(
                    "INSERT INTO derived_layer.workspace_excluded_patents "
                    "(workspace_id, patent_id, reason) VALUES (%s, %s, %s)",
                    (ws, 999, "不相干"),
                )
            c.commit()
            count = c.execute(
                "SELECT count(*) FROM derived_layer.workspace_excluded_patents WHERE patent_id = 999"
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_reason_nullable(self):
        """reason 可空（人工排除可不填理由）。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._new_workspace(c, "ws-noreason")
            c.execute(
                "INSERT INTO derived_layer.workspace_excluded_patents "
                "(workspace_id, patent_id) VALUES (%s, %s)",
                (ws, 555),
            )
            c.commit()
            reason = c.execute(
                "SELECT reason FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id = %s AND patent_id = 555",
                (ws,),
            ).fetchone()[0]
        self.assertIsNone(reason)

    def test_fk_cascade(self):
        """workspace 刪除時，其排除紀錄一併被 CASCADE 清掉。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._new_workspace(c, "ws-cascade")
            c.execute(
                "INSERT INTO derived_layer.workspace_excluded_patents "
                "(workspace_id, patent_id, reason) VALUES (%s, %s, %s)",
                (ws, 42, "不相干"),
            )
            c.commit()
            c.execute("DELETE FROM app_layer.workspaces WHERE workspace_id = %s", (ws,))
            c.commit()
            left = c.execute(
                "SELECT count(*) FROM derived_layer.workspace_excluded_patents WHERE workspace_id = %s",
                (ws,),
            ).fetchone()[0]
        self.assertEqual(left, 0)

    def test_downgrade_removes_table(self):
        """downgrade 到 0034 可移除該表；再 upgrade 回 head 不影響其他測試。"""
        cfg = _alembic_cfg()
        command.downgrade(cfg, "0034_market_doc_summary")
        with psycopg.connect(**_kw(TEST_DB)) as c:
            reg = c.execute(
                "SELECT to_regclass('derived_layer.workspace_excluded_patents')"
            ).fetchone()[0]
        self.assertIsNone(reg)
        command.upgrade(cfg, "head")


if __name__ == "__main__":
    unittest.main()
