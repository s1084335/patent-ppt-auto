"""文獻備註「搬主表＋接匯入自動鏈」契約與端到端回寫測試（0032）。

使用者定案（見任務）：
1. 文獻備註欄從 core_layer.patent_attributes 搬到 core_layer.patents 主表——
   一專利一列，AI 回寫直接 `UPDATE ... WHERE id`，不選 raw_record 列、不會 UPDATE 0 列靜默成功。
2. 文獻備註接進匯入後的自動 job 鏈（ai:patent_note 匯入後自動 enqueue，走 Companion）。

本檔三層：
- MigrationContractTests：migration 後 patents 有欄、patent_attributes 無欄、downgrade 可逆（真 DB）。
- WriteBackEndToEndTests：**本輪重點**——fake CLI 產假摘要，真 PatentNoteStore 對拋棄式 DB
  實跑「讀候選 → UPDATE patents.文獻備註 → 讀回主表有值」，證明真的寫進主表欄位。
- ImportEnqueueTests：匯入成功後自動 enqueue ai:patent_note；enqueue 失敗不影響匯入（mock jr，
  不真跑 CLI、不產生真 job）。

⚠ 全程拋棄式 DB（patent_ppt_notemain），絕不碰正式庫 patent_ppt；CLI 一律 fake，不真跑二進位。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

import psycopg
from alembic import command
from alembic.config import Config

from backend.app.worker import ai_patent_note_runner, handlers
from backend.app.worker.ai_narrative_runner import CliResult


TEST_DB = "patent_ppt_notemain"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# fixture id（避開正式資料範圍）。
PID_A = 950001  # 有獨立項、尚無備註 → 應被產生並寫回
PID_B = 950002  # 有獨立項、尚無備註
PID_NOCLAIM = 950003  # 無獨立項 → 不成批、不呼叫 CLI
RAW_A = 950201

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組拋棄式 DB 連線參數（PGPORT 預設 5433，密碼由 .env 的 PGPASSWORD 提供）。"""
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    password = os.getenv("PGPASSWORD")
    if password:
        kw["password"] = password
    return kw


def _seed():
    """灌 fixture：兩筆有獨立項、無備註；一筆無獨立項。走主表 core_layer.patents。"""
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        conn.execute(
            'INSERT INTO core_layer.patents (id, "主權項", country_code) VALUES (%s, %s, %s)',
            (PID_A, "一種阻力調節機構，包含固定座與磁控阻力盤……", "TW"),
        )
        conn.execute(
            'INSERT INTO core_layer.patents (id, "主權項", country_code) VALUES (%s, %s, %s)',
            (PID_B, "A resistance adjusting mechanism comprising a fixed seat ...", "US"),
        )
        conn.execute(
            'INSERT INTO core_layer.patents (id, "主權項", country_code) VALUES (%s, %s, %s)',
            (PID_NOCLAIM, "   ", "TW"),
        )
        conn.commit()


def setUpModule():
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = os.getenv("PGHOST", "127.0.0.1")
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
    command.upgrade(cfg, "head")
    _seed()


def tearDownModule():
    for k, v in _prev_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    except Exception:
        pass


