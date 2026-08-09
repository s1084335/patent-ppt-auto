"""goal-driven 報告規劃的資料契約（openspec change
enable-goal-driven-readonly-report-planning，第 1 節 tasks 1.2）。

五個結構的**唯一定義處**：runner、CLI 產出驗證、deterministic builder 與前端
共用同一份規則，不各自定義。

設計要點（design.md）：
- `ReportBrief`＋`SelectedChartBundle` 是**唯一任務輸入**：圖片與數據必須成對，
  以 checksum/version 阻止錯配（只給圖失去精確數字，只給 JSON 失去視覺判讀）。
- CLI 只產**內容與版型意圖**：不得輸出座標／字級／色彩，也不得自行加入
  未選圖表或遺漏使用者選的圖。
- 每個帶數字的敘述都要有 `evidence_ref`，且 evidence 的 snapshot 必須與本次一致
  （stale response 不得進 manifest）。
"""
from __future__ import annotations

import re
from typing import Any

# 版型 preset 白名單：CLI 只能從**已核准版型**中挑，實際幾何由 builder 解析。
# ⚠ 這是「備選版型庫不是必出清單」（2026-08-07 定案）的程式面：能選什麼在這裡，
# 每次出哪幾種由內容決定。新增版型＝先在 builder 實作再進本清單。
APPROVED_LAYOUT_PRESETS: frozenset[str] = frozenset({
    "cover", "exec_summary", "walls_gaps",
    "chart_hero", "chart_with_points", "chart_wide", "comparison", "percentage_bars",
    "table", "table_with_points", "stat_callout", "section_divider",
    "kp_quadrant", "kp_deepdive", "kp_compare", "kp_cards",
    "reading_guide", "direction",
})

# CLI 不得出現的幾何欄位（deterministic builder 專責）。
_GEOMETRY_KEYS = ("left_in", "top_in", "width_in", "height_in", "font_pt",
                  "color", "rgb", "x", "y", "size_pt")

_NUMBER_PATTERN = re.compile(r"\d")

# 要點的硬上限。⚠ 這**不是**版面容量（容量由組版端逐頁算，且隨版型而異），
# 是防極端值的守門。
#
# 動因（2026-08-09 實機 p8）：CLI 在一頁寫了 4 條、每條約 40 字，組版端算出
# 需要 12 行但該版型只有 11 行，最後一條被整條丟棄。prompt 給的是「2–4 條、
# 每條 30 字內」的品質指引；這裡只擋明顯離譜的值——若卡在 31 字就讓整個 job
# 失敗，使用者會什麼都看不到，那比少一條要點更糟。
MAX_POINTS_PER_SLIDE = 5
MAX_POINT_CHARS = 50


def validate_chart_bundle(bundle: dict[str, Any]) -> list[str]:
    """單一選圖資料包：圖片與數據成對、identity 與版本齊備。"""
    errors: list[str] = []
    for field in ("chart_identity", "report_key", "image_path", "version", "checksum"):
        if not str(bundle.get(field) or "").strip():
            errors.append(f"selected chart 缺 {field}")
    if not bundle.get("data_rows"):
        errors.append(
            f"{bundle.get('chart_identity', '?')} 缺 data_rows——"
            "圖片與結構化數據必須成對（只給圖會失去精確數字）")
    return errors


PPT_QUALITY_RETRY_LIMIT = 2
PPT_QUALITY_DECISIONS = ("pass", "regenerate_partial", "regenerate_report_version", "blocked_defect")
PPT_QUALITY_BLOCKED_DEFECT_TYPES = ("blocked_content_defect", "blocked_layout_defect")

_PARTIAL_REGENERATION_WARNINGS = frozenset({
    "narrative_missing",
    "narrative_fallback",
    "chart_missing_degraded",
    "missing_slots",
    "text_overflow_estimated",
})
_REPORT_VERSION_WARNINGS = frozenset({"artifact_manifest_missing"})
_LAYOUT_BLOCKING_WARNINGS = frozenset({"text_overlap", "out_of_bounds", "margin_violation"})

