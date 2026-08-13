from __future__ import annotations

from pathlib import Path


STATIC_INDEX = Path(__file__).resolve().parents[1] / "backend" / "app" / "static" / "index.html"


def test_company_group_governance_ui_is_wired():
    """前端必須有預設收合的集團治理區與必要 API 操作入口。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "company-group-registry" in src
    assert "renderCompanyGroupRegistry()" in src
    assert "function renderCompanyGroupRegistry()" in src
    assert "function createCompanyGroupLayer()" in src
    assert "function decideCompanyGroupSuggestion" in src
    assert "API + '/company-groups'" in src
    assert "/company-groups/suggestions/" in src


def test_report_generation_ui_exposes_group_scope():
    """報表產生前端必須可明確選 company/group，並把 report_scope 送到後端。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "report-scope-select" in src
    assert '<option value="company">公司</option>' in src
    assert '<option value="group">集團</option>' in src
    assert "report_scope: reportScope" in src
