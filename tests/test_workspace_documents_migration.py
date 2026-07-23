"""0027 workspace_documents 表契約測試（獨立測試 DB，不碰 patent_ppt）。

db patent_ppt_wsdoc → upgrade head，驗證 app_layer.workspace_documents 欄位定版（6 欄）、
bytea 型別、content STORAGE EXTERNAL、workspace_id FK ON DELETE CASCADE、分塊 append
round-trip，以及 downgrade 可移除表。
"""
from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_wsdoc"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 表定版 6 欄封頂：不存 byte_size（length(content) 可得）、不存 mime（magic number 推導）。
EXPECTED_COLUMNS = {
    "document_id",
    "workspace_id",
    "original_filename",
    "content",
    "file_hash",
    "uploaded_at",
}


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


class WorkspaceDocumentsMigrationTests(unittest.TestCase):
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

    def _new_workspace(self, conn, name: str) -> int:
        """建一個最小 workspace 供 FK 測試使用。"""
        return conn.execute(
            "INSERT INTO app_layer.workspaces (workspace_name) VALUES (%s) RETURNING workspace_id",
            (name,),
        ).fetchone()[0]

    def test_table_columns_and_types(self):
        """欄位定版 6 欄且 content 為 bytea（不是 text/base64，不做無謂放大）。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='app_layer' AND table_name='workspace_documents'"
            ).fetchall()
        cols = {r[0]: r[1] for r in rows}
        self.assertEqual(set(cols), EXPECTED_COLUMNS)
        self.assertEqual(cols["content"], "bytea")
        self.assertEqual(cols["uploaded_at"], "timestamp with time zone")

    def test_content_storage_external(self):
        """content 需 STORAGE EXTERNAL（PDF 已壓縮，再壓沒效益且吃 CPU）。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            storage = c.execute(
                "SELECT a.attstorage FROM pg_attribute a "
                "JOIN pg_class t ON t.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname='app_layer' AND t.relname='workspace_documents' "
                "AND a.attname='content'"
            ).fetchone()[0]
        # pg_attribute.attstorage：'e' = EXTERNAL（外置不壓縮），'x' = EXTENDED（預設，會壓縮）
        self.assertEqual(storage, "e")

    def test_workspace_fk_cascade_delete(self):
        """刪除 workspace 需連帶刪除其文獻（ON DELETE CASCADE），不留孤兒內容。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws_id = self._new_workspace(c, "ws-cascade")
            c.execute(
                "INSERT INTO app_layer.workspace_documents "
                "(workspace_id, original_filename, content) VALUES (%s, %s, %s)",
                (ws_id, "a.pdf", b"%PDF-1.4 body"),
            )
            c.commit()
            c.execute("DELETE FROM app_layer.workspaces WHERE workspace_id = %s", (ws_id,))
            c.commit()
            left = c.execute(
                "SELECT count(*) FROM app_layer.workspace_documents WHERE workspace_id = %s",
                (ws_id,),
            ).fetchone()[0]
        self.assertEqual(left, 0)

    def test_chunked_append_roundtrip_and_multiple_docs(self):
        """分塊 append 後內容完整、hash 相符；同一 workspace 可存多份文獻（不寫死只能一份）。"""
        chunks = [b"%PDF-1.4\n", b"stream-a\n", b"stream-b\n"]
        full = b"".join(chunks)
        digest = hashlib.sha256(full).hexdigest()
        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws_id = self._new_workspace(c, "ws-multi")
            doc_id = c.execute(
                "INSERT INTO app_layer.workspace_documents "
                "(workspace_id, original_filename) VALUES (%s, %s) RETURNING document_id",
                (ws_id, "first.pdf"),
            ).fetchone()[0]
            for chunk in chunks:
                c.execute(
                    "UPDATE app_layer.workspace_documents SET content = content || %s "
                    "WHERE document_id = %s",
                    (chunk, doc_id),
                )
            c.execute(
                "UPDATE app_layer.workspace_documents SET file_hash = %s WHERE document_id = %s",
                (digest, doc_id),
            )
            # 第二份文獻：同一 workspace 多份並存。
            c.execute(
                "INSERT INTO app_layer.workspace_documents "
                "(workspace_id, original_filename, content) VALUES (%s, %s, %s)",
                (ws_id, "second.pdf", b"%PDF-1.7 other"),
            )
            c.commit()
            row = c.execute(
                "SELECT content, file_hash, length(content) FROM app_layer.workspace_documents "
                "WHERE document_id = %s",
                (doc_id,),
            ).fetchone()
            count = c.execute(
                "SELECT count(*) FROM app_layer.workspace_documents WHERE workspace_id = %s",
                (ws_id,),
            ).fetchone()[0]
        self.assertEqual(bytes(row[0]), full)
        self.assertEqual(row[1], digest)
        # 不存 byte_size：length(content) 即可得，驗證這個推導成立。
        self.assertEqual(row[2], len(full))
        self.assertEqual(count, 2)

    def test_downgrade_removes_table(self):
        """downgrade 到 0027 的前一版可移除表；再 upgrade 回 head 不影響其他測試。

        ⚠ 指定**絕對 revision**（0026）而非相對 "-1"：之後再有新 migration 時，
        "-1" 退掉的會是那一版而非 0027，本測試會假性失敗。
        """
        cfg = _alembic_cfg()
        command.downgrade(cfg, "0026_patent_main_figure")
        with psycopg.connect(**_kw(TEST_DB)) as c:
            exists = c.execute("SELECT to_regclass('app_layer.workspace_documents')").fetchone()[0]
        self.assertIsNone(exists)
        command.upgrade(cfg, "head")


if __name__ == "__main__":
    unittest.main()
