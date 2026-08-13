from __future__ import annotations

from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy_containers.sh"


def test_deploy_script_sanitizes_database_url_from_crlf_env() -> None:
    """部署腳本從 Windows CRLF .env 讀 DATABASE_URL 時必須移除 CR 與外層引號。"""
    src = SCRIPT.read_text(encoding="utf-8")

    assert "normalize_db_url" in src
    assert "tr -d '\\r'" in src
    assert 'sed -E "s/^([\\\"' in src
    assert 'DB_URL="$(normalize_db_url "$DB_URL")"' in src
