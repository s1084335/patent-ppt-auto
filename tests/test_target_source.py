"""標的資料抽取契約（拋棄式 DB patent_ppt_targetsrc，絕不碰 patent_ppt）。

第二階段前置：以一件專利模擬標的（資料庫為唯一來源）。
- 文字欄自 core_layer.patents 取 title、abstract、"主權項"。
- PDF 連結自 core_layer.patent_attributes."文圖像文件(PDF)連結"，
  同 patent 多列取 raw_record_id 最大者（2026-07-21 實查定案）。
- 存 comparison run：output_type='target' 版本 append，payload 必須明確標 simulated。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_targetsrc"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.getenv("PGPASSWORD")
    return kw


def setUpModule():
    """建拋棄式 DB → upgrade head → 種標的案例；admin 不可用則整組 skip。"""
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    with psycopg.connect(**_kw(TEST_DB)) as c:
        # raw_records：patent_attributes.raw_record_id NOT NULL＋FK，需先種
        for rid in (1, 2):
            c.execute(
                "INSERT INTO raw_layer.raw_records "
                "(id, sheet_name, row_number, raw_data, source_system, source_file_hash) "
                "VALUES (%s, 's1', %s, '{}', 'test', 'hash')", (rid, rid))
        # 940001：全欄有值；attributes 兩列（raw_record_id 1 舊連結、2 新連結）→ 取最大者
        c.execute('INSERT INTO core_layer.patents (id, title, abstract, "主權項", "授權公告號") '
                  "VALUES (940001, '鋸切機', '一種鋸切機摘要', '主權項全文', 'TWI940001')")
        c.execute('INSERT INTO core_layer.patent_attributes '
                  '(patent_id, raw_record_id, "文圖像文件(PDF)連結") '
                  "VALUES (940001, 1, 'https://example.test/old.pdf')")
        c.execute('INSERT INTO core_layer.patent_attributes '
                  '(patent_id, raw_record_id, "文圖像文件(PDF)連結") '
                  "VALUES (940001, 2, 'https://example.test/new.pdf')")
        # 940002：有文字、無 attributes 列 → pdf_url 為 None 仍屬有效標的
        c.execute('INSERT INTO core_layer.patents (id, title, abstract, "主權項") '
                  "VALUES (940002, '跑步機', NULL, '跑步機主權項')")
        # 940003：文字欄全空 → 應拋明確錯誤
        c.execute('INSERT INTO core_layer.patents (id, title, abstract, "主權項") '
                  "VALUES (940003, '', NULL, '   ')")
        c.commit()


def tearDownModule():
    for k, v in _prev_env.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    except Exception:  # noqa: BLE001
        pass


def _scalar(sql: str, params=()):
    with psycopg.connect(**_kw(TEST_DB)) as c:
        row = c.execute(sql, params).fetchone()
    return row[0] if row else None


class ExtractTargetTests(unittest.TestCase):
    """extract_target_from_db：欄位組裝、pdf_url 取最大 raw_record_id、錯誤路徑。"""

    def _extract(self, pid):
        from backend.app.comparison.target_source import extract_target_from_db
        return extract_target_from_db(pid)

    def test_full_target_fields(self):
        t = self._extract(940001)
        self.assertEqual(t["patent_id"], 940001)
        self.assertEqual(t["patent_number"], "TWI940001")
        self.assertEqual(t["title"], "鋸切機")
        self.assertEqual(t["abstract"], "一種鋸切機摘要")
        self.assertEqual(t["main_claim"], "主權項全文")
        # 同 patent 多列取 raw_record_id 最大者
        self.assertEqual(t["pdf_url"], "https://example.test/new.pdf")
        # 模擬標的必須明確標注；來源註記需指出資料表
        self.assertIs(t["simulated"], True)
        self.assertIn("core_layer.patents", t["source"]["tables"])
        self.assertIn("core_layer.patent_attributes", t["source"]["tables"])

    def test_no_pdf_link_still_valid(self):
        t = self._extract(940002)
        self.assertIsNone(t["pdf_url"])
        self.assertEqual(t["main_claim"], "跑步機主權項")

    def test_all_text_empty_raises(self):
        from backend.app.comparison.target_source import TargetSourceEmptyError
        with self.assertRaises(TargetSourceEmptyError):
            self._extract(940003)

    def test_missing_patent_raises(self):
        from backend.app.comparison.target_source import TargetSourceNotFoundError
        with self.assertRaises(TargetSourceNotFoundError):
            self._extract(999999)


class SaveTargetTests(unittest.TestCase):
    """save_target：output_type='target' 版本 append；payload 必須明確標 simulated。"""

    def _store(self):
        from backend.app.comparison.comparison_store import ComparisonStore
        return ComparisonStore()

    def test_save_target_versioned_append(self):
        store = self._store()
        run_id = store.create_case("TWI940001", "模擬標的（以專利 940001 充當）", "smoke")
        from backend.app.comparison.target_source import extract_target_from_db
        target = extract_target_from_db(940001)
        v1 = store.save_target(run_id, target)
        v2 = store.save_target(run_id, target)
        self.assertEqual((v1, v2), (1, 2))
        self.assertEqual(_scalar(
            "SELECT data_json->>'simulated' FROM app_layer.workflow_outputs "
            "WHERE run_id=%s AND output_type='target' AND version=1", (run_id,)), "true")

    def test_save_target_requires_simulated_flag(self):
        store = self._store()
        run_id = store.create_case("TWI940001", "target", "smoke")
        with self.assertRaises(ValueError):
            store.save_target(run_id, {"title": "缺 simulated 標記"})
        self.assertEqual(_scalar(
            "SELECT count(*) FROM app_layer.workflow_outputs "
            "WHERE run_id=%s AND output_type='target'", (run_id,)), 0)


if __name__ == "__main__":
    unittest.main()
