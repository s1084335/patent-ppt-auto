from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from backend.app.main import app


def test_company_group_api_routes_are_registered():
    """所有治理入口都必須掛在 /api/v1/company-groups，供前端與 CLI 使用。"""
    client = TestClient(app)
    paths = set(client.app.openapi()["paths"])
    assert "/api/v1/company-groups" in paths
    assert "/api/v1/company-groups/{group_id}" in paths
    assert "/api/v1/company-groups/{group_id}/members" in paths
    assert "/api/v1/company-groups/suggestions" in paths
    assert "/api/v1/company-groups/suggestions/{member_id}/confirm" in paths
    assert "/api/v1/company-groups/suggestions/{member_id}/reject" in paths


def test_cli_suggestion_rejects_direct_confirmed_write():
    """CLI/AI 只能寫 suggested，不可繞過人工確認直接建立 confirmed mapping。"""
    from backend.app.repositories.company_group_repository import validate_cli_suggestion

    with pytest.raises(ValueError, match="confirmed"):
        validate_cli_suggestion(
            {
                "group_name": "Creative Group",
                "review_status": "confirmed",
                "members": [
                    {
                        "company_display_name": "創科",
                        "review_status": "confirmed",
                        "evidence_json": {"basis": ["user_target"]},
                    }
                ],
            }
        )


def test_suggestion_without_basis_is_insufficient_evidence():
    """無 seed、使用者目標或高信心內部名稱規則時，不可產生 confident candidate。"""
    from backend.app.repositories.company_group_repository import evaluate_suggestion_basis

    decision = evaluate_suggestion_basis(
        has_confirmed_seed=False,
        user_target=None,
        strong_internal_pattern=False,
    )
    assert decision["decision"] == "insufficient_evidence"
    assert "insufficient_evidence" in decision["warnings"]
    assert decision["can_create_confident_candidate"] is False


def test_valid_cli_suggestion_is_review_only():
    """有效 CLI 建議也只能轉成 suggested/cli_ai 的 review-only payload。"""
    from backend.app.repositories.company_group_repository import validate_cli_suggestion

    suggestion = validate_cli_suggestion(
        {
            "group_name": "創科集團",
            "members": [
                {
                    "company_code": "WIPS-A",
                    "company_display_name": "創科",
                    "evidence_json": {"basis": ["user_target"]},
                }
            ],
        }
    )
    assert suggestion["review_status"] == "suggested"
    assert suggestion["source_type"] == "cli_ai"
    assert suggestion["members"][0]["review_status"] == "suggested"
    assert suggestion["members"][0]["source_type"] == "cli_ai"


def test_cli_suggestion_rejects_malformed_evidence():
    """CLI/AI 建議必須提供 object 型態 evidence，避免 jsonb 寫入時才失敗。"""
    from backend.app.repositories.company_group_repository import validate_cli_suggestion

    with pytest.raises(ValueError, match="evidence_json must be an object"):
        validate_cli_suggestion(
            {
                "group_name": "Creative Group",
                "members": [
                    {
                        "company_display_name": "創科",
                        "evidence_json": ["not", "an", "object"],
                    }
                ],
            }
        )


def test_report_api_rejects_invalid_scope_before_job_creation():
    """report_scope 只能是 company/group，非法值應由 API schema 直接擋下。"""
    client = TestClient(app)

    response = client.post(
        "/api/v1/reports",
        json={"patent_ids": [1], "report_scope": "workspace"},
    )

    assert response.status_code == 422
