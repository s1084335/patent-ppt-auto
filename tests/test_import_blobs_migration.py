"""0024 import_blobs 表契約測試（獨立測試 DB，不碰 patent_ppt）。

db patent_ppt_impblob → upgrade head，驗證 app_layer.import_blobs 欄位定版、bytea 型別、
round-trip（分塊 append 後內容與 hash 相符）與 downgrade 可移除表。
"""
from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_impblob"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 表定版 5 欄：blob_id / original_filename / content / file_hash / byte_size
EXPECTED_COLUMNS = {"blob_id", "original_filename", "content", "file_hash", "byte_size", "created_at"}


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


class ImportBlobsMigrationTests(unittest.TestCase):
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

    def test_table_columns_and_types(self):
        """欄位定版且 content 為 bytea（不是 text/base64，不做無謂放大）。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='app_layer' AND table_name='import_blobs'"
            ).fetchall()
        cols = {r[0]: r[1] for r in rows}
        self.assertEqual(set(cols), EXPECTED_COLUMNS)
        self.assertEqual(cols["content"], "bytea")

    def test_chunked_append_roundtrip(self):
        """分塊 append 後內容完整、hash 相符（上傳端串流語意的 DB 側保證）。"""
        chunks = [b"col_a,col_b\n", b"v1,v2\n", b"v3,v4\n"]
        full = b"".join(chunks)
        digest = hashlib.sha256(full).hexdigest()
        with psycopg.connect(**_kw(TEST_DB)) as c:
            blob_id = c.execute(
                "INSERT INTO app_layer.import_blobs (original_filename) VALUES (%s) "
                "RETURNING blob_id", ("t.csv",)).fetchone()[0]
            for chunk in chunks:
                c.execute(
                    "UPDATE app_layer.import_blobs SET content = content || %s WHERE blob_id = %s",
                    (chunk, blob_id))
            c.execute(
                "UPDATE app_layer.import_blobs SET file_hash = %s, byte_size = %s WHERE blob_id = %s",
                (digest, len(full), blob_id))
            c.commit()
            row = c.execute(
                "SELECT content, file_hash, byte_size FROM app_layer.import_blobs WHERE blob_id = %s",
                (blob_id,)).fetchone()
        self.assertEqual(bytes(row[0]), full)
        self.assertEqual(row[1], digest)
        self.assertEqual(row[2], len(full))

    def test_downgrade_removes_table(self):
        """downgrade 到 0024 的前一版可移除表；再 upgrade 回 head 不影響其他測試。

        ⚠ 這裡指定**絕對 revision**（0023）而非相對的 "-1"：0024 之後只要再有新
        migration（如 0025），"-1" 退掉的就是那一版而不是 0024，本測試會假性失敗。
        """
        cfg = _alembic_cfg()
        command.downgrade(cfg, "0023_market_evidence")
        with psycopg.connect(**_kw(TEST_DB)) as c:
            exists = c.execute(
                "SELECT to_regclass('app_layer.import_blobs')").fetchone()[0]
        self.assertIsNone(exists)
        command.upgrade(cfg, "head")


if __name__ == "__main__":
    unittest.main()
