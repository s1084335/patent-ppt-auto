"""初階篩選：保留期硬刪（切片 F，PRE-007）。

## 🔴 本檔守的是「不可逆操作不得誤刪」

硬刪是本 change 唯一不可逆的動作。刪錯了沒有還原路徑——`restore_patents`
只還原「封存」狀態，硬刪是真的把列從 `core_layer.patents` 移除，
11 條 CASCADE 外鍵會連帶清掉附屬、圖、人物、向量、指派、檢索詞。

## ⚠ 規格沒防到、本檔補上的一條

PRE-007 只說「封存滿一年者成為硬刪對象」，**沒說要檢查它在別的 workspace
還是不是有效成員**。但排除是 **workspace 級**的（0035／0056 明訂）：
同一件專利可以在 A 被剔除、在 B 照常分析。

🔴 照規格字面實作會**把 B 的專利刪掉**，而 B 的使用者從頭到尾不知道。
故本模組加一條硬性前提：**只刪在所有 workspace 都不是有效成員的專利**。
"""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.types.json import Jsonb

TEST_DB = "patent_ppt_prefilter_purge"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLAIM_COL = "獨立項[KR,JP,US,CN,EP,IN]"

WS_A = 801          # 剔除發生的 workspace
WS_B = 802          # 另一個仍在用同一批專利的 workspace
PATENTS = [3001, 3002, 3003, 3004]


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


def _reset_pool() -> None:
    from backend.app.db import connection

    if getattr(connection, "_pool", None) is not None:
        try:
            connection._pool.close()
        except Exception:  # noqa: BLE001
            pass
        connection._pool = None


def _assert_pool_targets_test_db() -> None:
    from backend.app.db.connection import get_pool

    with get_pool().connection() as c:
        with c.cursor() as cur:
            cur.execute("SELECT current_database()")
            actual = cur.fetchone()[0]
    if actual != TEST_DB:
        raise AssertionError(
            f"連線池指向 {actual!r}，不是本檔的測試庫 {TEST_DB!r}")


class PurgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 🔴 先連、後改 env——連不上就 skip，一個環境變數都沒動過。
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True,
                                 connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")

        cls._prev = {k: os.environ.get(k)
                     for k in ("PGHOST", "PGPORT", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = _kw("postgres")["host"]
        os.environ["PGPORT"] = str(_kw("postgres")["port"])
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB
        _reset_pool()
        command.upgrade(_alembic_cfg(), "head")
        _assert_pool_targets_test_db()

    @classmethod
    def tearDownClass(cls):
        _reset_pool()
        for key, value in getattr(cls, "_prev", {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True,
                                 connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:  # noqa: BLE001
            pass

    def setUp(self):
        self.conn = psycopg.connect(**_kw(TEST_DB), autocommit=True)
        self.addCleanup(self.conn.close)
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM derived_layer.workspace_excluded_patents")
            cur.execute("DELETE FROM app_layer.workspaces")
            cur.execute("DELETE FROM core_layer.patents")
            cur.executemany("INSERT INTO core_layer.patents (id) VALUES (%s)",
                            [(p,) for p in PATENTS])
            cur.execute(
                "INSERT INTO app_layer.workspaces "
                "(workspace_id, workspace_name, is_global, patent_ids_json) "
                "VALUES (%s, 'A', false, %s)", (WS_A, Jsonb(PATENTS)))
            # B 也含 3002：驗「別的 workspace 還在用就不准刪」
            cur.execute(
                "INSERT INTO app_layer.workspaces "
                "(workspace_id, workspace_name, is_global, patent_ids_json) "
                "VALUES (%s, 'B', false, %s)", (WS_B, Jsonb([3002])))

    def _archive(self, patent_id: int, *, days_ago: int, workspace_id: int = WS_A):
        """把某筆標成已封存，並把封存時間往回推。"""
        when = datetime.now(timezone.utc) - timedelta(days=days_ago)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO derived_layer.workspace_excluded_patents "
                "(workspace_id, patent_id, reason, status, source, excluded_at) "
                "VALUES (%s, %s, '測試', 'excluded', 'prefilter', %s)",
                (workspace_id, patent_id, when))

    def _exists(self, patent_id: int) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM core_layer.patents WHERE id = %s",
                        (patent_id,))
            return cur.fetchone() is not None

    # ── F.1 保留期 ──────────────────────────────────────
    def test_within_retention_is_not_a_candidate(self):
        """🔴 未滿保留期者不得列入刪除對象。"""
        from backend.app.prefilter import purge

        self._archive(3001, days_ago=364)
        self._archive(3003, days_ago=366)
        ids = [c["patent_id"] for c in purge.purge_candidates(conn=self.conn)]
        self.assertNotIn(3001, ids, "未滿一年的被列入刪除對象了")
        self.assertIn(3003, ids)

    def test_pending_and_kept_are_never_candidates(self):
        """🔴 只有**已確定剔除**者才進入保留期計時。

        ⚠ pending 是還沒裁決、kept 是使用者說要留——兩者都不該被刪，
        而它們與 excluded 同表，只差一個 status 欄。
        """
        from backend.app.prefilter import purge

        when = datetime.now(timezone.utc) - timedelta(days=999)
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO derived_layer.workspace_excluded_patents "
                "(workspace_id, patent_id, status, source, excluded_at) "
                "VALUES (%s, %s, %s, 'prefilter', %s)",
                [(WS_A, 3001, "pending", when), (WS_A, 3003, "kept", when)])
        self.assertEqual(purge.purge_candidates(conn=self.conn), [])

    # ── 🔴 規格沒防到的：別的 workspace 還在用 ───────────
    def test_still_member_elsewhere_is_excluded_from_candidates(self):
        """🔴 在別的 workspace 仍是有效成員者，不得成為刪除對象。

        排除是 workspace 級的：同一件可以在 A 被剔除、在 B 照常分析。
        照 PRE-007 字面實作會把 B 的專利刪掉，而 B 的使用者不會知道。
        """
        from backend.app.prefilter import purge

        self._archive(3002, days_ago=400)          # 3002 也在 B 的成員名單裡
        self._archive(3003, days_ago=400)
        ids = [c["patent_id"] for c in purge.purge_candidates(conn=self.conn)]
        self.assertNotIn(3002, ids, "刪掉了別的 workspace 還在用的專利")
        self.assertIn(3003, ids)

    def test_excluded_in_every_workspace_is_a_candidate(self):
        """兩邊都剔除了就可以刪——「還在用」才是擋的理由。"""
        from backend.app.prefilter import purge

        self._archive(3002, days_ago=400, workspace_id=WS_A)
        self._archive(3002, days_ago=400, workspace_id=WS_B)
        ids = [c["patent_id"] for c in purge.purge_candidates(conn=self.conn)]
        self.assertIn(3002, ids)

    # ── F.2 dry-run ─────────────────────────────────────
    def test_dry_run_changes_nothing(self):
        """🔴 dry-run 不得變更任何資料。"""
        from backend.app.prefilter import purge

        self._archive(3003, days_ago=400)
        result = purge.purge_patents([3003], dry_run=True, conn=self.conn)
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["planned"], [3003])
        self.assertTrue(self._exists(3003), "dry-run 把專利刪掉了")
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM "
                        "derived_layer.workspace_excluded_patents")
            self.assertEqual(cur.fetchone()[0], 1, "dry-run 動到了剔除名單")

    def test_dry_run_is_the_default(self):
        """🔴 預設就是 dry-run——不可逆操作不得靠呼叫端記得傳參數。"""
        from backend.app.prefilter import purge

        self._archive(3003, days_ago=400)
        purge.purge_patents([3003], conn=self.conn)
        self.assertTrue(self._exists(3003), "沒傳 dry_run 就真的刪了")

    # ── F.3／F.6 引用清理 ───────────────────────────────
    def test_real_purge_removes_patent_and_references(self):
        """刪除後：專利不存在、成員名單不含它、剔除名單無孤兒列。"""
        from backend.app.prefilter import purge

        self._archive(3003, days_ago=400)
        result = purge.purge_patents([3003], dry_run=False, conn=self.conn)
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(self._exists(3003))
        with self.conn.cursor() as cur:
            cur.execute("SELECT patent_ids_json FROM app_layer.workspaces "
                        "WHERE workspace_id = %s", (WS_A,))
            self.assertNotIn(3003, cur.fetchone()[0],
                             "🔴 patent_ids_json 仍含已刪專利——無 FK 保護，"
                             "留孤兒不會報錯")
            cur.execute("SELECT count(*) FROM "
                        "derived_layer.workspace_excluded_patents "
                        "WHERE patent_id = %s", (3003,))
            self.assertEqual(cur.fetchone()[0], 0, "剔除名單留下孤兒列")

    def test_every_fk_to_patents_is_cascade(self):
        """🔴 硬刪的可行性完全建立在「所有 FK 都是 CASCADE」上。

        ⚠ 這條鎖的是**不變量**，不是某一張子表：
        - 有人日後加一條 `RESTRICT`／`NO ACTION` 的 FK → 硬刪會整批失敗
        - 有人加一條 `SET NULL` 的 FK → 子表留下 patent_id 為 NULL 的孤兒列，
          **不會報錯**，只會讓統計悄悄少一筆

        ⚠ 刻意不改用「插一筆子資料再看有沒有被連帶刪」：那只驗到那一張表，
        而風險在**下一張還沒被想到的表**。
        """
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT c.conrelid::regclass::text, c.confdeltype
                FROM pg_constraint c
                WHERE c.contype = 'f'
                  AND c.confrelid = 'core_layer.patents'::regclass
                ORDER BY 1
            """)
            rows = cur.fetchall()
        self.assertTrue(rows, "查不到任何指向 patents 的外鍵——查詢寫錯了")
        bad = [(name, rule) for name, rule in rows if rule != "c"]
        self.assertEqual(
            bad, [],
            f"有非 CASCADE 的外鍵指向 patents，硬刪會失敗或留孤兒：{bad}")

    def test_purge_refuses_non_candidates(self):
        """🔴 不得刪「不在候選清單裡」的專利。

        ⚠ 呼叫端傳什麼就刪什麼的話，這支函式本身就是一把沒有保險的刀。
        """
        from backend.app.prefilter import purge

        with self.assertRaises(purge.PurgeError):
            purge.purge_patents([3001], dry_run=False, conn=self.conn)
        self.assertTrue(self._exists(3001))

    # ── F.5 失敗隔離與逐筆回報 ──────────────────────────
    def test_failure_isolation(self):
        """🔴 一筆失敗不得影響其餘筆，且要逐筆回報。"""
        from backend.app.prefilter import purge

        for pid in (3003, 3004):
            self._archive(pid, days_ago=400)

        real = purge._delete_one

        def flaky(conn, patent_id):
            if patent_id == 3003:
                raise RuntimeError("模擬失敗")
            return real(conn, patent_id)

        purge._delete_one = flaky
        try:
            result = purge.purge_patents([3003, 3004], dry_run=False,
                                         conn=self.conn)
        finally:
            purge._delete_one = real

        self.assertEqual(result["deleted"], 1)
        self.assertTrue(self._exists(3003), "失敗那筆不該被刪")
        self.assertFalse(self._exists(3004), "其餘筆應照常刪除")
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0]["patent_id"], 3003)
        self.assertIn("模擬失敗", result["failed"][0]["error"])

    def test_batch_limit_is_respected(self):
        """批次上限：一次不得無上限地刪。"""
        from backend.app.prefilter import purge

        for pid in (3003, 3004):
            self._archive(pid, days_ago=400)
        result = purge.purge_patents([3003, 3004], dry_run=True, limit=1,
                                     conn=self.conn)
        self.assertEqual(len(result["planned"]), 1)


if __name__ == "__main__":
    unittest.main()
