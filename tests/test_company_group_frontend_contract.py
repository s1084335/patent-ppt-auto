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


def test_company_group_members_are_selected_from_existing_companies():
    """建立集團與新增成員都必須選既有公司，不得要求使用者重打名稱或代碼。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "API + '/company-codes/existing'" in src
    assert 'id="company-group-company-select"' in src
    assert "companyGroupCompanyOptionsHtml" in src
    assert "company-group-member-select-" in src
    assert "selectedCompanyGroupCompany" in src
    assert 'id="company-group-member-name"' not in src
    assert 'id="company-group-member-code"' not in src
    assert "prompt('要加入的公司顯示名')" not in src
    assert "prompt('公司代碼（可空）')" not in src


def test_cli_suggestions_and_established_groups_have_separate_sections():
    """CLI suggestions stay visible for review while established groups stay collapsed."""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert 'id="company-group-suggestions"' in src
    assert "companyGroupSuggestionHtml" in src
    assert "evidence_json" in src
    assert '<details id="company-group-list">' in src
    assert '<details id="company-group-list" open>' not in src
    assert "CLI 建議待審核" in src
    assert "已建立集團" in src


def test_report_generation_ui_exposes_group_scope():
    """報表產生前端必須可明確選 company/group，並把 report_scope 送到後端。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "report-scope-select" in src
    assert '<option value="company">公司</option>' in src
    assert '<option value="group">集團</option>' in src
    assert "report_scope: reportScope" in src
