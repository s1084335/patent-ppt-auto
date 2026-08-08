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
    return [f"slide {slide.get('slide_id', '?')} 含幾何欄位 {hits}——"
            "CLI 只給版型意圖，座標／字級／色彩由 builder 決定"]


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
