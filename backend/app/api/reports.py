"""報表相關 API：建立 report_generate 工作、查詢報表工作結果。

backend 只建立工作與讀結果，實際跑報表引擎的是 worker。payload 對齊 worker
handlers.py 的 report_generate。report_names 與 filters 欄以既有報表定義的
白名單驗證，未知即 422。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.jobs import job_to_dict
from backend.app.db import job_repository
from backend.app.reports.report_definitions import (
    ALLOWED_FILTER_COLUMNS,
    DEFAULT_REPORT_NAMES,
    REPORT_DEFINITIONS,
    allowed_filter_columns_for_report,
)
from backend.app.worker import ai_report_ppt_runner


router = APIRouter(tags=["reports"])


class ReportRequest(BaseModel):
    """建立報表產生工作。"""

    report_names: list[str] | None = None
    filters: dict[str, Any] | None = None
    limit: int | None = Field(default=None, ge=1)
    patent_ids: list[int] | None = None
    idempotency_key: str | None = Field(default=None, max_length=200)
    # 分群類報表（主題統計表／機會板／痛點板）的範圍：分群一律以 workspace 為單位。
    # 2026-07-28 補：原本前端沒送、model 也沒這欄，worker 端 workspace_id 恆為 None，
    # 三份分群報表一律靜默跳過——就算跑過分群也產不出來。None＝全庫報表，沒有分群範圍。
    workspace_id: int | None = Field(default=None, ge=1)


@router.get("/report-definitions")
def list_report_definitions() -> dict[str, Any]:
    """列出可用報表定義與篩選白名單——前端探索報表的入口。"""
    reports = [
        {
            "name": name,
            "label_zh": definition.label_zh,
            "label": definition.label,
            "report_type": definition.report_type,
            "filter_mode": "patent_level" if definition.supports_patent_ids else "family_translated",
        }
        for name, definition in sorted(REPORT_DEFINITIONS.items())
    ]
    return {
        "reports": reports,
        "default_report_names": list(DEFAULT_REPORT_NAMES),
        "allowed_filter_columns": sorted(ALLOWED_FILTER_COLUMNS),
    }


def _ppt_page_spec_to_dict(page_spec: Any) -> dict[str, Any]:
    """把 build_ppt.py 的 PageSpec 轉成前端可直接使用的 JSON。"""
    return {
        "page": int(page_spec.page),
        "kind": str(page_spec.kind),
        "title": str(page_spec.title),
        "subtitle": str(page_spec.subtitle or ""),
        "report_keys": list(page_spec.report_keys),
        "charts": list(page_spec.charts),
        "slots": list(page_spec.slots),
        "source": "template",
    }


def _ppt_kind_for_report(report_type: str) -> str:
    """依報表型態挑預設 PPT 版型，真正位置仍由 theme.json geometry 決定。"""
    if report_type == "detail":
        return "table"
    return "chart_with_narrative"


def _expand_ppt_pages_with_active_reports(template_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 active reports 補進 PPT 頁面，且保持結論與附錄類頁面在最後段。"""
    covered = {
        report_key
        for page in template_pages
        for report_key in page["report_keys"]
    }
    dynamic_pages: list[dict[str, Any]] = []
    for name, definition in REPORT_DEFINITIONS.items():
        if name in covered:
            continue
        dynamic_pages.append({
            "page": 0,
            "kind": _ppt_kind_for_report(definition.report_type),
            "title": definition.label_zh or definition.label,
            "subtitle": "",
            "report_keys": [name],
            "charts": [],
            "slots": [],
            "source": "report_definition",
        })

    insert_at = len(template_pages)
    for idx, page in enumerate(template_pages):
        if page["kind"] == "narrative_only" or any(
            marker in page["title"] for marker in ("結論", "附錄")
        ):
            insert_at = idx
            break

    pages = template_pages[:insert_at] + dynamic_pages + template_pages[insert_at:]
    for page_no, page in enumerate(pages, start=1):
        page["page"] = page_no
    return pages


@router.get("/reports/ppt-layout")
def get_report_ppt_layout() -> dict[str, Any]:
    """提供 PPT 頁面、版型與 theme geometry；需放在 /reports/{job_id} 前避免路由誤吃。"""
    builder = ai_report_ppt_runner._load_builder()
    template_pages = [_ppt_page_spec_to_dict(page) for page in builder.PAGE_LAYOUT]
    theme = json.loads(ai_report_ppt_runner.THEME_PATH.read_text(encoding="utf-8"))
    pages = _expand_ppt_pages_with_active_reports(template_pages)
    return {
        "theme": theme,
        "pages": pages,
        "kinds": sorted({page["kind"] for page in pages}),
    }


