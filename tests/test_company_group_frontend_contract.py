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
    assert 'id="company-group-company-picker"' in src
    assert "companyGroupCompanyOptionsHtml" in src
    assert "company-group-member-select-" in src
    assert "selectedCompanyGroupCompany" in src
    assert 'id="company-group-member-name"' not in src
    assert 'id="company-group-member-code"' not in src
    assert "prompt('要加入的公司顯示名')" not in src
    assert "prompt('公司代碼（可空）')" not in src


def test_new_company_group_supports_multiple_company_selection():
    """建立集團時可勾選多家公司，並一次送入 members 陣列。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert 'id="company-group-company-picker"' in src
    assert 'class="company-group-company-checkbox"' in src
    assert "selectedCompanyGroupCompanies" in src
    assert "const members = selectedCompanyGroupCompanies" in src
    assert "members: members" in src
    assert "members.length" in src


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


def test_pending_company_group_name_is_editable_and_sent_only_on_confirm():
    """待審集團名稱可先修短；確認才送名稱，拒絕不得改名。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert 'id="company-group-suggestion-name-' in src
    assert 'maxlength="255"' in src
    assert "確認後集團名稱" in src
    assert "function pendingCompanyGroupName" in src
    assert "group_name: groupName" in src
    assert "if (confirmed)" in src
    assert "請輸入集團名稱" in src


def test_existing_group_target_is_read_only_and_confirm_does_not_rename_it():
    """加入既有集團只確認 member，不得順手改 parent group 名稱。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "加入既有集團" in src
    assert "const canEditName = group.review_status !== 'confirmed'" in src
    assert "editGroupName" in src
    assert "if (editGroupName)" in src


def test_cli_suggestion_evidence_is_structured_for_human_review():
    """待審建議需顯示可讀證據，不得把 evidence_json 整包輸出。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "function companyGroupEvidenceHtml" in src
    assert "function companyGroupConfidenceLabel" in src
    assert "function companyGroupEvidenceUrl" in src
    assert "可信度" in src
    assert "證據來源" in src
    assert "注意事項" in src
    assert '>來源</a>' in src
    assert 'target="_blank" rel="noopener noreferrer"' in src
    assert "JSON.stringify(member.evidence_json)" not in src


def test_established_group_has_complete_reversal_actions():
    """已建立集團需同時保留單筆移除、AI 確認撤銷與整組解散。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "function undoCompanyGroupSuggestion" in src
    assert "function deleteCompanyGroupLayer" in src
    assert "/undo-confirm" in src
    assert "撤銷確認" in src
    assert "解散集團" in src
    assert "移除" in src
    assert "removeCompanyGroupLayerMember" in src


def test_company_group_ai_suggestions_have_manual_trigger_and_sse_refresh():
    """集團 AI 建議只能由使用者手動啟動，完成後沿用 SSE 刷新清單。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "function runCompanyGroupSuggestions()" in src
    assert 'onclick="runCompanyGroupSuggestions()"' in src
    assert "產生 AI 建議" in src
    assert "ai:company_group_suggestion" in src
    assert "API + '/ai-tasks'" in src
    assert "companyGroupSuggestionJobRunning" in src
    assert "AI 建議產生中" in src
    assert "'ai:company_group_suggestion': ['companyGroups']" in src
    assert 'id="company-group-ai-suggest"' in src
    assert "companyGroupSuggestionStarting = true" in src
    assert "suggestButton.disabled = true" in src
    assert "function taskDisplayName" in src
    assert "'ai:company_group_suggestion': 'AI 集團建議'" in src
    assert "taskDisplayName(t.job_type)" in src


def test_report_generation_ui_exposes_group_scope():
    """報表產生前端必須可明確選 company/group，並把 report_scope 送到後端。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "report-scope-select" in src
    assert '<option value="company">公司</option>' in src
    assert '<option value="group">集團</option>' in src
    assert "report_scope: reportScope" in src
