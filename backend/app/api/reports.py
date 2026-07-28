"""報表相關 API：建立 report_generate 工作、查詢報表工作結果。

⚠ `/reports/ppt-layout` 已搬到 main.py 的 report_versions_router（2026-07-29）：
本檔曾同時存在**兩份** ppt-layout 實作（helper 三支＋路由各兩份）——FastAPI 路由
先註冊者贏、Python 函式後定義者贏，實際行為是兩份的混種，第二個端點是永遠
打不到的死碼。搬到 versions router 的理由：①那組路由本來就被搬到 app.routes
最前，天然避開 /reports/{job_id} 把 ppt-layout 吃成 int 的 422，不再靠註解提醒
宣告順序；②頁面展開需要讀該版 report_data，而版本解析（本機＋DB 補位）的
唯一實作就在 main.py。

backend 只建立工作與讀結果，實際跑報表引擎的是 worker。payload 對齊 worker
handlers.py 的 report_generate。report_names 與 filters 欄以既有報表定義的
白名單驗證，未知即 422。
"""
from __future__ import annotations

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


@router.get("/reports/{job_id}")
def get_report(job_id: int) -> dict[str, Any]:
    """查詢報表工作的狀態與結果；非 report_generate 或不存在回 404。"""
    job = job_repository.get_job(job_id)
    if job is None or job.job_type != "report_generate":
        raise HTTPException(status_code=404, detail=f"report job {job_id} not found")
    return job_to_dict(job)