PPT_QUALITY_JSON_SCHEMAS: dict[str, dict[str, Any]] = {
    "PptQualityReport": {
        "type": "object",
        "required": [
            "schema_version",
            "decision",
            "issues",
            "render",
            "selected_chart_coverage",
            "evidence_coverage",
            "required_slot_coverage",
        ],
        "properties": {
            "schema_version": {"const": "ppt-quality-report.v1"},
            "decision": {"type": "string", "enum": list(PPT_QUALITY_DECISIONS)},
            "blocked_defect_type": {
                "type": ["string", "null"],
                "enum": [*PPT_QUALITY_BLOCKED_DEFECT_TYPES, None],
            },
            "issues": {"type": "array", "items": {"type": "object"}},
            "regeneration_plan": {"type": ["object", "null"]},
            "retry_limit": {"type": "integer", "maximum": PPT_QUALITY_RETRY_LIMIT},
        },
    },
    "RenderedPngManifest": {
        "type": "object",
        "required": ["render_success", "page_count", "pages"],
        "properties": {
            "render_success": {"type": "boolean"},
            "page_count": {"type": "integer", "minimum": 0},
            "pages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["page", "png_path", "rendered"],
                    "properties": {
                        "page": {"type": "integer", "minimum": 1},
                        "png_path": {"type": "string"},
                        "sha256": {"type": "string"},
                        "rendered": {"type": "boolean"},
                    },
                },
            },
        },
    },
    "RegenerationPlan": {
        "type": "object",
        "required": ["decision", "retry_limit", "targets", "locked"],
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["regenerate_partial", "regenerate_report_version", "blocked_defect"],
            },
            "retry_limit": {"type": "integer", "minimum": 0, "maximum": PPT_QUALITY_RETRY_LIMIT},
            "targets": {"type": "array", "items": {"type": "object"}},
            "locked": {"$ref": "#/$defs/ScopeLock"},
            "blocked_defect_type": {"type": "string", "enum": list(PPT_QUALITY_BLOCKED_DEFECT_TYPES)},
        },
    },
    "ScopeLock": {
        "type": "object",
        "required": ["slide_ids", "chart_identities", "narrative_keys", "evidence_refs"],
        "properties": {
            "slide_ids": {"type": "array", "items": {"type": "string"}},
            "chart_identities": {"type": "array", "items": {"type": "string"}},
            "narrative_keys": {"type": "array", "items": {"type": "string"}},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def _warning_type(warning: dict[str, Any]) -> str:
    """取出 manifest warning 的穩定型別欄位。"""
    return str(warning.get("type") or warning.get("warning_type") or "").strip()


def _target_key(issue: dict[str, Any]) -> str:
    """產生 retry 計數使用的 target key。"""
    slide_id = issue.get("slide_id") or issue.get("page") or issue.get("report_key") or "report"
    return f"{slide_id}:{issue['type']}"


def _issue_from_warning(warning: dict[str, Any]) -> dict[str, Any]:
    """把 builder manifest warning 正規化成 quality issue。"""
    issue = dict(warning)
    issue["type"] = _warning_type(warning)
    issue.setdefault("severity", "fail")
    return issue


def _slide_values(slides: list[dict[str, Any]], key: str) -> set[str]:
    """收集 slide manifest 內的列表欄位值。"""
    values: set[str] = set()
    for slide in slides:
        values.update(str(value) for value in (slide.get(key) or []) if str(value).strip())
    return values


def _collect_missing_slots(slides: list[dict[str, Any]]) -> dict[str, list[str]]:
    """找出每頁 required_slots 中尚未填入的 slot。"""
    missing: dict[str, list[str]] = {}
    for slide in slides:
        slide_id = str(slide.get("slide_id") or "?")
        required = {str(value) for value in (slide.get("required_slots") or []) if str(value).strip()}
        filled = {str(value) for value in (slide.get("filled_slots") or []) if str(value).strip()}
        gaps = sorted(required - filled)
        if gaps:
            missing[slide_id] = gaps
    return missing


def _scope_lock(slides: list[dict[str, Any]], selected_chart_identities: set[str]) -> dict[str, list[str]]:
    """建立局部重產時不可被 CLI 擴大修改的範圍鎖。"""
    narrative_keys = {
        str(value)
        for slide in slides
        for value in (slide.get("narrative_keys") or [])
        if str(value).strip()
    }
    return {
        "slide_ids": sorted(str(slide.get("slide_id")) for slide in slides if str(slide.get("slide_id") or "").strip()),
        "chart_identities": sorted(selected_chart_identities | _slide_values(slides, "chart_identities")),
        "narrative_keys": sorted(narrative_keys),
        "evidence_refs": sorted(_slide_values(slides, "evidence_refs")),
    }


def _target_for_issue(issue: dict[str, Any], *, retry_counts: dict[str, int]) -> dict[str, Any] | None:
    """依 warning 類型建立 regeneration target；blocked 類型不交給 CLI。"""
    warning_type = issue["type"]
    if warning_type in _LAYOUT_BLOCKING_WARNINGS:
        return None
    if warning_type in _REPORT_VERSION_WARNINGS:
        return {"scope": "report_version", "warning_type": warning_type}
    if warning_type == "text_overflow_estimated" and retry_counts.get(_target_key(issue), 0) >= PPT_QUALITY_RETRY_LIMIT:
        return None
    if warning_type in _PARTIAL_REGENERATION_WARNINGS:
        return {
            "scope": "partial",
            "warning_type": warning_type,
            "slide_id": issue.get("slide_id"),
            "report_key": issue.get("report_key"),
            "variant_key": issue.get("variant_key"),
            "slot": issue.get("slot"),
        }
    if warning_type in {"selected_chart_missing", "unselected_chart_used", "evidence_missing"}:
        return {"scope": "partial", "warning_type": warning_type, "slide_id": issue.get("slide_id")}
    return None


def _decide_quality(issues: list[dict[str, Any]], retry_counts: dict[str, int]) -> tuple[str, str | None]:
    """以固定優先序把 quality issues 摺疊成唯一 decision。"""
    if not issues:
        return "pass", None
    for issue in issues:
        warning_type = issue["type"]
        if warning_type in {"png_render_failed", "page_count_mismatch", "text_overlap", "out_of_bounds", "margin_violation"}:
            return "blocked_defect", "blocked_layout_defect"
        if warning_type == "text_overflow_estimated" and retry_counts.get(_target_key(issue), 0) >= PPT_QUALITY_RETRY_LIMIT:
            return "blocked_defect", "blocked_content_defect"
    if any(issue["type"] in _REPORT_VERSION_WARNINGS for issue in issues):
        return "regenerate_report_version", None
    return "regenerate_partial", None


def _regeneration_plan(
    *,
    decision: str,
    blocked_defect_type: str | None,
    issues: list[dict[str, Any]],
    locked: dict[str, list[str]],
    retry_counts: dict[str, int],
) -> dict[str, Any] | None:
    """依 decision 產生 runner 可持久化的 RegenerationPlan。"""
    if decision == "pass":
        return None
    targets = [
        target
        for issue in issues
        if (target := _target_for_issue(issue, retry_counts=retry_counts)) is not None
    ]
    plan: dict[str, Any] = {
        "schema_version": "regeneration-plan.v1",
        "decision": decision,
        "retry_limit": PPT_QUALITY_RETRY_LIMIT,
        "targets": targets,
        "locked": locked,
    }
    if blocked_defect_type:
        plan["blocked_defect_type"] = blocked_defect_type
    return plan


def build_ppt_quality_report(
    *,
    pptx_manifest: dict[str, Any],
    rendered_png_manifest: dict[str, Any],
    selected_chart_identities: list[str] | set[str] | tuple[str, ...],
    evidence_manifest: dict[str, Any] | None = None,
    retry_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """彙整 PPTX/PNG/evidence manifest，產生交付前 quality gate 報告。"""
    evidence_manifest = evidence_manifest or {}
    retry_counts = retry_counts or {}
    slides = list(pptx_manifest.get("slides") or [])
    selected = {str(value) for value in selected_chart_identities if str(value).strip()}
    used = _slide_values(slides, "chart_identities")
    evidence_refs = _slide_values(slides, "evidence_refs")

    issues = [_issue_from_warning(warning) for warning in (pptx_manifest.get("warnings") or [])]
    missing_slots = _collect_missing_slots(slides)
    for slide_id, slots in missing_slots.items():
        for slot in slots:
            issues.append({"type": "missing_slots", "severity": "fail", "slide_id": slide_id, "slot": slot})

    missing_selected = sorted(selected - used)
    for identity in missing_selected:
        issues.append({"type": "selected_chart_missing", "severity": "fail", "chart_identity": identity})
    unselected_used = sorted(used - selected)
    for identity in unselected_used:
        issues.append({"type": "unselected_chart_used", "severity": "fail", "chart_identity": identity})

    missing_evidence = sorted(ref for ref in evidence_refs if ref not in evidence_manifest)
    for ref in missing_evidence:
        issues.append({"type": "evidence_missing", "severity": "fail", "evidence_ref": ref})

    png_pages = rendered_png_manifest.get("pages") or []
    failed_pages = [
        page
        for page in png_pages
        if not page.get("rendered", rendered_png_manifest.get("render_success", False))
    ]
    if rendered_png_manifest.get("render_success") is False or failed_pages:
        issues.append({"type": "png_render_failed", "severity": "fail", "failed_pages": failed_pages})
    if int(pptx_manifest.get("page_count") or 0) != int(rendered_png_manifest.get("page_count") or 0):
        issues.append({
            "type": "page_count_mismatch",
            "severity": "fail",
            "pptx_page_count": int(pptx_manifest.get("page_count") or 0),
            "rendered_page_count": int(rendered_png_manifest.get("page_count") or 0),
        })

    decision, blocked_defect_type = _decide_quality(issues, retry_counts)
    locked = _scope_lock(slides, selected)
    return {
        "schema_version": "ppt-quality-report.v1",
        "report_version_id": pptx_manifest.get("report_version_id"),
        "decision": decision,
        "blocked_defect_type": blocked_defect_type,
        "retry_limit": PPT_QUALITY_RETRY_LIMIT,
        "issues": issues,
        "render": rendered_png_manifest,
        "selected_chart_coverage": {
            "selected": sorted(selected),
            "used": sorted(used),
            "missing": missing_selected,
            "unselected_used": unselected_used,
        },
        "evidence_coverage": {
            "referenced": sorted(evidence_refs),
            "available": sorted(str(ref) for ref in evidence_manifest),
            "missing": missing_evidence,
        },
        "required_slot_coverage": {"missing": missing_slots},
        "regeneration_plan": _regeneration_plan(
            decision=decision,
            blocked_defect_type=blocked_defect_type,
            issues=issues,
            locked=locked,
            retry_counts=retry_counts,
        ),
    }


def validate_report_brief(brief: dict[str, Any]) -> list[str]:
    """任務輸入契約：目標、頁數預算、snapshot 與選圖集合。"""
    errors: list[str] = []
    if not str(brief.get("north_star_goal") or "").strip():
        errors.append("north_star_goal 不得為空——沒有最大目標就無從規劃論證")
    if not str(brief.get("snapshot_id") or "").strip():
        errors.append("缺 snapshot_id——證據必須綁定資料快照")
    budget = brief.get("page_budget")
    if not isinstance(budget, int) or budget <= 0:
        errors.append("page_budget 必須是正整數")
    charts = brief.get("selected_charts") or []
    if not charts:
        errors.append("selected_charts 不得為空——使用者未選圖不得自動進 PPT")
    seen: set[str] = set()
    for bundle in charts:
        errors.extend(validate_chart_bundle(bundle))
        identity = str(bundle.get("chart_identity") or "")
        if identity in seen:
            errors.append(f"selected chart identity 重複：{identity}")
        seen.add(identity)
    return errors


def _slide_geometry_errors(slide: dict[str, Any]) -> list[str]:
    hits = [k for k in _GEOMETRY_KEYS if k in slide]
    if not hits:
        return []
    return [(f"slide {slide.get('slide_id', '?')} 含幾何欄位 {hits}——"
             "CLI 只給版型意圖，座標／字級／色彩由 builder 決定")]


def validate_slide_plan(
    plan: dict[str, Any],
    selected_identities: set[str],
    page_budget: int | None = None,
) -> list[str]:
    """SlidePlan：選圖完整性、版型白名單、無幾何、頁數預算。"""
    errors: list[str] = []
    slides = plan.get("slides") or []
    if not str(plan.get("plan_id") or "").strip():
        errors.append("缺 plan_id")
    if page_budget is not None and len(slides) > page_budget:
        errors.append(f"slides {len(slides)} 張超過 page_budget {page_budget}")
    used: set[str] = set()
    for slide in slides:
        sid = slide.get("slide_id") or "?"
        if not str(slide.get("purpose") or "").strip():
            errors.append(f"slide {sid} 缺 purpose（這頁要回答什麼）")
        preset = str(slide.get("layout_preset") or "")
        if preset not in APPROVED_LAYOUT_PRESETS:
            errors.append(f"slide {sid} layout_preset {preset!r} 不在核准版型清單")
        errors.extend(_slide_geometry_errors(slide))
        points = slide.get("narrative") or []
        if len(points) > MAX_POINTS_PER_SLIDE:
            errors.append(
                f"slide {sid} 有 {len(points)} 條要點，超過上限 {MAX_POINTS_PER_SLIDE}"
                "——版面放不下的會被整條丟棄")
        for point in points:
            text = str(point.get("text") or "")
            if len(text) > MAX_POINT_CHARS:
                errors.append(
                    f"slide {sid} 的要點長 {len(text)} 字，超過上限 {MAX_POINT_CHARS}："
                    f"{text[:20]}…")
        for identity in slide.get("chart_identities") or []:
            if identity not in selected_identities:
                errors.append(f"slide {sid} 使用未選圖表 {identity}")
            used.add(identity)
    missing = selected_identities - used
    if missing:
        errors.append(f"使用者選了但未使用的圖表：{sorted(missing)}")
    return errors


def validate_evidence(
    plan: dict[str, Any],
    manifest: dict[str, Any],
    snapshot_id: str,
) -> list[str]:
    """EvidenceManifest：ref 可解析、snapshot 一致、帶數字的敘述必須有依據。"""
    errors: list[str] = []
    for ref, entry in manifest.items():
        entry_snapshot = str(entry.get("snapshot_id") or "")
        if entry_snapshot != snapshot_id:
            errors.append(
                f"evidence {ref} 的 snapshot {entry_snapshot!r} 與本次 {snapshot_id!r} 不符"
                "——過期證據不得進 manifest")
    for slide in plan.get("slides") or []:
        sid = slide.get("slide_id") or "?"
        for point in slide.get("narrative") or []:
            ref = str(point.get("evidence_ref") or "")
            text = str(point.get("text") or "")
            if ref and ref not in manifest:
                errors.append(f"slide {sid} 的 evidence_ref {ref!r} 在 manifest 找不到")
            if not ref and _NUMBER_PATTERN.search(text):
                errors.append(
                    f"slide {sid} 有數字的敘述沒有 evidence_ref：{text[:20]!r}"
                    "——數字一律要能追到來源")
    return errors
