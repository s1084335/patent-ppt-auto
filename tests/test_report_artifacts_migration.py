"""0025 report_artifacts 表契約測試（獨立測試 DB，不碰 patent_ppt）。

db patent_ppt_repart → upgrade head，驗證 app_layer.report_artifacts 欄位定版、
bytea 型別、(version, filename) 複合主鍵、同名 upsert 覆蓋、單檔取回，與 downgrade 可移除表。
"""
from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_repart"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 表定版 6 欄：version / filename / content / file_hash / byte_size / created_at
EXPECTED_COLUMNS = {"version", "filename", "content", "file_hash", "byte_size", "created_at"}


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


class ReportArtifactsMigrationTests(unittest.TestCase):
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
        """欄位定版且 content 為 bytea（不是 text/base64，SVG 不做無謂放大）。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='app_layer' AND table_name='report_artifacts'"
            ).fetchall()
        cols = {r[0]: r[1] for r in rows}
        self.assertEqual(set(cols), EXPECTED_COLUMNS)
        self.assertEqual(cols["content"], "bytea")

    def test_composite_primary_key_version_filename(self):
        """PK＝(version, filename)：一個版本的一個檔名只有一列。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                """
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = 'app_layer.report_artifacts'::regclass AND i.indisprimary
                ORDER BY array_position(i.indkey, a.attnum)
                """
            ).fetchall()
        self.assertEqual([r[0] for r in rows], ["version", "filename"])

    def test_upsert_overwrites_same_file_in_same_version(self):
        """同版本重跑同名檔 upsert 覆蓋（不留半新半舊的產物）。"""
        first, second = b"<svg>old</svg>", b"<svg>new</svg>"
        sql = (
            "INSERT INTO app_layer.report_artifacts (version, filename, content, file_hash, byte_size) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (version, filename) DO UPDATE SET content = EXCLUDED.content, "
            "file_hash = EXCLUDED.file_hash, byte_size = EXCLUDED.byte_size"
        )
        with psycopg.connect(**_kw(TEST_DB)) as c:
            for content in (first, second):
                c.execute(sql, ("report_trial_x", "a.svg", content,
                                hashlib.sha256(content).hexdigest(), len(content)))
            c.commit()
            rows = c.execute(
                "SELECT content, byte_size FROM app_layer.report_artifacts "
                "WHERE version = %s AND filename = %s", ("report_trial_x", "a.svg")).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(bytes(rows[0][0]), second)
        self.assertEqual(rows[0][1], len(second))

    def test_store_roundtrip_upload_then_read_and_list(self):
        """跨容器真流程：store 上傳一整個報表目錄 → 單檔取回 → 列版本，全走真實 DB。

        這是「寫入端與讀取端不共享檔案系統」的實質保證：upload 後把本機目錄整個刪掉，
        再從 DB 取回內容仍完全一致。
        """
        import shutil
        import tempfile
        from unittest import mock

        from backend.app.db import report_artifact_store

        version = "report_trial_20260723_999999"
        tmp = Path(tempfile.mkdtemp(prefix="report_store_roundtrip_"))
        # 用專屬連線指向這顆拋棄式 DB，不動全域 lazy pool（它可能已連到別的 db）。
        conn = psycopg.connect(**_kw(TEST_DB))
        fake_pool = mock.MagicMock()
        fake_pool.connection.return_value.__enter__.return_value = conn
        fake_pool.connection.return_value.__exit__.return_value = False
        try:
            with mock.patch.object(report_artifact_store, "get_pool", return_value=fake_pool):
                run_dir = tmp / version
                run_dir.mkdir()
                (run_dir / "report_data.json").write_text(
                    '{"sections": [{"title": "趨勢"}]}', encoding="utf-8")
                (run_dir / "annual_trend.svg").write_text("<svg>趨勢</svg>", encoding="utf-8")
                (run_dir / "narratives.json").write_text(
                    f'{{"based_on_version": "{version}"}}', encoding="utf-8")
                uploaded = report_artifact_store.upload_run_dir(run_dir)
                # 模擬讀取端容器：本機完全沒有這份產物，只能從 DB 取。
                shutil.rmtree(run_dir)

                self.assertEqual(uploaded, 3)
                self.assertEqual(
                    report_artifact_store.read_file(version, "annual_trend.svg"),
                    "<svg>趨勢</svg>".encode("utf-8"),
                )
                self.assertIsNone(report_artifact_store.read_file(version, "nope.svg"))
                entry = next(
                    v for v in report_artifact_store.list_versions() if v["version"] == version)
                self.assertTrue(entry["has_narratives"])
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_downgrade_removes_table(self):
        """downgrade 到 0024 可移除表；再 upgrade 回 head 不影響其他測試。

        ⚠ 指定**絕對 revision** 而非相對 "-1"：0025 之後只要再有新 migration，
        "-1" 退掉的就是那一版而不是 0025，本測試會假性失敗。
        """
        cfg = _alembic_cfg()
        command.downgrade(cfg, "0024_import_blobs")
        with psycopg.connect(**_kw(TEST_DB)) as c:
            exists = c.execute("SELECT to_regclass('app_layer.report_artifacts')").fetchone()[0]
        self.assertIsNone(exists)
        command.upgrade(cfg, "head")


if __name__ == "__main__":
    unittest.main()
