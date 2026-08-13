"""手動啟動的公司集團連網查證任務，輸出僅能進 suggested 審核流程。"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from backend.app.repositories import company_group_repository as repository
from backend.app.worker.ai_payload_file import extract_json_payload
from backend.app.worker.cli_gateway import (
    WEB_RESEARCH_TOOLS,
    build_cli_command,
    parse_cli_result,
    run_cli,
)

DEFAULT_CLI_TIMEOUT_SECONDS = 600.0


class CompanyGroupSuggestionStore:
    """集中封裝候選讀取與既有 suggestion 寫入邊界。"""

    def fetch_candidates(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """讀取尚未分組的已確認公司。"""
        return repository.list_suggestion_candidates(limit=limit)

    def fetch_existing_groups(self) -> list[dict[str, Any]]:
        """讀取可供 CLI 指向的已確認集團與種子成員。"""
        return repository.list_confirmed_group_candidates()

    def ingest_suggestions(self, suggestions: list[dict[str, Any]]) -> dict[str, Any]:
        """透過既有 repository 寫入 suggested group/member。"""
        return repository.ingest_cli_suggestions(suggestions)


def _require_supported_cli(cli_kind: str) -> None:
    """連網任務只允許能強制工具白名單的 Claude CLI。"""
    if cli_kind != "claude":
        raise ValueError("company group web research requires claude tool whitelist")


def build_company_group_cli_command(
    cli_kind: str, prompt: str, *, model: str | None = None
) -> list[str]:
    """建立只允許 WebSearch 與 WebFetch 的 headless CLI 命令。"""
    _require_supported_cli(cli_kind)
    return build_cli_command(cli_kind, prompt, model=model, tools=WEB_RESEARCH_TOOLS)


def build_prompt(
    candidates: list[dict[str, Any]],
    existing_groups: list[dict[str, Any]],
) -> str:
    """將 backend 控制的候選公司內嵌為嚴格 JSON 查證任務。"""
    payload = json.dumps(
        {"companies": candidates, "confirmed_groups": existing_groups},
        ensure_ascii=False,
        indent=2,
    )
    return f"""你是公司集團歸屬的研究助手。請使用公開網頁查證下列公司是否屬於同一企業集團。

限制：
1. 只能使用輸入中的 company_code、company_display_name 與 group_id，不得新增、改寫或猜測。
2. 只提出有明確公開網頁證據的關係；不確定者省略，不要為每家公司強行建立集團。
3. 每位成員的 evidence_json.sources 至少包含一筆 https URL、頁面標題與支持該歸屬的摘要。
4. 你只能提供建議，不得宣稱已確認或已修改資料庫。
5. 加入既有集團時填 target_group_id 並省略 group_name；建立新集團時填 group_name 並省略 target_group_id。
6. 只輸出 JSON，不要 Markdown。格式如下：
{{"suggestions":[{{"target_group_id":123,"members":[{{"company_code":"...","company_display_name":"...","evidence_json":{{"confidence":"low|medium|high","sources":[{{"url":"https://...","title":"...","claim":"..."}}],"warnings":[]}}}}]}},{{"group_name":"新集團名稱","members":[{{"company_code":"...","company_display_name":"...","evidence_json":{{"confidence":"low|medium|high","sources":[{{"url":"https://...","title":"...","claim":"..."}}],"warnings":[]}}}}]}}]}}

