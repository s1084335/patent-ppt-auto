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
HEAD_REV = "0021_derived_app_consolidation"
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


if __name__ == "__main__":
    unittest.main()
