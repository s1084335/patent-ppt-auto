"""Claim 文字抽取契約（拋棄式 DB patent_ppt_claimsrc）。

fixture 用實查欄名（information_schema 2026-07-21）種三案例：
主欄「所有權利要求[JP,KR,CN]」有值／主欄空後備「獨立項[KR,JP,US,CN,EP,IN]」有值／兩者皆空。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_claimsrc"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.getenv("PGPASSWORD")
    return kw


def setUpModule():
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
        # 用實查欄名寫入；A 主欄有值、B 主欄空後備獨立項有值、C 兩者皆空
        c.execute('INSERT INTO core_layer.patents (id, title, "所有權利要求[JP,KR,CN]", '
                  '"獨立項[KR,JP,US,CN,EP,IN]") VALUES (930001, \'A\', %s, %s)',
                  ("主欄權利要求全文", "獨立項備援文字A"))
        c.execute('INSERT INTO core_layer.patents (id, title, "所有權利要求[JP,KR,CN]", '
                  '"獨立項[KR,JP,US,CN,EP,IN]") VALUES (930002, \'B\', %s, %s)',
                  ("   ", "獨立項備援文字B"))
        c.execute('INSERT INTO core_layer.patents (id, title, "所有權利要求[JP,KR,CN]", '
                  '"獨立項[KR,JP,US,CN,EP,IN]") VALUES (930003, \'C\', NULL, NULL)')
        c.commit()


def tearDownModule():
    for k, v in _prev_env.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    except Exception:  # noqa: BLE001
        pass


class ClaimSourceTests(unittest.TestCase):
    def _extract(self, pid):
        from backend.app.comparison.claim_source import extract_claim_source
        return extract_claim_source(pid)

    def test_primary_all_claims_used(self):
        r = self._extract(930001)
        self.assertEqual(r["text"], "主欄權利要求全文")
        self.assertEqual(r["source_fields"], ["所有權利要求"])

    def test_fallback_independent_when_primary_empty(self):
        r = self._extract(930002)
        self.assertEqual(r["text"], "獨立項備援文字B")
        self.assertEqual(r["source_fields"], ["獨立項"])  # 從屬項文字欄不存在，只取獨立項並標缺口

    def test_all_empty_raises(self):
        from backend.app.comparison.claim_source import ClaimSourceEmptyError
        with self.assertRaises(ClaimSourceEmptyError):
            self._extract(930003)

    def test_missing_patent_raises(self):
        from backend.app.comparison.claim_source import ClaimSourceNotFoundError
        with self.assertRaises(ClaimSourceNotFoundError):
            self._extract(999999)

    def test_source_fields_pass_claim_model_whitelist(self):
        # 抽取回傳的 source_fields 必須在 claim_model 白名單內
        from backend.app.comparison.claim_model import ALLOWED_SOURCE_FIELDS
        for pid in (930001, 930002):
            for f in self._extract(pid)["source_fields"]:
                self.assertIn(f, ALLOWED_SOURCE_FIELDS)


if __name__ == "__main__":
    unittest.main()
