from __future__ import annotations

import os


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    dbname = os.getenv("PGDATABASE", "patent_ppt")
    user = os.getenv("PGUSER", "postgres")
    password = os.getenv("PGPASSWORD")

    if password:
        return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
    return f"postgresql://{user}@{host}:{port}/{dbname}"

def get_connection_kwargs() -> dict[str, str | int]:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return {
            "conninfo": database_url,
            "options": os.getenv("PGOPTIONS", "-c search_path=core_layer,raw_layer,public"),
        }

    kwargs: dict[str, str | int] = {
        "host": os.getenv("PGHOST", "localhost"),
        "port": int(os.getenv("PGPORT", "5432")),
        "dbname": os.getenv("PGDATABASE", "patent_ppt"),
        "user": os.getenv("PGUSER", "postgres"),
        "options": os.getenv("PGOPTIONS", "-c search_path=core_layer,raw_layer,public"),
    }
    password = os.getenv("PGPASSWORD")
    if password:
        kwargs["password"] = password
    return kwargs
