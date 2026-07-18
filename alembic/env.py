"""Alembic environment.

The connection is built from the same DATABASE_URL / PG* environment variables
as backend.app.db.connection, so local runs, the dev DB and the container
migrate step share one config. The URL is assembled with sqlalchemy.URL.create
so special characters in PGPASSWORD are escaped correctly, and it forces the
psycopg (v3) driver. Migrations are hand-authored (no ORM metadata), so
autogenerate is not used. No search_path is applied here, so alembic_version is
created in the default (public) schema.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import URL, create_engine, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None
DEFAULT_PGPORT = "5433"
"""本機 migration 預設連到 Docker PostgreSQL 對外 port。"""


def _force_psycopg3(url: str) -> str:
    """把一般 PostgreSQL URL 轉成 SQLAlchemy psycopg v3 driver。"""
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    return url


def build_url() -> URL | str:
    """依 DATABASE_URL 或 PG* 環境變數建立 Alembic 連線 URL。"""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return _force_psycopg3(database_url)
    return URL.create(
        "postgresql+psycopg",
        username=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD"),
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", DEFAULT_PGPORT)),
        database=os.getenv("PGDATABASE", "patent_ppt"),
    )


def run_migrations_offline() -> None:
    """產生離線 migration SQL，不直接連線資料庫。"""
    url = build_url()
    context.configure(
        url=url.render_as_string(hide_password=False) if isinstance(url, URL) else url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """直接連線資料庫並套用 Alembic migration。"""
    engine = create_engine(build_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
