"""申請人代碼作為公司收斂依據（2026-07-23 定案）的契約測試。

使用者定案：「代碼是公司收斂的依據」——同一申請人代碼的所有專利，
必須收斂到同一個公司顯示名，且**代碼命中時優先於別稱**。

涵蓋三塊（皆用拋棄式 DB patent_ppt_code_conv，不碰正式庫 patent_ppt）：
1. 0030 migration 契約：唯一索引改為 (申請人代碼, alias_lookup_key)，
   允許「一別稱多公司（不同代碼）」，同代碼同別稱仍唯一；downgrade 可還原。
2. refresh_report_patent_base 的收斂行為：代碼命中對照表時，
   即使別稱也命中且指向不同公司名，仍以代碼結果為準；三個顯示名欄各自驗。
3. 同代碼跨字面收斂：同一代碼的不同申請人寫法（別稱表沒收錄者）
   仍收斂到同一顯示名——這是「代碼＝收斂依據」的核心保證。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_code_conv"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# downgrade 目標寫絕對 revision，避免之後再加 migration 時 "-1" 退錯版本
PREVIOUS_REVISION = "0029_report_base_orig_ipc_cpc"
NEW_INDEX = "ux_company_aliases_code_lookup_confirmed"
OLD_INDEX = "ux_company_aliases_lookup_confirmed"


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


def _rw(dbname: str) -> dict:
    kw = _kw(dbname)
    kw["options"] = "-c search_path=derived_layer,core_layer,raw_layer,public"
    return kw


def _alembic_cfg() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


class _BaseCodeConvergence(unittest.TestCase):
    """共用拋棄式 DB：建庫 → upgrade head。"""

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


class CodeLookupUniqueIndexMigrationTests(_BaseCodeConvergence):
    """0030：唯一索引改為含代碼的複合鍵。"""

    def setUp(self):
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute("TRUNCATE derived_layer.company_aliases")
            c.commit()

    def _indexdef(self, name: str) -> str | None:
        with psycopg.connect(**_kw(TEST_DB)) as c:
            row = c.execute(
                "SELECT indexdef FROM pg_indexes WHERE schemaname='derived_layer' AND indexname=%s",
                (name,),
            ).fetchone()
        return row[0] if row else None

    def test_new_composite_index_exists_and_old_removed(self):
        """新索引含「申請人代碼」與 alias_lookup_key；舊的單欄索引已移除。"""
        definition = self._indexdef(NEW_INDEX)
        self.assertIsNotNone(definition, f"缺少新唯一索引 {NEW_INDEX}")
        self.assertIn("申請人代碼", definition)
        self.assertIn("alias_lookup_key", definition)
        self.assertIn("confirmed", definition, "仍應只約束 review_status='confirmed'")
        self.assertIsNone(self._indexdef(OLD_INDEX), f"舊索引 {OLD_INDEX} 應已移除")

    def test_same_alias_allowed_across_different_codes(self):
        """一別稱多公司：不同代碼可共用同一別稱字面（舊索引會擋，這是本次放寬的重點）。"""
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼","公司名稱","別稱") '
                "VALUES ('UN1','Alpha Corp','Shared Name')"
            )
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼","公司名稱","別稱") '
                "VALUES ('UN2','Beta Corp','shared  name')"
            )
            c.commit()
            n = c.execute("SELECT count(*) FROM derived_layer.company_aliases").fetchone()[0]
        self.assertEqual(n, 2, "不同代碼的同一別稱應可並存")

    def test_same_code_same_alias_still_unique(self):
        """同代碼同別稱（normalize 後）仍必須唯一，否則收斂會出現兩列打架。"""
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼","公司名稱","別稱") '
                "VALUES ('UN1','Alpha Corp','Alpha Name')"
            )
            c.commit()
            with self.assertRaises(psycopg.errors.UniqueViolation):
                c.execute(
                    'INSERT INTO derived_layer.company_aliases ("申請人代碼","公司名稱","別稱") '
                    "VALUES ('UN1','Alpha Corp','ALPHA   NAME')"
                )
            c.rollback()

    def test_downgrade_restores_single_column_index(self):
        """downgrade 還原舊單欄索引；再 upgrade 回 head 不影響其他測試。"""
        cfg = _alembic_cfg()
        command.downgrade(cfg, PREVIOUS_REVISION)
        try:
            self.assertIsNone(self._indexdef(NEW_INDEX), "downgrade 後新索引應消失")
            old = self._indexdef(OLD_INDEX)
            self.assertIsNotNone(old, "downgrade 後應還原舊索引")
            self.assertIn("alias_lookup_key", old)
        finally:
            command.upgrade(cfg, "head")


class ReportDisplayNameCodeFirstTests(_BaseCodeConvergence):
    """refresh_report_patent_base：代碼命中時優先於別稱。"""

    def setUp(self):
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute("TRUNCATE derived_layer.company_aliases")
            c.execute("TRUNCATE legacy_0021.report_patent_base")
            c.execute("DELETE FROM core_layer.patent_people")
            c.execute("DELETE FROM core_layer.patent_sources")
            c.execute("DELETE FROM core_layer.patent_attributes")
            c.execute("DELETE FROM core_layer.patents")
            c.commit()

    def _seed_patent(self, conn, patent_id: int, people: dict) -> None:
        """建一筆最小專利與其 patent_people 欄位。"""
        conn.execute(
            'INSERT INTO core_layer.patents (id, "授權公告號", country_code) VALUES (%s, %s, %s)',
            (patent_id, f"PUB{patent_id}", "US"),
        )
        cols = ", ".join(f'"{k}"' for k in people)
        placeholders = ", ".join(["%s"] * len(people))
        conn.execute(
            f"INSERT INTO core_layer.patent_people (patent_id, {cols}) VALUES (%s, {placeholders})",
            (patent_id, *people.values()),
        )

    def _refresh_and_read(self) -> list[tuple]:
        from backend.app.derived.refresh_report_patent_base import REFRESH_SQL
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute(REFRESH_SQL)
            c.commit()
            return c.execute(
                "SELECT patent_id, applicant_display_name, current_assignee_display_name, "
                "recent_assignee_display_name FROM derived_layer.report_patent_base ORDER BY patent_id"
            ).fetchall()

    def test_applicant_code_wins_over_alias(self):
        """Red 核心：代碼與別稱都命中但指向不同公司名時，以代碼為準。"""
        with psycopg.connect(**_rw(TEST_DB)) as c:
            # 對照表：別稱 'Acme Ltd' 掛在代碼 UNCODE 上，公司名為「代碼版公司名」
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼","公司名稱","別稱") '
                "VALUES ('UNCODE','CodeName Corp','Acme Ltd')"
            )
            # 另一列：同樣字面別稱但掛在別的代碼（別稱路徑會先撞到這列）
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼","公司名稱","別稱") '
                "VALUES ('UNOTHER','AliasName Corp','acme  ltd')"
            )
            self._seed_patent(c, 1, {"申請人": "Acme Ltd", "標準化申請人": "Acme Ltd", "申請人代表碼": "UNCODE"})
            c.commit()
        rows = self._refresh_and_read()
        self.assertEqual(rows[0][1], "CodeName Corp", "代碼命中應優先於別稱命中")

    def test_same_code_different_spellings_converge(self):
        """代碼＝收斂依據：同代碼的不同申請人寫法收斂到同一顯示名。"""
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼","公司名稱","別稱") '
                "VALUES ('UNX','Stanley Black & Decker','Stanley Black and Decker Inc')"
            )
            # 三種字面，只有第一種在對照表中；三筆同代碼
            for pid, name in [(1, "Stanley Black and Decker Inc"),
                              (2, "STANLEY BLACK & DECKER, INC."),
                              (3, "Stanley Black & Decker (US)")]:
                self._seed_patent(c, pid, {"申請人": name, "標準化申請人": name, "申請人代表碼": "UNX"})
            c.commit()
        rows = self._refresh_and_read()
        names = {r[1] for r in rows}
        self.assertEqual(names, {"Stanley Black & Decker"},
                         f"同代碼三筆應收斂到同一顯示名，實得 {names}")

    def test_no_code_falls_back_to_alias(self):
        """沒有代碼時仍走別稱——代碼優先不得讓既有別稱收斂失效。"""
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼","公司名稱","別稱") '
                "VALUES ('UNZ','Makita Corp.','Makita Corporation')"
            )
            self._seed_patent(c, 1, {"申請人": "Makita Corporation", "標準化申請人": "Makita Corporation"})
            c.commit()
        rows = self._refresh_and_read()
        self.assertEqual(rows[0][1], "Makita Corp.", "無代碼時應回退別稱對照")

    def test_assignee_code_wins_over_alias(self):
        """current_assignee_display_name 亦以「標準當前專利權人代碼」優先。"""
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼","公司名稱","別稱") '
                "VALUES ('OWNCODE','OwnerByCode','Owner Ltd')"
            )
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼","公司名稱","別稱") '
                "VALUES ('OWNOTHER','OwnerByAlias','owner  ltd')"
            )
            self._seed_patent(c, 1, {
                "申請人": "Irrelevant",
                "標準當前專利權人[US,JP,KR,CN,CA,AU]": "Owner Ltd",
                "標準當前專利權人代碼[US,JP,KR,CN,CA,AU]": "OWNCODE",
            })
            c.commit()
        rows = self._refresh_and_read()
        self.assertEqual(rows[0][2], "OwnerByCode", "專利權人代碼命中應優先於別稱")

    def test_recent_assignee_has_no_code_column_so_alias_only(self):
        """最近受讓人無對應代碼欄，維持別稱路徑（釘住現況，避免誤加不存在的欄）。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            cols = c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='core_layer' AND table_name='patent_people'"
            ).fetchall()
        names = {r[0] for r in cols}
        self.assertNotIn("最近受讓人代碼[US,KR,CN]", names,
                         "若來源新增受讓人代碼欄，recent_assignee 也應改走代碼優先")
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼","公司名稱","別稱") '
                "VALUES ('UNR','Recent Co','Recent Assignee Ltd')"
            )
            self._seed_patent(c, 1, {"申請人": "X", "最近受讓人[US,KR,CN]": "Recent Assignee Ltd"})
            c.commit()
        rows = self._refresh_and_read()
        self.assertEqual(rows[0][3], "Recent Co")


if __name__ == "__main__":
    unittest.main()
