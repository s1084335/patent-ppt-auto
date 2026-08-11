"""SSE LISTEN 連線必須走 session 模式（complete-sse-data-refresh 實測根因修復）。

## 根因（2026-08-11 實測，listen_probe）

`.env` 的 DATABASE_URL 指 Supabase **pooler :6543（transaction pooling）**。
pgbouncer transaction 模式下 LISTEN/NOTIFY **靜默失效**——listener 掛在 6543 上
8 秒收不到任何 NOTIFY；同一個 NOTIFY 改由 :5432（session 模式）的 listener 立即收到
（發送端不拘，trigger 在 DB 端執行）。

也就是說：SSE 在本環境**從來沒真的通過**，任務卡更新一直是靠 30 秒輪詢與
頁面級輪詢撐著。「job 完成畫面不動」的病灶在此，不只是前端沒接刷新。

## 修法

只改 **LISTEN 那一條連線**：`get_listen_connection_kwargs()` 將 :6543 換 :5432
（可用 SSE_LISTEN_DATABASE_URL 顯式覆寫）。一般查詢連線維持 6543 transaction
pooling 不動——session 連線佔 slot，只有常駐 LISTEN 這一條值得用。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from backend.app.db import connection

EVENTS_PY = (Path(__file__).resolve().parents[1] / "backend" / "app"
             / "api" / "events.py")


class ListenKwargsTests(unittest.TestCase):
    def test_pooler_6543_swapped_to_session_5432(self):
        with mock.patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://u:p@host.pooler.supabase.com:6543/postgres?sslmode=require",
        }, clear=False):
            os.environ.pop("SSE_LISTEN_DATABASE_URL", None)
            kwargs = connection.get_listen_connection_kwargs()
        self.assertIn(":5432/", kwargs["conninfo"])
        self.assertNotIn(":6543/", kwargs["conninfo"])

    def test_explicit_override_wins(self):
        with mock.patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://u:p@host:6543/postgres",
            "SSE_LISTEN_DATABASE_URL": "postgresql://u:p@direct-host:5432/postgres",
        }, clear=False):
            kwargs = connection.get_listen_connection_kwargs()
        self.assertIn("direct-host:5432", kwargs["conninfo"])

    def test_non_pooler_url_unchanged(self):
        """已是 5432／直連者不得改寫——只針對 6543 transaction pooling 收斂。"""
        with mock.patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://u:p@db.example.com:5432/postgres",
        }, clear=False):
            os.environ.pop("SSE_LISTEN_DATABASE_URL", None)
            kwargs = connection.get_listen_connection_kwargs()
        self.assertIn("db.example.com:5432", kwargs["conninfo"])

    def test_local_pg_env_path_still_works(self):
        """無 DATABASE_URL（本機 PG* 直連）沿用一般 kwargs——直連本就 session。"""
        env = {k: v for k, v in os.environ.items()
               if k not in ("DATABASE_URL", "SSE_LISTEN_DATABASE_URL")}
        with mock.patch.dict(os.environ, env, clear=True):
            kwargs = connection.get_listen_connection_kwargs()
        self.assertNotIn("conninfo", kwargs)
        self.assertIn("host", kwargs)


class EventsUsesListenKwargsTests(unittest.TestCase):
    def test_listen_worker_uses_listen_kwargs(self):
        """events.py 的 LISTEN 執行緒必須用 listen 專用 kwargs，不得用一般連線。"""
        src = EVENTS_PY.read_text(encoding="utf-8")
        self.assertIn("get_listen_connection_kwargs", src,
                      "SSE LISTEN 未走 session 模式連線——6543 上收不到 NOTIFY")

    def test_notifies_generator_is_relooped(self):
        """🔴 第二根因（2026-08-11 實測）：psycopg 的 `notifies(timeout=0.5)` 是
        「generator **總壽命** 0.5 秒」不是「每 0.5 秒輪詢一次」——原寫法讓
        LISTEN 執行緒開場 0.5 秒就自然耗盡、關連線退出，之後所有 NOTIFY 全丟，
        端點永遠只送心跳。必須以 `while not stop.is_set()` 外圈重進 generator。"""
        src = EVENTS_PY.read_text(encoding="utf-8")
        self.assertRegex(
            src, r"while not stop\.is_set\(\):\s*\n\s*for notify in conn\.notifies\(",
            "notifies(timeout=…) 未包在 while 外圈——執行緒 0.5 秒即死")


if __name__ == "__main__":
    unittest.main()
