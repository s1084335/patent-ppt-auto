"""alias_variant_sweep：專利權人別稱掃描與註冊。"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_sweep"
CURATION_DB = "patent_ppt_curation"
# 升到 head：0030 起 company_aliases 唯一索引為 (申請人代碼, alias_lookup_key)，
# 停在舊版本測不到「一別稱多公司」的實際約束行為。
HEAD_REV = "head"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.environ["PGPASSWORD"]
    return kw


def _rw(dbname: str) -> dict:
    kw = _kw(dbname)
    kw["options"] = "-c search_path=derived_layer,core_layer,raw_layer,public"
    return kw


class AliasVariantSweepTests(unittest.TestCase):
    """驗證 alias_variant_sweep 在拋棄式 DB 上的行為。"""

    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        command.upgrade(cfg, HEAD_REV)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:
            pass

    _seq = 0

    def _insert_people(self, code: str, name: str, std_name: str | None = None):
        """輔助：插入一筆 patent_people。"""
        AliasVariantSweepTests._seq += 1
        seq = AliasVariantSweepTests._seq
        with psycopg.connect(**_rw(TEST_DB)) as c:
            with c.cursor() as cur:
                cur.execute(
                    "INSERT INTO core_layer.patents (id, title) VALUES (DEFAULT, 'test') RETURNING id")
                pid = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO raw_layer.raw_records (sheet_name, row_number, raw_data, "
                    "source_system, source_file_hash, imported_at) "
                    "VALUES ('S', %s, '{}'::jsonb, 'WIPS', 'h'||%s, now()) RETURNING id",
                    (seq, seq))
                rid = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO core_layer.patent_sources (patent_id, raw_record_id) VALUES (%s, %s)",
                    (pid, rid))
                cur.execute(
                    "INSERT INTO core_layer.patent_people "
                    "(patent_id, \"申請人代表碼\", \"申請人\", \"標準化申請人\") "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (patent_id) DO UPDATE SET \"申請人代表碼\"=EXCLUDED.\"申請人代表碼\"",
                    (pid, code, name, std_name))
            c.commit()
            return pid

    def _count_aliases(self, code: str) -> int:
        """回傳指定 code 在 company_aliases 的列數。"""
        with psycopg.connect(**_rw(TEST_DB)) as c:
            return c.execute(
                'SELECT count(*) FROM derived_layer.company_aliases WHERE "申請人代碼"=%s',
                (code,)).fetchone()[0]

    def test_sweep_auto_inserts_new_variants(self):
        """唯一 code 有新變體時自動補入別稱。"""
        # 先建立既有對照 (code X → 公司A)
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼", "公司名稱", "別稱", source_file) '
                "VALUES ('X001', '公司A', '公司A', 'setup')")
            c.commit()
        # 新增 patent_people 有 code X001 但名稱是「公司A(Inc.)」
        self._insert_people("X001", "公司A(Inc.)", "公司A")
        # 執行 sweep
        from backend.app.derived.alias_variant_sweep import sweep_and_report
        result = sweep_and_report(connect_kwargs={"options": "-c search_path=derived_layer,core_layer,raw_layer,public",
                                                    **_kw(TEST_DB)})
        self.assertGreater(result["inserted"], 0, "應自動補入新變體")
        self.assertGreaterEqual(self._count_aliases("X001"), 2)

    def test_sweep_skips_existing_aliases(self):
        """既有別稱（normalize 後相同）跳過不重複插入。"""
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼", "公司名稱", "別稱", source_file) '
                "VALUES ('Y001', '公司Y', '公司Y', 'setup')")
            c.commit()
        self._insert_people("Y001", "公司Y")
        # 第一次 sweep 應插入
        from backend.app.derived.alias_variant_sweep import sweep_and_report
        r1 = sweep_and_report(connect_kwargs={"options": "-c search_path=derived_layer,core_layer,raw_layer,public",
                                               **_kw(TEST_DB)})
        # 第二次 sweep 應全部跳過
        r2 = sweep_and_report(connect_kwargs={"options": "-c search_path=derived_layer,core_layer,raw_layer,public",
                                               **_kw(TEST_DB)})
        self.assertEqual(r2["inserted"], 0, "第二次不應有新插入")
        self.assertGreater(r2["skipped_existing"], 0, "應回報已跳過的既有別稱")

    def test_sweep_unknown_code_goes_manual(self):
        """不在 company_aliases 的 code → manual_review，不寫表。"""
        self._insert_people("Z999", "未知公司Z")
        from backend.app.derived.alias_variant_sweep import sweep_and_report
        r = sweep_and_report(connect_kwargs={"options": "-c search_path=derived_layer,core_layer,raw_layer,public",
                                              **_kw(TEST_DB)})
        self.assertGreater(len(r["manual_review"]), 0)
        self.assertEqual(
            r["manual_review"][0]["reason"], "unknown_code")
        self.assertEqual(self._count_aliases("Z999"), 0)

    def test_sweep_output_manual_review_html(self):
        """manual_review 輸出單頁 HTML 存到 output/。"""
        import tempfile
        from backend.app.derived.alias_variant_sweep import write_manual_review_html
        manual = [
            {"company_code": "M001", "alias_name": "Mystery Corp", "reason": "unknown_code"},
            {"company_code": "M002", "alias_name": "Dual Inc.", "reason": "conflicting_code"},
        ]
        out_dir = Path(tempfile.mkdtemp())
        path = write_manual_review_html(manual, out_dir)
        self.assertTrue(path.exists())
        self.assertIn("manual_review", path.name)
        html = path.read_text(encoding="utf-8")
        self.assertIn("M001", html)
        self.assertIn("M002", html)
        self.assertIn("unknown_code", html)
        self.assertIn("conflicting_code", html)
        # cleanup
        path.unlink()
        out_dir.rmdir()

    def test_sweep_returns_statistics(self):
        """回傳 inserted / skipped_existing / manual_review 統計。"""
        from backend.app.derived.alias_variant_sweep import sweep_and_report
        r = sweep_and_report(connect_kwargs={"options": "-c search_path=derived_layer,core_layer,raw_layer,public",
                                              **_kw(TEST_DB)})
        self.assertIn("inserted", r)
        self.assertIn("skipped_existing", r)
        self.assertIn("manual_review", r)
        self.assertIsInstance(r["manual_review"], list)


class DisplayNameGovernanceTests(unittest.TestCase):
    """公司顯示名 curation upsert 與名稱治理管線（匯入/sweep 共用核心）的 needs_zh_name 偵測。"""

    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{CURATION_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{CURATION_DB}"')
        except Exception as exc:
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = CURATION_DB
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        command.upgrade(cfg, HEAD_REV)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{CURATION_DB}" WITH (FORCE)')
        except Exception:
            pass

    def _seed_alias(self, code, name, alias, source_file="setup", review_status="confirmed"):
        """輔助：直接種一列對照（模擬既有資料）。"""
        with psycopg.connect(**_rw(CURATION_DB)) as c:
            c.execute(
                'INSERT INTO derived_layer.company_aliases '
                '("申請人代碼", "公司名稱", "別稱", source_file, review_status) '
                "VALUES (%s, %s, %s, %s, %s)",
                (code, name, alias, source_file, review_status))
            c.commit()

    def _rows(self, code):
        """輔助：取指定 code 的 (公司名稱, 別稱, source_file, review_status) 列。"""
        with psycopg.connect(**_rw(CURATION_DB)) as c:
            return c.execute(
                'SELECT "公司名稱", "別稱", source_file, review_status '
                'FROM derived_layer.company_aliases WHERE "申請人代碼"=%s ORDER BY id',
                (code,)).fetchall()

    def test_apply_inserts_new_confirmed_rows(self):
        """upsert 三態之一：全新 lookup key → INSERT confirmed，canonical 自身納入別稱。"""
        from backend.app.derived.company_alias_importer import apply_confirmed_display_names
        result = apply_confirmed_display_names(
            {"C100": {"canonical": "甲公司", "aliases": ["Alpha Tools Inc.", "Alpha Tools Corporation"]}},
            source_label="display_name_curation_test",
            connect_kwargs=_rw(CURATION_DB))
        self.assertEqual(result["inserted"], 3)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["dedup_dropped"], 0)
        rows = self._rows("C100")
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r[0] == "甲公司" and r[3] == "confirmed" for r in rows))
        self.assertIn("甲公司", [r[1] for r in rows], "canonical 自身應納入別稱")

    def test_apply_updates_existing_key_without_unique_violation(self):
        """upsert 三態之二：lookup key 已存在（confirmed）→ UPDATE 該列 re-canonicalize，不拋 UniqueViolation。"""
        self._seed_alias("C200", "BETA CORP", "Beta Corp")
        from backend.app.derived.company_alias_importer import apply_confirmed_display_names
        # 「BETA  corp」大小寫＋空白變體 normalize 後與既有「Beta Corp」同 lookup key，
        # 天真 INSERT 會撞 ux_company_aliases_lookup_confirmed；正確行為是 UPDATE 既有列。
        result = apply_confirmed_display_names(
            {"C200": {"canonical": "乙公司", "aliases": ["BETA  corp"]}},
            source_label="display_name_curation_test",
            connect_kwargs=_rw(CURATION_DB))
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["inserted"], 1)  # canonical 乙公司為新 key
        rows = self._rows("C200")
        self.assertEqual(len(rows), 2, "撞鍵應更新既有列，不插新列")
        by_alias = {r[1]: r for r in rows}
        seeded = by_alias["Beta Corp"]
        self.assertEqual(seeded[0], "乙公司")
        self.assertEqual(seeded[2], "display_name_curation_test")
        self.assertEqual(seeded[3], "confirmed")

    def test_apply_updates_review_required_row_to_confirmed(self):
        """撞鍵不限 confirmed：review_required 列被裁決後轉 confirmed。"""
        self._seed_alias("C210", "GAMMA", "Gamma LLC", review_status="review_required")
        from backend.app.derived.company_alias_importer import apply_confirmed_display_names
        result = apply_confirmed_display_names(
            {"C210": {"canonical": "丙公司", "aliases": ["Gamma LLC"]}},
            source_label="display_name_curation_test",
            connect_kwargs=_rw(CURATION_DB))
        self.assertEqual(result["updated"], 1)
        rows = self._rows("C210")
        statuses = {r[1]: r[3] for r in rows}
        self.assertEqual(statuses["Gamma LLC"], "confirmed")

    def test_apply_dedups_within_batch(self):
        """upsert 三態之三：批內去重 key＝(代碼, lookup key)，同代碼字面變體去重、跨代碼各自保留。

        2026-07-23「代碼是收斂依據」定案後改為代碼層級去重：
        舊行為跨 code 共用 key 空間，會把第二個代碼的顯示名整列吃掉（C302 落 0 列），
        那是舊單欄唯一索引的副作用而非需求——不同代碼本來就可以有相同顯示名。
        """
        from backend.app.derived.company_alias_importer import apply_confirmed_display_names
        result = apply_confirmed_display_names(
            {
                "C300": {"canonical": "丁公司", "aliases": ["Delta Tools", "DELTA TOOLS", "delta  tools"]},
                "C301": {"canonical": "戊公司", "aliases": []},
                "C302": {"canonical": "戊公司", "aliases": []},
            },
            source_label="display_name_curation_test",
            connect_kwargs=_rw(CURATION_DB))
        # C300：丁公司＋Delta Tools 入庫，兩個大小寫/空白變體在同代碼內被去重（2 列丟棄）；
        # C301／C302 的「戊公司」代碼不同，各自落一列，不再互相排擠。
        self.assertEqual(result["dedup_dropped"], 2)
        self.assertEqual(result["inserted"], 4)
        self.assertEqual({r[1] for r in self._rows("C300")}, {"丁公司", "Delta Tools"})
        self.assertEqual(len(self._rows("C301")), 1)
        self.assertEqual(len(self._rows("C302")), 1)

    def test_govern_needs_zh_name_detection(self):
        """治理管線：中文 canonical 不列、英文 canonical 列入、已 curation 裁決（含保留原文）不列。"""
        self._seed_alias("N001", "中文公司", "中文公司")
        self._seed_alias("N002", "English Co", "English Co")
        # N003 已走過 curation（裁決＝保留原文），source_file 即裁決標記
        self._seed_alias("N003", "Keep Original Co", "Keep Original Co",
                         source_file="display_name_curation_20260721")
        from backend.app.derived.company_alias_importer import govern_company_names
        result = govern_company_names(
            [("N001", "中文公司 股份有限公司"), ("N002", "English Co Ltd."), ("N003", "Keep Original Co LLC")],
            source_label="test_intake",
            connect_kwargs=_rw(CURATION_DB))
        codes = {r["company_code"] for r in result["needs_zh_name"]}
        self.assertIn("N002", codes, "英文 canonical 應列入待中文化")
        self.assertNotIn("N001", codes, "中文 canonical 不應列入")
        self.assertNotIn("N003", codes, "已裁決保留原文者不得重複浮現")

    def test_import_path_reports_needs_zh_name(self):
        """匯入路徑（register_known_code_variants 薄包裝）自動帶出 needs_zh_name。"""
        self._seed_alias("N010", "Foreign Newco", "Foreign Newco")
        from backend.app.derived.company_alias_importer import register_known_code_variants
        result = register_known_code_variants(
            [("N010", "Foreign Newco Inc.")],
            source_label="import:test.xlsx",
            connect_kwargs=_rw(CURATION_DB))
        self.assertIn("needs_zh_name", result)
        self.assertIn("N010", {r["company_code"] for r in result["needs_zh_name"]})

    def test_manual_review_html_has_needs_zh_section(self):
        """manual_review HTML 需含「待中文化建議」節。"""
        import tempfile
        from backend.app.derived.alias_variant_sweep import write_manual_review_html
        out_dir = Path(tempfile.mkdtemp())
        path = write_manual_review_html(
            [], out_dir,
            needs_zh_name=[{"company_code": "Z100", "company_name": "Foreign Co"}])
        html = path.read_text(encoding="utf-8")
        self.assertIn("待中文化建議", html)
        self.assertIn("Z100", html)
        path.unlink()
        out_dir.rmdir()

    def test_sweep_reports_needs_zh_name_key(self):
        """sweep 為治理管線的全量觸發點：統計需帶 needs_zh_name 鍵。"""
        from backend.app.derived.alias_variant_sweep import sweep_and_report
        r = sweep_and_report(connect_kwargs={
            "options": "-c search_path=derived_layer,core_layer,raw_layer,public",
            **_kw(CURATION_DB)})
        self.assertIn("needs_zh_name", r)
        self.assertIsInstance(r["needs_zh_name"], list)


if __name__ == "__main__":
    unittest.main()
