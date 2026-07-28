"""company_alias_importer：匯入去重與正規化欄名對應。

涵蓋 2026-07-23 修正的兩個缺陷：
- 缺陷 1：主流程未依 DB 唯一索引同一把 key 去重，來源檔含大小寫變體時
  整批 UniqueViolation rollback。去重 key 隨「代碼是收斂依據」定案改為
  (申請人代碼, normalize_lookup(別稱))，與 0030 的複合唯一索引一致。
- 缺陷 2（複核）：normalize_alias_rows 讀來源檔的中文表頭、輸出英文 key，
  實際為載入路徑而非死程式；本測試釘住此契約避免被誤刪。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from openpyxl import Workbook

TEST_DB = "patent_ppt_alias_import"
# 必須升到 head：0030 起唯一索引為 (申請人代碼, alias_lookup_key)，
# 停在舊版本會讓 importer 的 ON CONFLICT 找不到對應約束。
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


class NormalizeAliasRowsTests(unittest.TestCase):
    """normalize_alias_rows 的欄名契約（不需 DB）。"""

    def test_reads_chinese_headers_and_emits_english_keys(self):
        """來源檔表頭為中文；輸出 key 為英文，供 SQL 具名參數使用。"""
        from backend.app.derived.company_alias_importer import normalize_alias_rows
        rows = normalize_alias_rows([
            {"申請人代碼": "UN1", "正規化名稱": "Alpha Corp", "別稱": "Alpha"},
            {"申請人代碼": "UN1", "正規化名稱": "Alpha Corp", "別稱": "ALPHA CORP"},
        ])
        self.assertEqual(len(rows), 2, "中文表頭應被正確讀取，不得整批濾光")
        # 2026-07-28 四欄：輸出 key 隨對照檔拆成 zh_name／normalized_name 兩個。
        self.assertEqual(
            set(rows[0]),
            {"company_code", "zh_name", "normalized_name", "alias_name"})
        self.assertEqual(rows[0]["normalized_name"], "Alpha Corp")
        self.assertIsNone(rows[0]["zh_name"], "沒填中文欄就該是 None，不得回填英文名")

    def test_drops_rows_missing_required_values(self):
        """兩個名稱欄都空、或別稱缺，即略過（2026-07-28 四欄）。"""
        from backend.app.derived.company_alias_importer import normalize_alias_rows
        rows = normalize_alias_rows([
            {"申請人代碼": "UN1", "正規化名稱": "Alpha Corp", "別稱": ""},
            {"申請人代碼": "UN2", "正規化名稱": "", "別稱": "Beta"},
            {"申請人代碼": "UN3", "正規化名稱": "Gamma", "別稱": "Gamma Inc"},
        ])
        self.assertEqual([r["normalized_name"] for r in rows], ["Gamma"])

    def test_dedups_by_code_and_lookup_key_not_triple(self):
        """去重必須用 DB 唯一索引同一把 key（代碼＋normalize_lookup(別稱)），非三元組。

        Red：舊實作以 (代碼, 名稱, 別稱) 三元組去重，大小寫變體會漏抓。
        """
        from backend.app.derived.company_alias_importer import normalize_alias_rows
        rows, dropped = normalize_alias_rows([
            {"申請人代碼": "UN1", "正規化名稱": "Koki Holdings", "別稱": "KOKI HOLDINGS CO LTD"},
            {"申請人代碼": "UN1", "正規化名稱": "Koki Holdings", "別稱": "Koki Holdings Co Ltd"},
        ], with_dropped=True)
        self.assertEqual(len(rows), 1, "同代碼同 lookup key 只應留一筆")
        self.assertEqual(len(dropped), 1, "被丟棄的列必須回報，不得靜默丟棄")
        self.assertEqual(dropped[0]["alias_name"], "Koki Holdings Co Ltd")
        self.assertEqual(dropped[0]["kept_alias_name"], "KOKI HOLDINGS CO LTD")

    def test_same_alias_under_different_codes_both_kept(self):
        """一別稱多公司：同字面別稱分屬不同代碼時兩列都要留（代碼收斂的直接後果）。"""
        from backend.app.derived.company_alias_importer import normalize_alias_rows
        rows, dropped = normalize_alias_rows([
            {"申請人代碼": "UN1", "正規化名稱": "Alpha Corp", "別稱": "Shared Name"},
            {"申請人代碼": "UN2", "正規化名稱": "Beta Corp", "別稱": "shared  name"},
        ], with_dropped=True)
        self.assertEqual(len(rows), 2, "不同代碼的同一別稱不得互相排擠")
        self.assertEqual(len(dropped), 0)

    def test_dedup_is_generic_across_arbitrary_duplicates(self):
        """通用性：任意來源檔、任意重複量都要處理，不得寫死特定公司。"""
        from backend.app.derived.company_alias_importer import normalize_alias_rows
        records = []
        for i in range(50):
            records.append({"申請人代碼": f"C{i}", "正規化名稱": f"Co{i}", "別稱": f"Widget {i} Ltd"})
            records.append({"申請人代碼": f"C{i}", "正規化名稱": f"Co{i}", "別稱": f"WIDGET  {i}  LTD"})
        rows, dropped = normalize_alias_rows(records, with_dropped=True)
        self.assertEqual(len(rows), 50)
        self.assertEqual(len(dropped), 50)


class ImportCompanyAliasesTests(unittest.TestCase):
    """匯入主流程在拋棄式 DB 上的行為（含唯一索引撞鍵）。"""

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

    def setUp(self):
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute("TRUNCATE derived_layer.company_aliases")
            c.commit()

    def _write_xlsx(self, rows: list[tuple[str, str, str]]) -> Path:
        """輔助：以中文表頭寫出暫時 xlsx 來源檔。"""
        import tempfile
        wb = Workbook()
        ws = wb.active
        ws.append(["申請人代碼", "公司中文名稱", "正規化名稱", "別稱"])
        for r in rows:
            ws.append(list(r))
        path = Path(tempfile.mkdtemp()) / "alias.xlsx"
        wb.save(path)
        return path

    def _count(self) -> int:
        with psycopg.connect(**_rw(TEST_DB)) as c:
            return c.execute("SELECT count(*) FROM derived_layer.company_aliases").fetchone()[0]

    def test_import_succeeds_with_case_variant_duplicates(self):
        """Red 核心：來源檔含大小寫重複時匯入應成功，不得整批 rollback。"""
        path = self._write_xlsx([
            ("UN1", "Koki Holdings", "KOKI HOLDINGS CO LTD"),
            ("UN1", "Koki Holdings", "Koki Holdings Co Ltd"),
            ("UN2", "Rexon", "REXON Industrial Corporation Ltd"),
            ("UN2", "Rexon", "Rexon Industrial Corporation Ltd"),
            ("UN3", "Makita", "Makita Corp"),
        ])
        from backend.app.derived.company_alias_importer import import_company_aliases
        summary = import_company_aliases(path, connect_kwargs=_rw(TEST_DB))
        self.assertEqual(summary["status"], "imported")
        self.assertEqual(self._count(), 3, "去重後 3 列應全部進庫")

    def test_import_reports_dropped_duplicates_in_summary(self):
        """被丟棄的列必須記入 summary 警告（沿 wips_importer figure_warnings 慣例）。"""
        path = self._write_xlsx([
            ("UN1", "Koki Holdings", "KOKI HOLDINGS CO LTD"),
            ("UN1", "Koki Holdings", "Koki Holdings Co Ltd"),
        ])
        from backend.app.derived.company_alias_importer import import_company_aliases
        summary = import_company_aliases(path, connect_kwargs=_rw(TEST_DB))
        self.assertEqual(summary["rows"], 1)
        self.assertEqual(summary["duplicate_dropped"], 1)
        self.assertEqual(len(summary["duplicate_warnings"]), 1)
        w = summary["duplicate_warnings"][0]
        self.assertEqual(w["alias_name"], "Koki Holdings Co Ltd")
        self.assertEqual(w["kept_alias_name"], "KOKI HOLDINGS CO LTD")
        self.assertEqual(w["lookup_key"], "koki holdings co ltd")

    def test_import_is_idempotent(self):
        """重跑同一檔不應增加列數或拋錯。"""
        path = self._write_xlsx([
            ("UN1", "Alpha Corp", "Alpha"),
            ("UN1", "Alpha Corp", "ALPHA"),
        ])
        from backend.app.derived.company_alias_importer import import_company_aliases
        import_company_aliases(path, connect_kwargs=_rw(TEST_DB))
        first = self._count()
        import_company_aliases(path, connect_kwargs=_rw(TEST_DB))
        self.assertEqual(self._count(), first)

    def test_dry_run_does_not_write(self):
        """dry_run 只讀不寫，且仍回報去重統計。"""
        path = self._write_xlsx([
            ("UN1", "Alpha Corp", "Alpha"),
            ("UN1", "Alpha Corp", "alpha"),
        ])
        from backend.app.derived.company_alias_importer import import_company_aliases
        summary = import_company_aliases(path, dry_run=True)
        self.assertEqual(summary["status"], "dry_run")
        self.assertEqual(summary["duplicate_dropped"], 1)
        self.assertEqual(self._count(), 0)


if __name__ == "__main__":
    unittest.main()
