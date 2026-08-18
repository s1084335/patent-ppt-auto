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
    """無建議**且無跳過**時整段隱藏；有建議時顯示可讀資訊，不輸出 raw JSON。

    🔴 2026-08-18 契約更新：原本是「無建議就一律隱藏」。但
    「零筆建議」與「找到的都沒證據所以全被跳過」是**兩件完全不同的事**——
    後者代表那幾家值得人工去查，整段藏起來等於把這個訊息吃掉。
    """
    src = STATIC_INDEX.read_text(encoding="utf-8")

    assert "company-normalization-review" in src
    assert "if (!companyNormalizationSuggestions.length)" in src
    assert "companyNormalizationSkippedHtml" in src, "跳過的揭露函式不見了"
    assert "被跳過" in src, "跳過的情形沒有寫給使用者看"
    assert "function companyNormalizationSuggestionHtml" in src
    assert "function companyNormalizationEvidenceHtml" in src
    assert "原始變體" in src
    assert "目標公司" in src
    assert "名稱依據" in src
    # 🔴 2026-08-18：四種建議的標籤要講使用者要知道的事，不得洩漏內部機制。
    # ⚠「建立臨時公司」會讓人以為確認後還要再做一次什麼——實際上確認即生效，
    #   `TEMP:` 只是代碼層「不冒充 WIPS 代碼」的系統標記。
    assert "建立臨時公司" not in src, "仍在畫面上講內部的臨時代碼機制"
    for label in ("歸入既有公司", "更新公司名稱", "新公司", "自然人歸戶"):
        assert label in src, f"缺少建議種類標籤：{label}"
    assert "WIPS 代碼可日後補" in src, "沒告訴使用者代碼是選配不是待辦"
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
