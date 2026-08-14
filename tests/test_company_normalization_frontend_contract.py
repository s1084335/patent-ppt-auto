from __future__ import annotations

from pathlib import Path


STATIC_INDEX = Path(__file__).resolve().parents[1] / "backend" / "app" / "static" / "index.html"


def test_company_normalization_ai_review_ui_is_single_manual_entry():
    """公司正規化 AI 建議必須是單一手動入口，並掛在既有公司治理區。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "function renderCompanyNormalizationReview" in src
    assert "function runCompanyNormalizationSuggestions" in src
    assert 'onclick="runCompanyNormalizationSuggestions()"' in src
    assert "ai:company_normalization_suggestion" in src
    assert "API + '/company-normalization-suggestions/generate'" in src
    assert "companyNormalizationSuggestionJobRunning" in src
    assert "AI 公司正規化建議產生中" in src
    assert "API + '/company-zh-drafts/generate'" not in src


def test_company_normalization_pending_review_is_hidden_when_empty_and_readable():
    """無建議時整段隱藏；有建議時顯示可讀資訊，不輸出 raw JSON。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "company-normalization-review" in src
    assert "if (!companyNormalizationSuggestions.length) return ''" in src
    assert "function companyNormalizationSuggestionHtml" in src
    assert "function companyNormalizationEvidenceHtml" in src
    assert "原始變體" in src
    assert "目標公司" in src
    assert "名稱依據" in src
    assert "注意事項" in src
    assert ">來源</a>" in src
    assert "JSON.stringify(suggestion.metadata)" not in src


def test_company_normalization_review_supports_multiselect_edit_confirm_skip():
    """審核區要能多選、改中英文名、確認與略過。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert 'class="company-normalization-checkbox"' in src
    assert "selectedCompanyNormalizationSuggestions" in src
    assert "confirmSelectedCompanyNormalizationSuggestions" in src
    assert "skipCompanyNormalizationSuggestion" in src
    assert "company-normalization-zh-" in src
    assert "company-normalization-en-" in src
    assert "company-normalization-target-" in src
    assert "companyNormalizationTargetOptionsHtml" in src
    assert "target_code:" in src
    assert "action: 'confirm'" in src
    assert "action: 'skip'" in src
    assert "未選取任何建議" in src


def test_company_normalization_sse_mapping_refreshes_company_aliases():
    """AI job 成功後刷新公司治理區；refresh_derived 仍刷新專利列表。"""
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "'ai:company_normalization_suggestion': ['companyAliases']" in src
    assert "'companyAliases': { navs: ['browse'], run: renderCompanyCodeRegistry }" in src
    assert "'refresh_derived': ['browsePatents']" in src
    assert "'ai:company_normalization_suggestion': 'AI 公司正規化建議'" in src