def _column_exists(conn, table: str, column: str) -> bool:
    """查 core_layer.<table> 是否有 <column> 欄（migration 契約用）。"""
    row = conn.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'core_layer' AND table_name = %s AND column_name = %s
        """,
        (table, column),
    ).fetchone()
    return row is not None


class MigrationContractTests(unittest.TestCase):
    """0032：文獻備註欄搬到 patents 主表、從 patent_attributes 移除，downgrade 可逆。"""

    def test_note_column_on_patents_and_not_on_attributes(self):
        """upgrade head 後：patents 有「文獻備註」，patent_attributes 無。"""
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            self.assertTrue(_column_exists(conn, "patents", "文獻備註"))
            self.assertFalse(_column_exists(conn, "patent_attributes", "文獻備註"))

    def test_downgrade_then_upgrade_is_reversible(self):
        """downgrade 到 0031 → 欄回 patent_attributes；再 upgrade → 欄回 patents。"""
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        command.downgrade(cfg, "0031_patent_figures_paired")
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            self.assertFalse(_column_exists(conn, "patents", "文獻備註"))
            self.assertTrue(_column_exists(conn, "patent_attributes", "文獻備註"))
        command.upgrade(cfg, "head")
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            self.assertTrue(_column_exists(conn, "patents", "文獻備註"))
            self.assertFalse(_column_exists(conn, "patent_attributes", "文獻備註"))


class _FakeCli:
    """假 CLI runner：從 prompt 撈 patent_id，逐一回吐固定備註（不跑二進位）。"""

    def __init__(self, note: str):
        self.note = note

    def __call__(self, argv, timeout):
        import json

        # 2026-07-27 起獨立項走資料檔不走命令列（Windows 32,767 上限），
        # 故與真實 CLI 一樣讀 argv 內的資料檔，不再解析 prompt 字串。
        from tests.ai_payload_test_helpers import patent_ids_from_argv

        ids = patent_ids_from_argv(argv)
        notes = [{"patent_id": pid, "note": self.note} for pid in ids]
        return CliResult(
            exit_code=0,
            stdout=json.dumps({"result": json.dumps({"notes": notes}, ensure_ascii=False)}),
            stderr="",
        )


class WriteBackEndToEndTests(unittest.TestCase):
    """本輪重點：實跑回寫，證明真的寫進 core_layer.patents.文獻備註（非 UPDATE 0 列靜默成功）。"""

    def _note_of(self, pid: int) -> str | None:
        """讀回主表某專利的文獻備註（驗證真的落庫）。"""
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            row = conn.execute(
                'SELECT "文獻備註" FROM core_layer.patents WHERE id = %s', (pid,)
            ).fetchone()
        return row[0] if row else None

    def test_run_writes_note_into_patents_main_table(self):
        """匯入 → 產備註 → UPDATE patents → 讀回 patents.文獻備註 有值。"""
        note = "一種阻力調節機構，以磁控阻力盤調節運動負荷。"
        store = ai_patent_note_runner.PatentNoteStore(connect_kwargs=_kw(TEST_DB))
        result = ai_patent_note_runner.run_patent_note(
            workspace_id=None,  # 全庫
            cli_runner=_FakeCli(note),
            store=store,
            char_budget=12_000,
            skip_existing=True,
        )
        # 兩筆有獨立項者都寫入；無獨立項者不成批。
        self.assertEqual(result["notes_written"], 2)
        self.assertEqual(self._note_of(PID_A), note)
        self.assertEqual(self._note_of(PID_B), note)
        # 無獨立項者主表備註仍為空（來源無值就空著）。
        self.assertIsNone(self._note_of(PID_NOCLAIM))

    def test_skip_existing_reads_patents_note_not_attributes(self):
        """第二次跑 skip_existing=True：主表已有備註者不再進候選（讀主表判斷，不 JOIN attributes）。"""
        store = ai_patent_note_runner.PatentNoteStore(connect_kwargs=_kw(TEST_DB))
        # 前一測試已把 A/B 寫入備註；此處候選應為空（兩筆都已有備註）。
        candidates = store.fetch(workspace_id=None, skip_existing=True)
        pids = {pid for pid, _ in candidates}
        self.assertNotIn(PID_A, pids)
        self.assertNotIn(PID_B, pids)


class ImportEnqueueTests(unittest.TestCase):
    """匯入成功後自動 enqueue ai:patent_note；enqueue 失敗不影響匯入（不真跑 CLI、不產生真 job）。"""

    def test_enqueue_patent_note_creates_ai_job_after_import(self):
        """有新專利時，_enqueue_patent_note 建一筆 ai:patent_note，帶 workspace_id。"""
        summary = {"patent_ids": [PID_A, PID_B], "workspace_id": 777}
        fake_job = mock.MagicMock(job_id=12345)
        with mock.patch("backend.app.db.job_repository.create_job", return_value=fake_job) as create:
            handlers._enqueue_patent_note(summary)
        create.assert_called_once()
        args, kwargs = create.call_args
        self.assertEqual(args[0], "ai:patent_note")
        self.assertEqual(kwargs.get("workspace_id"), 777)
        self.assertEqual(summary["patent_note_job_id"], 12345)

    def test_no_new_patents_does_not_enqueue(self):
        """重複檔／dry-run 無新專利：不 enqueue（沒東西可產生備註）。"""
        summary: dict = {"patent_ids": []}
        with mock.patch("backend.app.db.job_repository.create_job") as create:
            handlers._enqueue_patent_note(summary)
        create.assert_not_called()
        self.assertNotIn("patent_note_job_id", summary)

    def test_enqueue_failure_is_isolated_from_import(self):
        """enqueue 拋錯：不 raise（匯入不受影響），錯誤記進 summary.patent_note_error。"""
        summary = {"patent_ids": [PID_A], "workspace_id": 777}
        with mock.patch(
            "backend.app.db.job_repository.create_job", side_effect=RuntimeError("queue down")
        ):
            # 不得 raise——匯入已成功，文獻備註只是輔助。
            handlers._enqueue_patent_note(summary)
        self.assertIn("patent_note_error", summary)
        self.assertIn("queue down", summary["patent_note_error"])


if __name__ == "__main__":
    unittest.main()
