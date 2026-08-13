"""手動啟動的公司集團連網查證任務，輸出僅能進 suggested 審核流程。"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from backend.app.repositories import company_group_repository as repository
from backend.app.worker.cli_gateway import build_cli_command, parse_cli_result, run_cli


DEFAULT_CLI_TIMEOUT_SECONDS = 600.0
WEB_RESEARCH_TOOLS = ("WebSearch", "WebFetch")


class CompanyGroupSuggestionStore:
    """集中封裝候選讀取與既有 suggestion 寫入邊界。"""

    def fetch_candidates(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """讀取尚未分組的已確認公司。"""
        return repository.list_suggestion_candidates(limit=limit)

    def ingest_suggestions(self, suggestions: list[dict[str, Any]]) -> dict[str, Any]:
        """透過既有 repository 寫入 suggested group/member。"""
        return repository.ingest_cli_suggestions(suggestions)


def build_company_group_cli_command(
    cli_kind: str, prompt: str, *, model: str | None = None
) -> list[str]:
    """建立只允許 WebSearch 與 WebFetch 的 headless CLI 命令。"""
    if cli_kind != "claude":
        raise ValueError("company group web research requires claude tool whitelist")
    return build_cli_command(cli_kind, prompt, model=model, tools=WEB_RESEARCH_TOOLS)


def build_prompt(candidates: list[dict[str, Any]]) -> str:
    """將 backend 控制的候選公司內嵌為嚴格 JSON 查證任務。"""
    payload = json.dumps({"companies": candidates}, ensure_ascii=False, indent=2)
    return f"""你是公司集團歸屬的研究助手。請使用公開網頁查證下列公司是否屬於同一企業集團。

限制：
1. 只能使用輸入中的 company_code 與 company_display_name，不得新增、改寫或猜測公司。
2. 只提出有明確公開網頁證據的關係；不確定者省略，不要為每家公司強行建立集團。
3. 每位成員的 evidence_json.sources 至少包含一筆 https URL、頁面標題與支持該歸屬的摘要。
4. 你只能提供建議，不得宣稱已確認或已修改資料庫。
5. 只輸出 JSON，不要 Markdown。格式如下：
{{"suggestions":[{{"group_name":"集團名稱","members":[{{"company_code":"...","company_display_name":"...","evidence_json":{{"confidence":"low|medium|high","sources":[{{"url":"https://...","title":"...","claim":"..."}}],"warnings":[]}}}}]}}]}}

候選公司：
{payload}
"""


def _extract_suggestions(raw: str) -> list[dict[str, Any]]:
    """解析 CLI JSON，拒絕非物件或非陣列結果。"""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("CLI company group result must be valid JSON") from exc
    suggestions = payload.get("suggestions") if isinstance(payload, dict) else None
    if not isinstance(suggestions, list):
        raise ValueError("CLI company group result requires suggestions list")
    return suggestions


def _validated_suggestions(
    suggestions: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """鎖定候選身分、https 證據與 suggested-only 狀態。"""
    known = {
        str(item["company_code"]): str(item["company_display_name"])
        for item in candidates
    }
    seen_codes: set[str] = set()
    validated: list[dict[str, Any]] = []
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            raise ValueError("suggestion must be an object")
        members = suggestion.get("members")
        if not isinstance(members, list) or not members:
            raise ValueError("suggestion members must be a non-empty list")
        normalized_members: list[dict[str, Any]] = []
        for member in members:
            if not isinstance(member, dict):
                raise ValueError("suggestion member must be an object")
            code = str(member.get("company_code") or "")
            name = str(member.get("company_display_name") or "")
            if code not in known or known[code] != name:
                raise ValueError(f"unknown company in suggestion: {code or name}")
            if code in seen_codes:
                raise ValueError(f"company appears in multiple suggestions: {code}")
            evidence = member.get("evidence_json")
            sources = evidence.get("sources") if isinstance(evidence, dict) else None
            if not isinstance(sources, list) or not any(
                isinstance(source, dict)
                and str(source.get("url") or "").startswith("https://")
                for source in sources
            ):
                raise ValueError(f"company group suggestion requires https source: {code}")
            seen_codes.add(code)
            normalized_members.append(
                {
                    "company_code": code,
                    "company_display_name": name,
                    "review_status": "suggested",
                    "source_type": "cli_ai",
                    "evidence_json": evidence,
                }
            )
        validated.append(
            {
                "group_name": str(suggestion.get("group_name") or "").strip(),
                "review_status": "suggested",
                "source_type": "cli_ai",
                "members": normalized_members,
            }
        )
    return [repository.validate_cli_suggestion(item) for item in validated]


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
    if cli_kind != "claude":
        raise ValueError("company group web research requires claude tool whitelist")
    suggestion_store = store if store is not None else CompanyGroupSuggestionStore()
    if progress is not None:
        progress("讀取未分組公司", 10)
    candidates = suggestion_store.fetch_candidates(limit=limit)
    if not candidates:
        if progress is not None:
            progress("沒有待查證的公司", 100)
        return {"candidate_count": 0, "suggestion_count": 0, "inserted": 0}

    prompt = build_prompt(candidates)
    if progress is not None:
        progress("連網查證集團關係", 30)
    if cli_runner is None:
        result = run_cli(
            build_company_group_cli_command(cli_kind, prompt, model=model), timeout_seconds
        )
        raw = str(parse_cli_result(result).get("result") or "")
    else:
        raw = str(cli_runner(prompt, timeout_seconds=timeout_seconds))

    suggestions = _validated_suggestions(_extract_suggestions(raw), candidates)
    if progress is not None:
        progress("寫入待審核建議", 85)
    write_result = (
        suggestion_store.ingest_suggestions(suggestions)
        if suggestions
        else {"inserted": 0}
    )
    if progress is not None:
        progress("集團建議已產生", 100)
    return {
        "candidate_count": len(candidates),
        "suggestion_count": len(suggestions),
        "inserted": int(write_result.get("inserted", 0)),
    }
