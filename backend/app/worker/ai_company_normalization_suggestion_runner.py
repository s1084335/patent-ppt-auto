"""公司正規化 AI 建議 runner。

本任務讓 CLI 連網查證公司變體、法人中文名與自然人公司關係，但輸出只能成為
`company_aliases.review_status='ai_suggested'` 的待審資料；WIPS code 永遠由 Backend
受控 target map 或 TEMP 規則決定，AI 不得回傳或覆寫。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from backend.app.transforms.text import clean_text
from backend.app.worker.ai_payload_file import extract_json_payload
from backend.app.worker.cli_gateway import (
    WEB_RESEARCH_TOOLS,
    build_cli_command,
    parse_cli_result,
    run_cli,
)

DEFAULT_CLI_TIMEOUT_SECONDS = 600.0
PROMPT_VERSION = "company_normalization_suggestion_v2"

SUGGESTION_KINDS = frozenset(
    {"map_existing", "update_names", "create_temp", "person_affiliation"}
)
NAME_BASES = frozenset({"market_common_name", "registered_legal_name"})
CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})
PERSON_ROLES = frozenset({"owner", "proprietor", "director"})
FORBIDDEN_CODE_FIELDS = frozenset({"code", "wips_code", "company_code", "code_override"})


class SkippableSuggestionError(ValueError):
    """代表單筆 AI 建議可被跳過，但不應讓整個 job 失敗。"""


class CompanyNormalizationSuggestionStore:
    """集中封裝 DB 讀寫；runner 本身只處理契約與驗證。"""

    def fetch_candidates(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """讀取未正式歸戶的公司／自然人原始變體。"""
        from backend.app.derived.company_alias_importer import (
            list_company_normalization_candidates,
        )

        return list_company_normalization_candidates(limit=limit)

    def fetch_targets(self) -> list[dict[str, Any]]:
        """讀取已確認公司白名單；code 只留在 Backend 私有 map。"""
        from backend.app.derived.company_alias_importer import (
            list_company_normalization_targets,
        )

        return list_company_normalization_targets()

    def ingest_suggestions(self, suggestions: list[dict[str, Any]]) -> dict[str, Any]:
        """把驗證後的建議寫成 review-only rows。"""
        from backend.app.derived.company_alias_importer import (
            ingest_company_normalization_suggestions,
        )

        return ingest_company_normalization_suggestions(suggestions)


def _require_supported_cli(cli_kind: str) -> None:
    """連網查證只能使用可強制工具白名單的 Claude CLI。"""
    if cli_kind != "claude":
        raise ValueError("company normalization web research requires claude tool whitelist")


def build_company_normalization_cli_command(
    cli_kind: str, prompt: str, *, model: str | None = None
) -> list[str]:
    """建立只允許 WebSearch/WebFetch 的 CLI 命令。"""
    _require_supported_cli(cli_kind)
    return build_cli_command(cli_kind, prompt, model=model, tools=WEB_RESEARCH_TOOLS)


def _public_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """移除 WIPS code 後才交給 CLI，保留 target_ref 與公司名供指向。"""
    return [
        {
            "target_ref": item["target_ref"],
            "zh_name": item.get("zh_name"),
            "normalized_name": item.get("normalized_name"),
        }
        for item in targets
    ]


def build_prompt(
    candidates: list[dict[str, Any]],
    targets: list[dict[str, Any]],
) -> str:
    """將受控候選與 target refs 組成嚴格 JSON 查證任務。"""
    payload = json.dumps(
        {"candidates": candidates, "confirmed_targets": _public_targets(targets)},
        ensure_ascii=False,
        indent=2,
    )
    return f"""你是公司正規化查證助手。請使用公開網頁，判斷候選原始名稱應如何歸戶。

