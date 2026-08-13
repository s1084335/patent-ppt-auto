"""集團 AI 建議任務的最小權限、資料邊界與 suggested-only 契約。"""
from __future__ import annotations

import json

import pytest

from backend.app.db import job_repository
from backend.app.worker import ai_bridge


class FakeStore:
    """記錄 runner 的受控讀寫，避免單元測試碰資料庫。"""

    def __init__(self, candidates):
        self.candidates = list(candidates)
        self.written = []

    def fetch_candidates(self, *, limit=None):
        rows = self.candidates
        return rows if limit is None else rows[:limit]

    def ingest_suggestions(self, suggestions):
        self.written.extend(suggestions)
        return {"inserted": len(suggestions)}


def _valid_cli_result() -> str:
    return json.dumps(
        {
            "suggestions": [
                {
                    "group_name": "創科集團",
                    "members": [
                        {
                            "company_code": "C001",
                            "company_display_name": "創科實業",
                            "evidence_json": {
                                "confidence": "high",
                                "sources": [
                                    {
                                        "url": "https://example.com/company",
                                        "title": "Company profile",
                                        "claim": "The company belongs to the group.",
                                    }
                                ],
                            },
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )


def test_job_is_registered_only_with_ai_bridge():
    """新任務必須走集中式 AI bridge，不進一般 worker。"""
    from backend.app.worker import runner

    job_type = "ai:company_group_suggestion"
    assert job_type in job_repository.AI_JOB_TYPES
    assert ai_bridge._AI_JOB_RUNNERS[job_type] == "_run_ai_company_group_suggestion_job"
    assert job_type not in runner.DEFAULT_WORKER_JOB_TYPES


def test_cli_command_allows_only_web_search_and_fetch():
    """CLI 可連網取證，但不得取得 shell、檔案、MCP 或資料庫能力。"""
    from backend.app.worker import ai_company_group_suggestion_runner as runner

    argv = runner.build_company_group_cli_command("claude", "prompt")
    allowed = argv[argv.index("--allowedTools") + 1 :]
    assert allowed == ["WebSearch", "WebFetch"]
    for forbidden in ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "mcp__"):
        assert all(forbidden not in item for item in allowed)


def test_cli_kind_without_tool_whitelist_is_rejected():
    """無法強制工具白名單的 CLI 不得執行這個連網任務。"""
    from backend.app.worker import ai_company_group_suggestion_runner as runner

    with pytest.raises(ValueError, match="requires claude"):
        runner.build_company_group_cli_command("opencode", "prompt")


@pytest.mark.parametrize(
    "render",
    [
        lambda raw: raw,
        lambda raw: f"依契約輸出：\n```json\n{raw}\n```",
        lambda raw: f"以下為契約指定的 JSON 物件：\n```json\n{raw}\n```",
        lambda raw: f"```json\n{raw}\n```",
        lambda raw: f"{raw}\n以上為建議。",
    ],
)
def test_extract_suggestions_accepts_real_cli_wrappers(render):
    """昂貴的連網查證不得因 CLI 加開場白、圍欄或尾句整趟作廢。"""
    from backend.app.worker import ai_company_group_suggestion_runner as runner

    suggestions = runner._extract_suggestions(render(_valid_cli_result()))
    assert suggestions[0]["group_name"] == "創科集團"


def test_runner_uses_controlled_candidates_and_writes_review_only_suggestions():
    """CLI 只能針對 backend 給定公司提案，且寫入既有 suggested workflow。"""
    from backend.app.worker import ai_company_group_suggestion_runner as runner

    store = FakeStore([{"company_code": "C001", "company_display_name": "創科實業"}])
    prompts = []

    def cli(prompt, *, timeout_seconds):
        prompts.append(prompt)
        return _valid_cli_result()

    result = runner.run_company_group_suggestions(store=store, cli_runner=cli)

    assert result == {"candidate_count": 1, "suggestion_count": 1, "inserted": 1}
    assert "C001" in prompts[0]
    assert "創科實業" in prompts[0]
    assert store.written[0]["review_status"] == "suggested"
    assert store.written[0]["source_type"] == "cli_ai"
    assert store.written[0]["members"][0]["review_status"] == "suggested"


def test_runner_rejects_unknown_company_or_missing_web_source():
    """模型不得憑空新增公司，也不得產生沒有 URL 證據的連網建議。"""
    from backend.app.worker import ai_company_group_suggestion_runner as runner

    store = FakeStore([{"company_code": "C001", "company_display_name": "創科實業"}])
    unknown = json.loads(_valid_cli_result())
    unknown["suggestions"][0]["members"][0]["company_code"] = "UNKNOWN"
    with pytest.raises(ValueError, match="unknown company"):
        runner.run_company_group_suggestions(
            store=store,
            cli_runner=lambda *_args, **_kwargs: json.dumps(unknown, ensure_ascii=False),
        )

    no_source = json.loads(_valid_cli_result())
    no_source["suggestions"][0]["members"][0]["evidence_json"]["sources"] = []
    with pytest.raises(ValueError, match="https source"):
        runner.run_company_group_suggestions(
            store=store,
            cli_runner=lambda *_args, **_kwargs: json.dumps(no_source, ensure_ascii=False),
        )


def test_no_candidates_skips_cli_and_database_write():
    """沒有未分組公司時直接成功結束，不浪費 CLI 呼叫。"""
    from backend.app.worker import ai_company_group_suggestion_runner as runner

    store = FakeStore([])
    called = False

    def cli(*_args, **_kwargs):
        nonlocal called
        called = True
        return _valid_cli_result()

    result = runner.run_company_group_suggestions(store=store, cli_runner=cli)
    assert result == {"candidate_count": 0, "suggestion_count": 0, "inserted": 0}
    assert called is False
    assert store.written == []
