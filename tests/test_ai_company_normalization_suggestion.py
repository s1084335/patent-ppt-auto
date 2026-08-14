"""公司正規化 AI 建議：review-only、禁止 AI 產 WIPS code、人工確認契約。"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from backend.app.db import job_repository
from backend.app.worker import ai_bridge


class FakeStore:
    """記錄 runner 的受控輸入與待審寫入，不碰真 DB。"""

    def __init__(self, candidates, targets):
        self.candidates = list(candidates)
        self.targets = list(targets)
        self.written = []

    def fetch_candidates(self, *, limit=None):
        rows = self.candidates
        return rows if limit is None else rows[:limit]

    def fetch_targets(self):
        return self.targets

    def ingest_suggestions(self, suggestions):
        self.written.extend(suggestions)
        return {"inserted": len(suggestions)}


def _candidate(ref="cand:1", raw_name="Techtronic Outdoor Products Technology Limited"):
    return {
        "candidate_ref": ref,
        "raw_name": raw_name,
        "candidate_type": "company",
        "current_code": None,
        "current_zh_name": None,
        "current_normalized_name": None,
    }


def _target(ref="target:1", code="UN164421", zh_name="創科", normalized_name="Techtronic Industries"):
    return {
        "target_ref": ref,
        "code": code,
        "zh_name": zh_name,
        "normalized_name": normalized_name,
    }


def _valid_result(kind="map_existing"):
    item = {
        "suggestion_kind": kind,
        "candidate_refs": ["cand:1"],
        "target_ref": "target:1",
        "suggested_zh_name": "創科",
        "suggested_normalized_name": "Techtronic Industries",
        "confidence": "high",
        "reason": "公開資料顯示該名稱屬於同一公司身分。",
        "evidence": [
            {
                "url": "https://example.com/profile",
                "title": "Company profile",
                "claim": "Techtronic Industries operates the referenced brand.",
            }
        ],
        "warnings": [],
    }
    if kind == "update_names":
        item["zh_name_basis"] = "market_common_name"
    return json.dumps({"suggestions": [item]}, ensure_ascii=False)


def test_job_is_registered_only_with_ai_bridge():
    """新任務只能由 AI bridge 領，一般 worker 不得 claim。"""
    from backend.app.worker import runner

    job_type = "ai:company_normalization_suggestion"
    assert job_type in job_repository.AI_JOB_TYPES
    assert ai_bridge._AI_JOB_RUNNERS[job_type] == "_run_ai_company_normalization_suggestion_job"
    assert job_type not in runner.DEFAULT_WORKER_JOB_TYPES


def test_cli_command_allows_only_web_search_and_fetch():
    """公司正規化查證可連網，但不可取得 shell、檔案、DB、MCP。"""
    from backend.app.worker import ai_company_normalization_suggestion_runner as runner

    argv = runner.build_company_normalization_cli_command("claude", "prompt")
    allowed = argv[argv.index("--allowedTools") + 1 :]
    assert allowed == ["WebSearch", "WebFetch"]
    for forbidden in ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "mcp__"):
        assert all(forbidden not in item for item in allowed)


def test_runner_uses_opaque_refs_and_writes_review_only_rows():
    """CLI 只看 opaque refs；WIPS code 由 backend target 白名單私下解析後寫待審列。"""
    from backend.app.worker import ai_company_normalization_suggestion_runner as runner

    store = FakeStore([_candidate()], [_target()])
    prompts = []

    result = runner.run_company_normalization_suggestions(
        store=store,
        cli_runner=lambda prompt, **_: prompts.append(prompt) or _valid_result(),
    )

    assert result == {"candidate_count": 1, "suggestion_count": 1, "inserted": 1}
    assert "UN164421" not in prompts[0]
    assert "target:1" in prompts[0]
    assert store.written[0]["review_status"] == "ai_suggested"
    assert store.written[0]["source_type"] == "ai_suggested"
    assert store.written[0]["company_code"] == "UN164421"
    assert store.written[0]["metadata"]["suggestion_kind"] == "map_existing"


@pytest.mark.parametrize("bad_field", ["code", "wips_code", "company_code", "code_override"])
def test_runner_rejects_any_ai_supplied_code_field(bad_field):
    """AI 回傳任何 code 欄位都必須整筆拒絕，不能拿文字推測或覆寫代碼。"""
    from backend.app.worker import ai_company_normalization_suggestion_runner as runner

    payload = json.loads(_valid_result())
    payload["suggestions"][0][bad_field] = "UN999999"
    store = FakeStore([_candidate()], [_target()])

    with pytest.raises(ValueError, match="code field"):
        runner.run_company_normalization_suggestions(
            store=store,
            cli_runner=lambda *_args, **_kwargs: json.dumps(payload, ensure_ascii=False),
        )
    assert store.written == []


def test_runner_rejects_unknown_candidate_or_target_ref():
    """候選與目標都只能來自 backend 受控白名單。"""
    from backend.app.worker import ai_company_normalization_suggestion_runner as runner

    unknown_candidate = json.loads(_valid_result())
    unknown_candidate["suggestions"][0]["candidate_refs"] = ["cand:missing"]
    with pytest.raises(ValueError, match="unknown candidate_ref"):
        runner.run_company_normalization_suggestions(
            store=FakeStore([_candidate()], [_target()]),
            cli_runner=lambda *_args, **_kwargs: json.dumps(unknown_candidate, ensure_ascii=False),
        )

    unknown_target = json.loads(_valid_result())
    unknown_target["suggestions"][0]["target_ref"] = "target:missing"
    with pytest.raises(ValueError, match="unknown target_ref"):
        runner.run_company_normalization_suggestions(
            store=FakeStore([_candidate()], [_target()]),
            cli_runner=lambda *_args, **_kwargs: json.dumps(unknown_target, ensure_ascii=False),
        )


@pytest.mark.parametrize("role", ["owner", "proprietor", "director"])
def test_person_affiliation_accepts_only_owner_proprietor_director(role):
    """自然人分析歸戶只接受 owner/proprietor/director 且需證據。"""
    from backend.app.worker import ai_company_normalization_suggestion_runner as runner

    payload = json.loads(_valid_result("person_affiliation"))
    payload["suggestions"][0].update(
        {
            "relationship_role": role,
            "person_identity_evidence": [
                {"url": "https://example.com/person", "title": "Registry", "claim": "same person"}
            ],
            "relationship_evidence": [
                {"url": "https://example.com/director", "title": "Registry", "claim": role}
            ],
        }
    )
    store = FakeStore([_candidate(raw_name="Jane Chen")], [_target()])

    runner.run_company_normalization_suggestions(
        store=store,
        cli_runner=lambda *_args, **_kwargs: json.dumps(payload, ensure_ascii=False),
    )

    assert store.written[0]["metadata"]["relationship_role"] == role


@pytest.mark.parametrize("role", ["founder", "CEO", "manager", "employee", "inventor", "contact"])
def test_person_affiliation_rejects_insufficient_roles(role):
    """founder、CEO、員工、發明人、聯絡人或同名不能當公司歸戶證據。"""
    from backend.app.worker import ai_company_normalization_suggestion_runner as runner

    payload = json.loads(_valid_result("person_affiliation"))
    payload["suggestions"][0].update(
        {
            "relationship_role": role,
            "person_identity_evidence": [
                {"url": "https://example.com/person", "title": "Person", "claim": "same person"}
            ],
            "relationship_evidence": [
                {"url": "https://example.com/role", "title": "Role", "claim": role}
            ],
        }
    )

    with pytest.raises(ValueError, match="unsupported relationship_role"):
        runner.run_company_normalization_suggestions(
            store=FakeStore([_candidate(raw_name="Jane Chen")], [_target()]),
            cli_runner=lambda *_args, **_kwargs: json.dumps(payload, ensure_ascii=False),
        )


def test_review_confirmation_delegates_confirmed_writer_and_enqueues_one_refresh():
    """人工確認多筆建議只委派唯一 confirmed writer，且只排一個 refresh_derived。"""
    from backend.app.api.company_aliases import CompanyNormalizationReviewDecision
    from backend.app.derived import company_alias_importer as importer

    drafts = [
        {
            "id": 10,
            "candidate_ref": "cand:1",
            "company_code": "UN164421",
            "raw_name": "Techtronic Outdoor Products Technology Limited",
            "suggested_zh_name": "創科",
            "suggested_normalized_name": "Techtronic Industries",
            "metadata": {"suggestion_id": "s1", "suggestion_kind": "map_existing"},
        },
        {
            "id": 11,
            "candidate_ref": "cand:2",
            "company_code": "UN164421",
            "raw_name": "TTI",
            "suggested_zh_name": "創科",
            "suggested_normalized_name": "Techtronic Industries",
            "metadata": {"suggestion_id": "s1", "suggestion_kind": "map_existing"},
        },
    ]
    decisions = [
        CompanyNormalizationReviewDecision(suggestion_id=10, action="confirm"),
        CompanyNormalizationReviewDecision(
            suggestion_id=11,
            action="confirm",
            zh_name="創科集團",
            normalized_name="Techtronic Industries",
        ),
    ]

    with mock.patch.object(importer, "list_company_normalization_suggestions", return_value={"items": drafts}), \
            mock.patch.object(importer, "list_company_normalization_targets", return_value=[]), \
            mock.patch.object(importer, "apply_confirmed_display_names", return_value={"inserted": 3}) as writer, \
            mock.patch.object(importer, "clear_company_normalization_suggestions", return_value=2), \
            mock.patch("backend.app.api.company_aliases.create_job", return_value=77) as create_job:
        result = importer.confirm_company_normalization_suggestions(decisions)

    assert result["confirmed"] == 2
    writer.assert_called_once()
    mapping = writer.call_args.args[0]
    assert mapping["UN164421"]["zh_name"] == "創科集團"
    assert set(mapping["UN164421"]["aliases"]) == {
        "Techtronic Outdoor Products Technology Limited",
        "TTI",
    }
    create_job.assert_not_called()


def test_review_confirmation_can_override_target_with_backend_company_option():
    """使用者改選目標公司時，正式 mapping 必須使用該公司既有 code/name。"""
    from backend.app.api.company_aliases import CompanyNormalizationReviewDecision
    from backend.app.derived import company_alias_importer as importer

    drafts = [
        {
            "id": 10,
            "company_code": "UN164421",
            "raw_name": "Ryobi",
            "suggested_zh_name": "創科",
            "suggested_normalized_name": "Techtronic Industries",
            "metadata": {"suggestion_id": "s1", "suggestion_kind": "map_existing"},
        }
    ]
    targets = [
        {
            "target_ref": "target:x",
            "code": "UN109300",
            "zh_name": "美沃奇",
            "normalized_name": "Milwaukee Tool",
        }
    ]
    decision = CompanyNormalizationReviewDecision(
        suggestion_id=10,
        action="confirm",
        target_code="UN109300",
    )

    with mock.patch.object(importer, "list_company_normalization_suggestions", return_value={"items": drafts}), \
            mock.patch.object(importer, "list_company_normalization_targets", return_value=targets), \
            mock.patch.object(importer, "apply_confirmed_display_names", return_value={"inserted": 1}) as writer, \
            mock.patch.object(importer, "clear_company_normalization_suggestions", return_value=1):
        result = importer.confirm_company_normalization_suggestions([decision])

    assert result["confirmed"] == 1
    mapping = writer.call_args.args[0]
    assert "UN109300" in mapping
    assert mapping["UN109300"]["zh_name"] == "美沃奇"
    assert mapping["UN109300"]["normalized_name"] == "Milwaukee Tool"
