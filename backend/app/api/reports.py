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
            # 前端據此禁用選項（2026-07-29）：痛點四象限的 Y 軸是痛點嚴重度，
            # 沒有市場資料時全部 unknown，整張圖無判讀價值。
            "requires_market_data": definition.requires_market_data,
            # 版面：年度矩陣的交叉表欄多，需滿寬（stacked）；其餘左右 45/55。
            "layout": definition.layout,
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
    if request.workspace_id is not None:
        # ⚠ 必須同時進 payload：handler 讀的是 `payload.get("workspace_id")`
        # （分群 cluster_data 的範圍、version_meta 的歸屬鍵都靠它）。
        # 只給 create_job 的具名參數＝只寫進 workflow_runs.workspace_id 欄，
        # worker 完全看不到——2026-07-31 實機 #145「job succeeded 但前端整個
        # 清單空白」的根因，也是本專案第三次「欄位在、位置不對」的同型錯誤。
        payload["workspace_id"] = request.workspace_id

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


# ══ 目標驅動規劃（P2）══════════════════════════════════════════════
# 前端送 ReportBrief（最大目標＋選圖 identity），後端排 ai:report_plan job；
# ⚠ 契約驗證在 runner（planning_contracts 唯一定義處），此處只做形狀轉換。


class ReportPlanRequest(BaseModel):
    """ReportBrief 的前端形狀（選圖以 identity 字串傳，bundle 由 runner materialize）。"""

    north_star_goal: str = Field(..., min_length=1)
    audience: str = ""
    page_budget: int = Field(default=12, ge=1, le=30)
    snapshot_id: str = Field(..., min_length=1)
    selected_charts: list[str] = Field(default_factory=list)


@router.post("/workspaces/{workspace_id}/report-plan")
def create_report_plan_job(workspace_id: int, request: ReportPlanRequest) -> dict[str, Any]:
    """排一筆目標驅動規劃任務（走 Companion／headless CLI）。"""
    if not request.selected_charts:
        raise HTTPException(status_code=422, detail="至少要選一張圖表；未選圖不得自動進 PPT")
    job = job_repository.create_job(
        "ai:report_plan",
        {
            "workspace_id": workspace_id,
            "north_star_goal": request.north_star_goal,
            "audience": request.audience,
            "page_budget": request.page_budget,
            "snapshot_id": request.snapshot_id,
            "selected_charts": request.selected_charts,
        },
        workspace_id=workspace_id,
    )
    return {"job_id": job.job_id, "status": "queued"}
