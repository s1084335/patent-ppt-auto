"""0029 report_patent_base 補 Orig. IPC/CPC 兩欄的契約測試（獨立測試 DB，不碰 patent_ppt）。

db patent_ppt_origipc → upgrade head，驗證：
1. 實體表 legacy_0021.report_patent_base 具備 "Orig. IPC(Main)"／"Orig. CPC(Main)" 且為 text
2. 相容 VIEW derived_layer.report_patent_base **帶得出**新欄（VIEW 為 SELECT * 式，
   不重建就不會有新欄，這是本次缺陷的真正成因）
3. 既有 "Curr. IPC(Main)"／"Curr. CPC(Main)" 仍在（只增不減，不影響其他讀取者）
4. VIEW 欄位為實體表欄位的完整投影（重建沒有漏欄）
5. downgrade 可反向還原（兩欄移除、VIEW 仍可查且不含新欄）
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_origipc"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
NEW_COLUMNS = ("Orig. IPC(Main)", "Orig. CPC(Main)")
KEPT_COLUMNS = ("Curr. IPC(Main)", "Curr. CPC(Main)")
# downgrade 目標寫絕對 revision：用相對 "-1" 在本版之後再加 migration 時會退錯版本。
PREVIOUS_REVISION = "0028_global_workspace"


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


def _columns(conn, schema: str, table: str) -> dict[str, str]:
    """取某表／VIEW 的欄位名 → 型別對照。"""
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s",
        (schema, table),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


class OrigIpcCpcMigrationTests(unittest.TestCase):
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

    def test_base_table_has_orig_columns_as_text(self):
        """實體表具備兩個 Orig. 欄，型別為 text（與來源 core_layer.patents 同型別）。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            cols = _columns(c, "legacy_0021", "report_patent_base")
        for name in NEW_COLUMNS:
            self.assertIn(name, cols, f"legacy_0021.report_patent_base 缺少 {name}")
            self.assertEqual(cols[name], "text", f"{name} 型別應為 text")

    def test_curr_columns_kept(self):
        """既有 Curr. 兩欄保留不動——只增不減，避免影響其他讀取者。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            cols = _columns(c, "legacy_0021", "report_patent_base")
        for name in KEPT_COLUMNS:
            self.assertIn(name, cols, f"既有欄位 {name} 不應被移除")

    def test_view_exposes_orig_columns(self):
        """相容 VIEW 帶得出新欄——VIEW 未重建正是本次 refresh／報表 query 報錯的成因。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            cols = _columns(c, "derived_layer", "report_patent_base")
            self.assertTrue(set(NEW_COLUMNS).issubset(cols), f"VIEW 缺少 Orig. 欄：{sorted(cols)}")
            # 真的查得動，不只是 information_schema 有登記
            c.execute('SELECT "Orig. IPC(Main)", "Orig. CPC(Main)" FROM derived_layer.report_patent_base LIMIT 1')

    def test_view_is_full_projection_of_base_table(self):
        """VIEW 欄位＝實體表欄位（同名同順序），確保重建沒有漏欄或改序。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            table_cols = c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='legacy_0021' AND table_name='report_patent_base' "
                "ORDER BY ordinal_position"
            ).fetchall()
            view_cols = c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='derived_layer' AND table_name='report_patent_base' "
                "ORDER BY ordinal_position"
            ).fetchall()
        self.assertEqual([r[0] for r in view_cols], [r[0] for r in table_cols])

    def test_downgrade_removes_columns_and_restores_view(self):
        """downgrade 移除兩欄並還原 VIEW；再 upgrade 回 head 不影響其他測試。"""
        cfg = _alembic_cfg()
        command.downgrade(cfg, PREVIOUS_REVISION)
        with psycopg.connect(**_kw(TEST_DB)) as c:
            table_cols = _columns(c, "legacy_0021", "report_patent_base")
            view_cols = _columns(c, "derived_layer", "report_patent_base")
            for name in NEW_COLUMNS:
                self.assertNotIn(name, table_cols, f"downgrade 後實體表仍有 {name}")
                self.assertNotIn(name, view_cols, f"downgrade 後 VIEW 仍有 {name}")
            # VIEW 還原後仍可查，且既有欄位還在
            c.execute('SELECT "Curr. IPC(Main)" FROM derived_layer.report_patent_base LIMIT 1')
        command.upgrade(cfg, "head")


if __name__ == "__main__":
    unittest.main()
