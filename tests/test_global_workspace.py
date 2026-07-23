"""全庫 workspace 契約測試（0028 欄位／唯一性／護欄／成員同步）。

2026-07-23 定案「專利總覽＝全庫 workspace」：保留一個特殊 workspace，成員為全部專利，
分群／報表／AI 全部沿用既有機制。本檔驗四件事：

1. migration 0028 契約：`app_layer.workspaces.is_global` 欄位存在，且 partial unique index
   在 DB 層真的擋得住第二個全庫 workspace（程式漏判也不會出現兩個）。
2. 全庫 workspace 的建立與識別：`ensure_global_workspace()` 冪等，回同一個 workspace_id；
   識別一律查 `is_global` 欄，不寫死 workspace_id。
3. 護欄：刪除／改名／手動增減成員全庫 workspace 一律被擋。
4. 成員自動同步：匯入的專利自動 union 進全庫 workspace（去重）。

DB 相關斷言走拋棄式測試庫 patent_ppt_globalws（連不上即 skip），全程不碰正式庫 patent_ppt。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_globalws"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _kw(dbname: str) -> dict:
    """由 PG* 環境變數組連線參數，明確指定 dbname（絕不對 patent_ppt 做寫入）。"""
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


def _alembic_cfg() -> Config:
    """指向本 repo 的 alembic 設定（script_location 用絕對路徑，避免 cwd 影響）。"""
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


def _reset_pool():
    """丟棄既有連線池，讓服務層依當前 PGDATABASE 重建（測試庫是啟動後才切換的）。

    連線池是 lazy 單例，import 時可能已指向預設庫；測試不新增正式程式的重置 API，
    直接歸零模組全域即可（僅測試環境使用）。
    """
    from backend.app.db import connection

    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:  # noqa: BLE001
            pass
    connection._pool = None


class _DbTestBase(unittest.TestCase):
    """共用拋棄式測試庫：建庫 → upgrade head → 測完 DROP。連不上即整類 skip。"""

    @classmethod
    def setUpClass(cls):
        """建立拋棄式測試庫並升到 head；PG 不可用時 skip（不觸碰正式庫）。"""
        # Windows 上 localhost 會先試 IPv6 造成長延遲，統一走 127.0.0.1（與既有契約測試一致）。
        cls._prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = os.getenv("PGHOST", "127.0.0.1")
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        os.environ.pop("DATABASE_URL", None)  # 強制走 PG* 路徑
        os.environ["PGDATABASE"] = TEST_DB    # alembic env.py 與連線池依此解析
        command.upgrade(_alembic_cfg(), "head")

    @classmethod
    def tearDownClass(cls):
        """還原環境變數、丟棄指向測試庫的連線池，再刪除測試庫。

        ⚠ 連線池是跨測試共用的模組單例：若只還原環境變數而不重置池，後續測試會繼續
        借到指向「已被 DROP 的測試庫」的連線而整批失敗。故必須在還原 env 後再重置一次，
        讓下一個使用者依正常 PGDATABASE 重建。
        """
        for key, value in cls._prev_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _reset_pool()
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:  # noqa: BLE001
            pass

    def setUp(self):
        """每個測試前清空 workspaces，確保「只能有一個全庫」的斷言彼此獨立。"""
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as conn:
            conn.execute("DELETE FROM app_layer.workspaces")


class Migration0028ContractTests(_DbTestBase):
    """0028：is_global 欄位與 partial unique index 的 DB 層契約。"""

    def test_is_global_column_exists_with_default_false(self):
        """workspaces.is_global 存在、boolean、NOT NULL、預設 false。"""
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            row = conn.execute(
                """
                SELECT data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'app_layer' AND table_name = 'workspaces'
                  AND column_name = 'is_global'
                """
            ).fetchone()
        self.assertIsNotNone(row, "0028 應新增 app_layer.workspaces.is_global 欄位")
        data_type, is_nullable, column_default = row
        self.assertEqual(data_type, "boolean")
        self.assertEqual(is_nullable, "NO", "is_global 必須 NOT NULL")
        self.assertIn("false", (column_default or "").lower(), "預設值應為 false")

    def test_partial_unique_index_blocks_second_global_workspace(self):
        """DB 層 partial unique index 真的擋得住第二個 is_global=true（程式漏判也不會有兩個）。"""
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json, is_global) "
                "VALUES ('全庫一', '[]'::jsonb, true)"
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json, is_global) "
                    "VALUES ('全庫二', '[]'::jsonb, true)"
                )

    def test_partial_index_still_allows_many_non_global_workspaces(self):
        """partial index 只約束 is_global=true，一般 workspace 不受限（可有多個 false）。"""
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as conn:
            for name in ("一般 A", "一般 B", "一般 C"):
                conn.execute(
                    "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json) "
                    "VALUES (%s, '[]'::jsonb)",
                    (name,),
                )
            count = conn.execute(
                "SELECT count(*) FROM app_layer.workspaces WHERE NOT is_global"
            ).fetchone()[0]
        self.assertEqual(count, 3)

    def test_downgrade_removes_column(self):
        """downgrade 可執行且移除 is_global；跑完再升回 head 供後續測試使用。"""
        cfg = _alembic_cfg()
        command.downgrade(cfg, "0027_workspace_documents")
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            row = conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema='app_layer' "
                "AND table_name='workspaces' AND column_name='is_global'"
            ).fetchone()
        self.assertIsNone(row, "downgrade 後 is_global 應移除")
        command.upgrade(cfg, "head")


class GlobalWorkspaceServiceTests(_DbTestBase):
    """全庫 workspace 的建立、識別與成員同步。"""

    def setUp(self):
        """清空 workspaces 並讓連線池指向測試庫。"""
        super().setUp()
        _reset_pool()

    def test_ensure_global_workspace_is_idempotent(self):
        """第一次呼叫建立、之後回同一個 workspace_id（不會建出第二個）。"""
        from backend.app.app_layer import global_workspace

        first = global_workspace.ensure_global_workspace()
        second = global_workspace.ensure_global_workspace()
        self.assertEqual(first, second)
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            count = conn.execute(
                "SELECT count(*) FROM app_layer.workspaces WHERE is_global"
            ).fetchone()[0]
        self.assertEqual(count, 1, "全庫 workspace 只能有一個")

    def test_get_global_workspace_id_reads_column_not_hardcoded_id(self):
        """識別走 is_global 欄查詢；未建立時回 None，不假設 id=0/1。"""
        from backend.app.app_layer import global_workspace

        self.assertIsNone(global_workspace.get_global_workspace_id())
        # 先塞一般 workspace 佔掉小 id，確保識別不是靠「最小 id」猜。
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json) "
                "VALUES ('佔位一般 ws', '[]'::jsonb)"
            )
        created = global_workspace.ensure_global_workspace()
        self.assertEqual(global_workspace.get_global_workspace_id(), created)

    def test_sync_adds_patents_with_union_dedup(self):
        """匯入專利同步進全庫：union 去重，重複收錄不會讓成員變多。"""
        from backend.app.app_layer import global_workspace

        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as conn:
            for pid in (910001, 910002, 910003):
                conn.execute(
                    "INSERT INTO core_layer.patents (id, title) VALUES (%s, %s)",
                    (pid, f"fixture {pid}"),
                )
        workspace_id = global_workspace.sync_global_workspace_patents([910001, 910002])
        global_workspace.sync_global_workspace_patents([910002, 910003])
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            members = conn.execute(
                "SELECT patent_ids_json FROM app_layer.workspaces WHERE workspace_id = %s",
                (workspace_id,),
            ).fetchone()[0]
        self.assertEqual([int(v) for v in members], [910001, 910002, 910003],
                         "union 去重且保序，重複的 910002 只收一次")

    def test_sync_creates_global_workspace_on_first_import(self):
        """全庫 workspace 不存在時，第一次同步自動建立。"""
        from backend.app.app_layer import global_workspace

        self.assertIsNone(global_workspace.get_global_workspace_id())
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as conn:
            conn.execute("INSERT INTO core_layer.patents (id, title) VALUES (910101, 'f')")
        workspace_id = global_workspace.sync_global_workspace_patents([910101])
        self.assertEqual(global_workspace.get_global_workspace_id(), workspace_id)


class GlobalWorkspaceGuardTests(_DbTestBase):
    """三個護欄：全庫 workspace 不得刪除／改名／手動增減成員。"""

    def setUp(self):
        """清空 workspaces、重建連線池，並建立一個全庫 workspace 供護欄測試。"""
        super().setUp()
        _reset_pool()
        from backend.app.app_layer import global_workspace

        self.global_id = global_workspace.ensure_global_workspace()

    def test_delete_is_blocked(self):
        """刪除全庫 workspace 被擋（GlobalWorkspaceProtectedError）。"""
        from backend.app.app_layer import global_workspace

        with self.assertRaises(global_workspace.GlobalWorkspaceProtectedError):
            global_workspace.assert_not_global(self.global_id, action="delete")

    def test_rename_is_blocked(self):
        """改名全庫 workspace 被擋。"""
        from backend.app.app_layer import global_workspace

        with self.assertRaises(global_workspace.GlobalWorkspaceProtectedError):
            global_workspace.assert_not_global(self.global_id, action="rename")

    def test_manual_member_add_is_blocked_in_clustering_service(self):
        """clustering 服務的手動加成員路徑擋住全庫 workspace（成員只由匯入自動同步）。"""
        from backend.app.app_layer import global_workspace
        from backend.app.clustering import workspace_service

        with self.assertRaises(global_workspace.GlobalWorkspaceProtectedError):
            workspace_service.add_workspace_patents(
                workspace_id=self.global_id, patent_ids=[910201], added_by="tester")

    def test_manual_member_add_is_blocked_in_app_layer(self):
        """app_layer 的 add_patents_to_workspace 手動路徑同樣擋住全庫 workspace。"""
        from backend.app.app_layer import global_workspace, workspace_create

        with self.assertRaises(global_workspace.GlobalWorkspaceProtectedError):
            workspace_create.add_patents_to_workspace(
                workspace_id=self.global_id, patent_ids=[910202])

    def test_normal_workspace_passes_all_guards(self):
        """一般 workspace 不受護欄影響（assert_not_global 放行）。"""
        from backend.app.app_layer import global_workspace

        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as conn:
            normal_id = conn.execute(
                "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json) "
                "VALUES ('一般 ws', '[]'::jsonb) RETURNING workspace_id"
            ).fetchone()[0]
        # 不 raise 即通過。
        global_workspace.assert_not_global(int(normal_id), action="delete")
        global_workspace.assert_not_global(int(normal_id), action="rename")


if __name__ == "__main__":
    unittest.main()