限制：
1. 只能使用輸入中的 candidate_ref 與 target_ref；不得新增、猜測、輸出 WIPS code、company_code、code 或 code_override。
2. suggestion_kind 只能是 map_existing、update_names、create_temp、person_affiliation。
3. map_existing/update_names/person_affiliation 若指向既有公司，只能填 target_ref。
4. create_temp 表示沒有可靠既有目標，仍不得輸出代碼；Backend 會自行產 TEMP:*。
5. 中文名必須有來源。zh_name_basis 只能是 market_common_name 或 registered_legal_name；翻譯、音譯、模型記憶不足以建立建議。
6. person_affiliation 只接受 owner、proprietor、director；founder、CEO、經理、員工、發明人、聯絡人或同名都不足。
7. evidence、person_identity_evidence、relationship_evidence 的 url 都必須是 https。
8. 每一筆 suggestion 必須提供 evidence 且至少 1 筆 HTTPS source；找不到證據就不要輸出該 suggestion。
9. 只輸出 JSON，不要 Markdown。格式：
{{"suggestions":[{{"suggestion_kind":"map_existing","candidate_refs":["..."],"target_ref":"...","suggested_zh_name":"...","suggested_normalized_name":"...","zh_name_basis":"market_common_name","confidence":"low|medium|high","reason":"...","evidence":[{{"url":"https://...","title":"...","claim":"..."}}],"warnings":[]}}]}}

