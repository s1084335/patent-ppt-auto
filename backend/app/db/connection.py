from __future__ import annotations

import os
import threading


DEFAULT_PGPORT = "5433"
"""本機開發預設連到 Docker PostgreSQL 對外 port；容器內部署用環境變數覆蓋。"""


def _get_pgport() -> int:
    """讀取 PGPORT；格式錯誤時丟出可讀錯誤，讓 /ready 可清楚回報設定問題。"""
    raw = os.getenv("PGPORT", DEFAULT_PGPORT)
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"PGPORT must be an integer, got {raw!r}") from exc


def get_database_url() -> str:
    """組出 psycopg 可用的連線字串，優先使用 DATABASE_URL。"""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    host = os.getenv("PGHOST", "localhost")
    port = _get_pgport()
    dbname = os.getenv("PGDATABASE", "patent_ppt")
    user = os.getenv("PGUSER", "postgres")
    password = os.getenv("PGPASSWORD")

    if password:
        return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
    return f"postgresql://{user}@{host}:{port}/{dbname}"


# 預設 search_path（2026-07-27 實機修）。
# ⚠ **必須含 extensions**：pgvector 在 Supabase 裝在 extensions schema，
#   少了它 `vector` 型別就找不到 → embeddings 寫入炸 UndefinedObject
#   → 分群回報「no patents with reusable embeddings」（錯誤訊息指向下游，很難聯想）。
# ⚠ 這裡是**預設值**，不是 fallback：psycopg 的 options 會**覆蓋** DB 端的
#   search_path。Supabase 本身的預設（"$user", public, extensions）是對的，
#   反而是我們這行把 extensions 拿掉——「忘了設 PGOPTIONS」因此等於壞掉。
_DEFAULT_PG_OPTIONS = "-c search_path=core_layer,raw_layer,public,extensions"


def get_connection_kwargs() -> dict[str, str | int]:
    """回傳 psycopg.connect 參數；本機預設走 localhost:5433。"""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return {
            "conninfo": database_url,
            "options": os.getenv("PGOPTIONS", _DEFAULT_PG_OPTIONS),
            # Supabase pooler 會重用後端連線；psycopg 自動 prepared statement
            # 可能撞到既有 _pg3_N 名稱，導致 DuplicatePreparedStatement。關閉自動 prepare。
            "prepare_threshold": None,
        }

    kwargs: dict[str, str | int] = {
        "host": os.getenv("PGHOST", "localhost"),
        "port": _get_pgport(),
        "dbname": os.getenv("PGDATABASE", "patent_ppt"),
        "user": os.getenv("PGUSER", "postgres"),
        "options": os.getenv("PGOPTIONS", _DEFAULT_PG_OPTIONS),
        "prepare_threshold": None,
    }
    password = os.getenv("PGPASSWORD")
    if password:
        kwargs["password"] = password
    return kwargs


# 連線池（lazy 單例）：高頻查詢路徑（報表引擎/前端/LLM 工具呼叫）用池借還，
# 避免每個請求都開關連線。單次型 CLI（refresh/import）維持直連即可。
_pool = None
_pool_lock = threading.Lock()


def get_pool():
    """取得全域 psycopg 連線池（首次呼叫時建立）。"""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg.conninfo import make_conninfo
                from psycopg_pool import ConnectionPool

                _pool = ConnectionPool(
                    conninfo=make_conninfo(**get_connection_kwargs()),
                    min_size=1,
                    max_size=5,
                    open=True,
                )
    return _pool