@router.post("/reports")
def create_report(request: ReportRequest) -> dict[str, Any]:
    """建立報表產生工作；未知報表名或篩選欄回 422。"""
    report_names = request.report_names or list(DEFAULT_REPORT_NAMES)
    unknown = sorted(set(report_names) - set(REPORT_DEFINITIONS))
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"unknown report_names: {', '.join(unknown)}"
        )
    if request.filters:
        bad_columns = sorted(set(request.filters) - ALLOWED_FILTER_COLUMNS)
        if bad_columns:
            raise HTTPException(
                status_code=422,
                detail=f"filters use non-whitelisted columns: {', '.join(bad_columns)}",
            )
        for report_name in report_names:
            definition = REPORT_DEFINITIONS[report_name]
            invalid_for_report = sorted(
                set(request.filters) - allowed_filter_columns_for_report(definition)
            )
            if invalid_for_report:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"filters not supported by report {report_name}: "
                        f"{', '.join(invalid_for_report)}"
                    ),
                )

    payload: dict[str, Any] = {"report_names": list(report_names)}
    if request.filters is not None:
        payload["filters"] = request.filters
    if request.limit is not None:
        payload["limit"] = request.limit
    if request.patent_ids is not None:
        payload["patent_ids"] = request.patent_ids

    job = job_repository.create_job(
        "report_generate",
        payload,
        workspace_id=request.workspace_id,
        idempotency_key=request.idempotency_key,
    )
    return job_to_dict(job)


def _ppt_page_spec_to_dict(page_spec: Any) -> dict[str, Any]:
    """把 build_ppt.py 的 PageSpec 轉成前端可直接使用的 JSON 物件。"""
    return {
        "page": page_spec.page,
        "kind": page_spec.kind,
        "title": page_spec.title,
        "subtitle": page_spec.subtitle,
        "report_keys": list(page_spec.report_keys),
        "charts": list(page_spec.charts),
        "slots": list(page_spec.slots),
        "source": "template_outline",
    }


def _ppt_kind_for_report(report_type: str) -> str:
    """依報表型態選用既有 PPT 版型，不在 API 端新增座標規格。"""
    if report_type == "detail":
        return "table"
    return "chart_with_narrative"


def _expand_ppt_pages_with_active_reports(template_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保留範例 PPT 大綱，並把未覆蓋的 active reports 補在結論與附錄前。"""
    covered = {
        report_key
        for page in template_pages
        for report_key in page["report_keys"]
    }
    dynamic_pages: list[dict[str, Any]] = []
    for report_name, definition in REPORT_DEFINITIONS.items():
        if report_name in covered:
            continue
        dynamic_pages.append(
            {
                "page": 0,
                "kind": _ppt_kind_for_report(definition.report_type),
                "title": definition.label_zh,
                "subtitle": definition.label,
                "report_keys": [report_name],
                "charts": [f"{report_name}.svg"],
                "slots": [],
                "source": "report_definition",
            }
        )

    if not dynamic_pages:
        return template_pages

    # 結論、附錄或純敘事頁要靠後；新增報表頁插在這些收尾頁之前。
    tail_start = next(
        (
            index
            for index, page in enumerate(template_pages)
            if page["kind"] == "narrative_only"
            or "結論" in (page["title"] or "")
            or "附錄" in (page["title"] or "")
        ),
        len(template_pages),
    )
    pages = template_pages[:tail_start] + dynamic_pages + template_pages[tail_start:]
    for page_number, page in enumerate(pages, start=1):
        page["page"] = page_number
    return pages


@router.get("/reports/ppt-layout")
def get_ppt_layout() -> dict[str, Any]:
    """列出 PPT 預覽與產檔共用的 theme geometry、範例大綱與 active report 頁面。

    注意：本路由必須宣告在 `/reports/{job_id}` 前面，否則 FastAPI 會把
    `ppt-layout` 當成 job_id 轉 int，前端會拿到 422。
    """
    try:
        builder = ai_report_ppt_runner._load_builder()
        theme = json.loads(ai_report_ppt_runner.THEME_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ppt layout unavailable: {exc}") from exc

    template_pages = [_ppt_page_spec_to_dict(page_spec) for page_spec in builder.PAGE_LAYOUT]
    pages = _expand_ppt_pages_with_active_reports(template_pages)
    kinds: list[str] = []
    for page in pages:
        if page["kind"] not in kinds:
            kinds.append(page["kind"])
    return {"theme": theme, "pages": pages, "kinds": kinds}


@router.get("/reports/{job_id}")
def get_report(job_id: int) -> dict[str, Any]:
    """查詢報表工作的狀態與結果；非 report_generate 或不存在回 404。"""
    job = job_repository.get_job(job_id)
    if job is None or job.job_type != "report_generate":
        raise HTTPException(status_code=404, detail=f"report job {job_id} not found")
    return job_to_dict(job)