候選公司：
{payload}
"""


def _extract_suggestions(raw: str) -> list[dict[str, Any]]:
    """解析 CLI JSON，拒絕非物件或非陣列結果。"""
    payload = extract_json_payload(raw)
    suggestions = payload.get("suggestions") if isinstance(payload, dict) else None
    if not isinstance(suggestions, list):
        raise ValueError("CLI company group result requires suggestions list")
    return suggestions


def _resolve_suggestion_target(
    suggestion: dict[str, Any], known_groups: dict[int, str]
) -> tuple[int | None, str]:
    """解析新集團名稱或受控既有集團 ID。"""
    raw_target = suggestion.get("target_group_id")
    if raw_target is None:
        return None, str(suggestion.get("group_name") or "").strip()
    if isinstance(raw_target, bool):
        raise ValueError("target_group_id must be an integer")  # noqa: TRY004
    try:
        target_group_id = int(raw_target)
    except (TypeError, ValueError) as exc:
        raise ValueError("target_group_id must be an integer") from exc
    if target_group_id not in known_groups:
        raise ValueError(f"unknown target_group_id: {target_group_id}")
    return target_group_id, known_groups[target_group_id]


def _require_https_evidence(member: dict[str, Any], *, code: str) -> dict[str, Any]:
    """取出 evidence 並要求至少一筆 HTTPS 來源。"""
    evidence = member.get("evidence_json")
    sources = evidence.get("sources") if isinstance(evidence, dict) else None
    has_https_source = isinstance(sources, list) and any(
        isinstance(source, dict)
        and str(source.get("url") or "").startswith("https://")
        for source in sources
    )
    if not has_https_source:
        raise ValueError(f"company group suggestion requires https source: {code}")
    return evidence


def _validate_suggestion_member(
    member: Any,
    *,
    known: dict[str, str],
    seen_codes: set[str],
) -> dict[str, Any]:
    """驗證單一候選公司身分與 HTTPS 證據。"""
    if not isinstance(member, dict):
        raise ValueError("suggestion member must be an object")  # noqa: TRY004
    code = str(member.get("company_code") or "")
    name = str(member.get("company_display_name") or "")
    if code not in known or known[code] != name:
        raise ValueError(f"unknown company in suggestion: {code or name}")
    if code in seen_codes:
        raise ValueError(f"company appears in multiple suggestions: {code}")
    evidence = _require_https_evidence(member, code=code)
    seen_codes.add(code)
    return {
        "company_code": code,
        "company_display_name": name,
        "review_status": "suggested",
        "source_type": "cli_ai",
        "evidence_json": evidence,
    }


def _validate_suggestion(
    suggestion: Any,
    *,
    known: dict[str, str],
    known_groups: dict[int, str],
    seen_codes: set[str],
) -> dict[str, Any]:
    """驗證一組新集團或既有集團成員建議。"""
    if not isinstance(suggestion, dict):
        raise ValueError("suggestion must be an object")  # noqa: TRY004
    target_group_id, group_name = _resolve_suggestion_target(suggestion, known_groups)
    members = suggestion.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("suggestion members must be a non-empty list")
    normalized_members = [
        _validate_suggestion_member(member, known=known, seen_codes=seen_codes)
        for member in members
    ]
    return {
        "group_name": group_name,
        "target_group_id": target_group_id,
        "review_status": "suggested",
        "source_type": "cli_ai",
        "members": normalized_members,
    }


def _validated_suggestions(
    suggestions: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    existing_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """鎖定候選身分、https 證據與 suggested-only 狀態。"""
    known = {
        str(item["company_code"]): str(item["company_display_name"])
        for item in candidates
    }
    known_groups = {
        int(item["group_id"]): str(item["group_name"])
        for item in existing_groups
    }
    seen_codes: set[str] = set()
    validated = [
        _validate_suggestion(
            suggestion,
            known=known,
            known_groups=known_groups,
            seen_codes=seen_codes,
        )
        for suggestion in suggestions
    ]
    return [repository.validate_cli_suggestion(item) for item in validated]


def _report_progress(
    progress: Callable[[str, int], None] | None, message: str, percent: int
) -> None:
    """有進度 callback 時才回報，不讓主流程重複判斷。"""
    if progress is not None:
        progress(message, percent)


def _run_cli_research(
    *,
    cli_kind: str,
    prompt: str,
    model: str | None,
    cli_runner: Callable[..., str] | None,
    timeout_seconds: float,
) -> str:
    """執行正式 CLI 或測試注入 runner，統一回傳文字結果。"""
    if cli_runner is not None:
        return str(cli_runner(prompt, timeout_seconds=timeout_seconds))
    result = run_cli(
        build_company_group_cli_command(cli_kind, prompt, model=model), timeout_seconds
    )
    return str(parse_cli_result(result).get("result") or "")


def run_company_group_suggestions(
    *,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: Callable[..., str] | None = None,
    store: Any | None = None,
    limit: int | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, int]:
    """讀候選、連網查證、驗證結果，再寫入 review-only suggestions。"""
    _require_supported_cli(cli_kind)
    suggestion_store = store if store is not None else CompanyGroupSuggestionStore()
    _report_progress(progress, "讀取未分組公司", 10)
    candidates = suggestion_store.fetch_candidates(limit=limit)
    if not candidates:
        _report_progress(progress, "沒有待查證的公司", 100)
        return {"candidate_count": 0, "suggestion_count": 0, "inserted": 0}

    existing_groups = suggestion_store.fetch_existing_groups()
    prompt = build_prompt(candidates, existing_groups)
    _report_progress(progress, "連網查證集團關係", 30)
    raw = _run_cli_research(
        cli_kind=cli_kind,
        prompt=prompt,
        model=model,
        cli_runner=cli_runner,
        timeout_seconds=timeout_seconds,
    )

    suggestions = _validated_suggestions(
        _extract_suggestions(raw), candidates, existing_groups
    )
    _report_progress(progress, "寫入待審核建議", 85)
    write_result = (
        suggestion_store.ingest_suggestions(suggestions)
        if suggestions
        else {"inserted": 0}
    )
    _report_progress(progress, "集團建議已產生", 100)
    return {
        "candidate_count": len(candidates),
        "suggestion_count": len(suggestions),
        "inserted": int(write_result.get("inserted", 0)),
    }
