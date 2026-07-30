"""連線池必須停用自動 prepared statement（2026-07-30 實機 job #136 failed）。

## 問題

    ai_report_ppt_runner.py:495  uploaded = uploader(run_dir)
    report_artifact_store.py:41  cur.execute(...)
    psycopg.errors.DuplicatePreparedStatement: prepared statement "_pg3_0" already exists

Supabase pooler（6543 transaction mode）會把同一條後端連線重用給不同 client。
psycopg 預設 `prepare_threshold=5`——同一句 SQL 執行第 6 次就自動改用 prepared
statement 並取名 `_pg3_N`。後端連線上若已有同名 statement（別的 client 留下的），
就撞名。

`upload_run_dir` 逐檔 INSERT，一個報表版本 20+ 檔，必然跨過門檻。

## 根因

`get_connection_kwargs()` **已經**回傳 `prepare_threshold: None`（連註解都寫明是為了
防這個錯），但 `get_pool()` 是這樣用它的：

    ConnectionPool(conninfo=make_conninfo(**get_connection_kwargs()), ...)

⚠ `prepare_threshold` **不是 libpq 連線參數**，是 psycopg 的 client 端設定。
`make_conninfo` 不認得它，實測結果字串裡**根本沒有這個 key**——防護在池這條路徑上
完全失效，而直連路徑（`psycopg.connect(**kwargs)`）是好的。

同一個防護，兩條路徑只有一條生效；池那條靜默失效，直到跨過 5 次門檻才炸。

## 定案

池要用 `kwargs=` 把 client 端設定傳給每條連線，不能全塞進 conninfo。
"""
from __future__ import annotations

import unittest

from psycopg.conninfo import make_conninfo

from backend.app.db.connection import get_connection_kwargs


class ConnectionKwargsTests(unittest.TestCase):
    """直連路徑的防護（既有行為，不得回歸）。"""

    def test_prepare_threshold_disabled(self):
        self.assertIsNone(
            get_connection_kwargs().get("prepare_threshold", "MISSING"),
            "prepare_threshold 必須為 None（停用自動 prepare）")


class MakeConninfoDropsClientSettingsTests(unittest.TestCase):
    """🔴 釘住「make_conninfo 會丟掉 prepare_threshold」這個事實。

    這不是我們能修的行為（libpq 就是不認），所以測試不是要它改變，而是**釘住**它——
    日後有人想把其他 client 端設定塞進 conninfo 時，這支測試說明為什麼不行。
    """

    def test_prepare_threshold_not_in_conninfo(self):
        kwargs = dict(get_connection_kwargs())
        kwargs.pop("password", None)  # 不讓密碼進斷言訊息
        conninfo = make_conninfo(**kwargs)
        self.assertNotIn(
            "prepare_threshold", conninfo,
            "make_conninfo 竟保留了 prepare_threshold——若 psycopg 改了行為，"
            "get_pool 的 kwargs 傳法可以簡化")


class PoolPrepareThresholdTests(unittest.TestCase):
    """🔴 池借出的連線也必須停用自動 prepare。"""

    def test_pool_receives_client_kwargs(self):
        """池建構時要把 prepare_threshold 經 `kwargs=` 傳給每條連線。

        ⚠ 不實際開池：那會連 DB（測試不得依賴外部連線）。改為攔截
        ConnectionPool 的建構參數，驗傳法正確。
        """
        import backend.app.db.connection as conn_mod

        captured: dict = {}

        class FakePool:
            def __init__(self, **kw):
                captured.update(kw)

        import sys
        import types

        fake_mod = types.ModuleType("psycopg_pool")
        fake_mod.ConnectionPool = FakePool
        real_mod = sys.modules.get("psycopg_pool")
        real_pool = conn_mod._pool
        sys.modules["psycopg_pool"] = fake_mod
        conn_mod._pool = None
        try:
            conn_mod.get_pool()
        finally:
            conn_mod._pool = real_pool
            if real_mod is not None:
                sys.modules["psycopg_pool"] = real_mod
            else:
                sys.modules.pop("psycopg_pool", None)

        self.assertIn(
            "kwargs", captured,
            "ConnectionPool 未收到 kwargs——client 端設定沒傳下去，"
            "池借出的連線 prepare_threshold 會是預設 5")
        self.assertIsNone(
            captured["kwargs"].get("prepare_threshold", "MISSING"),
            "池連線的 prepare_threshold 必須為 None，否則 upload_run_dir "
            "逐檔 INSERT 跨過門檻就撞 DuplicatePreparedStatement")

    def test_conninfo_still_has_libpq_params(self):
        """⚠ 搬 prepare_threshold 出去時不得把真正的連線參數一起搬走。"""
        import sys
        import types

        import backend.app.db.connection as conn_mod

        captured: dict = {}

        class FakePool:
            def __init__(self, **kw):
                captured.update(kw)

        fake_mod = types.ModuleType("psycopg_pool")
        fake_mod.ConnectionPool = FakePool
        real_mod = sys.modules.get("psycopg_pool")
        real_pool = conn_mod._pool
        sys.modules["psycopg_pool"] = fake_mod
        conn_mod._pool = None
        try:
            conn_mod.get_pool()
        finally:
            conn_mod._pool = real_pool
            if real_mod is not None:
                sys.modules["psycopg_pool"] = real_mod
            else:
                sys.modules.pop("psycopg_pool", None)

        conninfo = captured.get("conninfo", "")
        self.assertTrue(conninfo, "conninfo 不得為空")
        # search_path 由 options 帶入，掉了會查不到 core_layer 的表
        self.assertIn("options", conninfo, "options（search_path）不得遺失")


if __name__ == "__main__":
    unittest.main()