受控輸入：
{payload}
"""


def _extract_suggestions(raw: str) -> list[dict[str, Any]]:
    """解析 CLI JSON，容忍 Claude 前後包文字或 code fence。"""
    payload = extract_json_payload(raw)
    suggestions = payload.get("suggestions") if isinstance(payload, dict) else None
    if not isinstance(suggestions, list):
        raise ValueError("CLI company normalization result requires suggestions list")
    return suggestions


def _require_no_code_fields(item: dict[str, Any]) -> None:
    """AI 結果不得含任何可寫 code 欄位，未知欄位中的 code 也拒絕。"""
    forbidden = sorted(FORBIDDEN_CODE_FIELDS.intersection(item.keys()))
    if forbidden:
        raise ValueError(f"AI suggestion contains forbidden code field: {', '.join(forbidden)}")


def _clean_limited(value: Any, *, field: str, max_length: int) -> str:
    """清理並限制文字欄位，避免過長 AI 文本進 metadata。"""
    text = clean_text(value) or ""
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > max_length:
        raise ValueError(f"{field} is too long")
    return text


def _optional_clean_limited(value: Any, *, field: str, max_length: int) -> str | None:
    """可空文字欄位的清理與長度限制。"""
    text = clean_text(value)
    if text and len(text) > max_length:
        raise ValueError(f"{field} is too long")
    return text


def _https_evidence(value: Any, *, field: str, min_count: int = 1) -> list[dict[str, str]]:
    """驗證 evidence 陣列且每筆來源必須是 HTTPS。"""
    if not isinstance(value, list) or len(value) < min_count:
        raise ValueError(f"{field} requires at least {min_count} source(s)")
    rows: list[dict[str, str]] = []
    for source in value:
        if not isinstance(source, dict):
            raise ValueError(f"{field} source must be an object")
        url = _clean_limited(source.get("url"), field=f"{field}.url", max_length=500)
        if not url.startswith("https://"):
            raise ValueError(f"{field} requires https source")
        rows.append(
            {
                "url": url,
                "title": _optional_clean_limited(
                    source.get("title"), field=f"{field}.title", max_length=200
                )
                or "",
                "claim": _clean_limited(
                    source.get("claim"), field=f"{field}.claim", max_length=500
                ),
            }
        )
    return rows


def _skippable_required_evidence(value: Any, *, field: str) -> list[dict[str, str]]:
    """驗證主建議 evidence；缺證據時跳過該筆，不中斷整批 job。"""
    try:
        return _https_evidence(value, field=field)
    except ValueError as exc:
        raise SkippableSuggestionError(str(exc)) from exc


def _validate_person_fields(item: dict[str, Any]) -> dict[str, Any]:
    """驗證自然人關係角色與證據門檻。"""
    role = _clean_limited(item.get("relationship_role"), field="relationship_role", max_length=40)
    if role not in PERSON_ROLES:
        raise ValueError(f"unsupported relationship_role: {role}")
    return {
        "relationship_role": role,
        "person_identity_evidence": _https_evidence(
            item.get("person_identity_evidence"), field="person_identity_evidence"
        ),
        "relationship_evidence": _https_evidence(
            item.get("relationship_evidence"), field="relationship_evidence"
        ),
    }


def _validate_suggestion(
    item: Any,
    *,
    candidates_by_ref: dict[str, dict[str, Any]],
    targets_by_ref: dict[str, dict[str, Any]],
    seen_refs: set[str],
) -> dict[str, Any]:
    """驗證單筆 AI 建議，並把 target_ref 私下解析成 Backend 權威 code。"""
    if not isinstance(item, dict):
        raise ValueError("suggestion must be an object")
    _require_no_code_fields(item)
    kind = str(item.get("suggestion_kind") or "").strip()
    if kind not in SUGGESTION_KINDS:
        raise ValueError(f"unsupported suggestion_kind: {kind}")
    candidate_refs = item.get("candidate_refs")
    if not isinstance(candidate_refs, list) or not candidate_refs:
        raise ValueError("candidate_refs must be a non-empty list")
    normalized_refs: list[str] = []
    local_refs: set[str] = set()
    for ref in candidate_refs:
        ref_text = str(ref or "").strip()
        if ref_text not in candidates_by_ref:
            raise ValueError(f"unknown candidate_ref: {ref_text}")
        if ref_text in seen_refs or ref_text in local_refs:
            raise ValueError(f"candidate_ref appears in multiple suggestions: {ref_text}")
        local_refs.add(ref_text)
        normalized_refs.append(ref_text)

    target_ref = clean_text(item.get("target_ref"))
    target = None
    if kind != "create_temp":
        if not target_ref or target_ref not in targets_by_ref:
            raise ValueError(f"unknown target_ref: {target_ref or ''}")
        target = targets_by_ref[target_ref]
    elif target_ref and target_ref not in targets_by_ref:
        raise ValueError(f"unknown target_ref: {target_ref}")

    zh_name = _optional_clean_limited(
        item.get("suggested_zh_name"), field="suggested_zh_name", max_length=200
    )
    normalized_name = _optional_clean_limited(
        item.get("suggested_normalized_name"),
        field="suggested_normalized_name",
        max_length=200,
    )
    if not (zh_name or normalized_name):
        raise ValueError("suggested_zh_name or suggested_normalized_name is required")
    name_basis = _optional_clean_limited(
        item.get("zh_name_basis"), field="zh_name_basis", max_length=40
    )
    if zh_name and name_basis and name_basis not in NAME_BASES:
        raise ValueError(f"unsupported zh_name_basis: {name_basis}")
    confidence = str(item.get("confidence") or "").strip().lower()
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"unsupported confidence: {confidence}")

    metadata: dict[str, Any] = {
        "suggestion_kind": kind,
        "candidate_refs": normalized_refs,
        "target_ref": target_ref,
        "confidence": confidence,
        "reason": _clean_limited(item.get("reason"), field="reason", max_length=1000),
        "evidence": _skippable_required_evidence(item.get("evidence"), field="evidence"),
        "warnings": [
            _clean_limited(w, field="warning", max_length=500)
            for w in (item.get("warnings") or [])
            if clean_text(w)
        ],
        "zh_name_basis": name_basis,
        "prompt_version": PROMPT_VERSION,
    }
    if kind == "person_affiliation":
        metadata.update(_validate_person_fields(item))

    if target is not None:
        code = str(target["code"])
        final_zh = zh_name or target.get("zh_name")
        final_en = normalized_name or target.get("normalized_name")
    else:
        from backend.app.api.company_aliases import make_temp_code

        basis_name = normalized_name or zh_name or candidates_by_ref[normalized_refs[0]]["raw_name"]
        code = make_temp_code(basis_name)
        final_zh = zh_name
        final_en = normalized_name

    result = {
        "company_code": code,
        "zh_name": final_zh,
        "normalized_name": final_en,
        "candidate_refs": normalized_refs,
        "raw_names": [candidates_by_ref[ref]["raw_name"] for ref in normalized_refs],
        "review_status": "ai_suggested",
        "source_type": "ai_suggested",
        "metadata": metadata,
    }
    seen_refs.update(normalized_refs)
    return result


def _validated_suggestions(
    suggestions: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    targets: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """驗證 AI 建議；缺 evidence 的單筆建議跳過，其它契約錯誤維持硬失敗。"""
    candidates_by_ref = {str(item["candidate_ref"]): item for item in candidates}
    targets_by_ref = {str(item["target_ref"]): item for item in targets}
    seen_refs: set[str] = set()
    validated: list[dict[str, Any]] = []
    skipped_invalid = 0
    for item in suggestions:
        try:
            validated.append(
                _validate_suggestion(
                    item,
                    candidates_by_ref=candidates_by_ref,
                    targets_by_ref=targets_by_ref,
                    seen_refs=seen_refs,
                )
            )
        except SkippableSuggestionError:
            skipped_invalid += 1
    return validated, skipped_invalid


def _run_cli_research(
    *,
    cli_kind: str,
    prompt: str,
    model: str | None,
    cli_runner: Callable[..., str] | None,
    timeout_seconds: float,
) -> str:
    """執行正式 CLI 或測試注入 runner。"""
    if cli_runner is not None:
        return str(cli_runner(prompt, timeout_seconds=timeout_seconds))
    result = run_cli(
        build_company_normalization_cli_command(cli_kind, prompt, model=model),
        timeout_seconds,
    )
    return str(parse_cli_result(result).get("result") or "")


def _report_progress(
    progress: Callable[[str, int], None] | None, message: str, percent: int
) -> None:
    """有進度 callback 才回報。"""
    if progress is not None:
        progress(message, percent)


def run_company_normalization_suggestions(
    *,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: Callable[..., str] | None = None,
    store: Any | None = None,
    limit: int | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, int]:
    """讀候選、連網查證、驗證結果，再寫入公司正規化待審建議。"""
    _require_supported_cli(cli_kind)
    suggestion_store = store if store is not None else CompanyNormalizationSuggestionStore()
    _report_progress(progress, "讀取公司正規化候選", 10)
    candidates = suggestion_store.fetch_candidates(limit=limit)
    if not candidates:
        _report_progress(progress, "沒有待查證的公司變體", 100)
        return {"candidate_count": 0, "suggestion_count": 0, "inserted": 0}

    targets = suggestion_store.fetch_targets()
    prompt = build_prompt(candidates, targets)
    _report_progress(progress, "連網查證公司正規化", 35)
    raw = _run_cli_research(
        cli_kind=cli_kind,
        prompt=prompt,
        model=model,
        cli_runner=cli_runner,
        timeout_seconds=timeout_seconds,
    )
    suggestions, skipped_invalid = _validated_suggestions(
        _extract_suggestions(raw), candidates, targets
    )
    _report_progress(progress, "寫入公司正規化待審建議", 85)
    write_result = (
        suggestion_store.ingest_suggestions(suggestions)
        if suggestions
        else {"inserted": 0}
    )
    _report_progress(progress, "公司正規化建議已產生", 100)
    result = {
        "candidate_count": len(candidates),
        "suggestion_count": len(suggestions),
        "inserted": int(write_result.get("inserted", 0)),
    }
    if skipped_invalid:
        result["skipped_invalid"] = skipped_invalid
    return result
