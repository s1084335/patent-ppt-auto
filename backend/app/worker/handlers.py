"""worker job_type 到實際業務模組的派發層。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from backend.app.clustering.runner import calibrate_top_level, finalize_top_level
from backend.app.clustering.sources import SOURCE_FIELD_TECHNICAL, get_source_spec
from backend.app.clustering.workspace_service import incremental_workspace
from backend.app.reports.report_engine import run_reports_batch

from .job_context import JobContext


Handler = Callable[[dict[str, Any], JobContext], dict[str, Any]]
LONG_TASK_HEARTBEAT_SECONDS = 60.0


def _source_field(value: Any, *, default: str | None = None) -> str:
    """驗證 worker payload 內的 source_field，避免 technical 這類非法別名流入模型。"""
    source_field = str(value or default or "").strip()
    if not source_field:
        raise ValueError("source_field is required")
    get_source_spec(source_field)
    return source_field


def _heartbeat_interval(payload: dict[str, Any]) -> float:
    """讀取測試或部署可覆蓋的 heartbeat 間隔，預設每 60 秒補一次。"""
    return float(payload.get("heartbeat_interval_seconds") or LONG_TASK_HEARTBEAT_SECONDS)


def _json_safe(value: Any) -> Any:
    """把 handler 回傳值轉成 JSONB 可安全序列化的基本型別。"""
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def handle_clustering_calibrate(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """對 workspace 執行第一層候選分群；只產生候選，不會自動 finalize。"""
    context.heartbeat("clustering_calibrate_started", 5)
    workspace_id = payload.get("workspace_id")
    if workspace_id is None:
        raise ValueError("clustering_calibrate requires workspace_id")
    # 分群掃 k 與 coherence 可能很久；背景 keeper 只補 heartbeat，不改模型結果。
    with context.keepalive("clustering_calibrate_running", 20, interval_seconds=_heartbeat_interval(payload)):
        summary = calibrate_top_level(
            workspace_id=int(workspace_id),
            source_field=_source_field(payload.get("source_field"), default=SOURCE_FIELD_TECHNICAL),
            batch_size=int(payload.get("batch_size") or 8),
            kmeans_batch_size=int(payload.get("kmeans_batch_size") or 128),
        )
    context.heartbeat("clustering_calibrate_completed", 95)
    return _json_safe(summary)


def handle_clustering_finalize(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """依使用者選定的 candidate 把候選分群正式寫入 topics。"""
    context.heartbeat("clustering_finalize_started", 5)
    run_id = payload.get("run_id")
    candidate_id = payload.get("candidate_id")
    if run_id is None or candidate_id is None:
        raise ValueError("clustering_finalize requires run_id and candidate_id")
    # finalize 會重跑選定 k 並寫 topics/assignments，長資料量時需持續保活。
    with context.keepalive("clustering_finalize_running", 35, interval_seconds=_heartbeat_interval(payload)):
        summary = finalize_top_level(
            run_id=int(run_id),
            candidate_id=int(candidate_id),
            selected_by=str(payload.get("selected_by") or "worker"),
            batch_size=int(payload.get("batch_size") or 8),
            kmeans_batch_size=int(payload.get("kmeans_batch_size") or 128),
        )
    context.heartbeat("clustering_finalize_completed", 95)
    return _json_safe(summary)


def handle_clustering_incremental(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """對指定 workspace/source_field 執行 incremental 分群更新。"""
    context.heartbeat("clustering_incremental_started", 5)
    workspace_id = payload.get("workspace_id")
    source_field = payload.get("source_field")
    if workspace_id is None or source_field is None:
        raise ValueError("clustering_incremental requires workspace_id and source_field")
    # incremental 也可能批次處理大量新增專利，因此與 full flow 使用同一個保活機制。
    with context.keepalive("clustering_incremental_running", 35, interval_seconds=_heartbeat_interval(payload)):
        summary = incremental_workspace(workspace_id=int(workspace_id), source_field=_source_field(source_field))
    context.heartbeat("clustering_incremental_completed", 95)
    return _json_safe(summary)


def handle_report_generate(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行報表引擎批次查詢，將報表 JSON 回寫到 processing_jobs。"""
    context.heartbeat("report_generate_started", 10)
    report_names = payload.get("report_names")
    if not isinstance(report_names, list) or not report_names:
        raise ValueError("report_generate requires non-empty report_names")
    result = run_reports_batch(
        [str(name) for name in report_names],
        filters=payload.get("filters"),
        limit=payload.get("limit"),
        patent_ids=payload.get("patent_ids"),
    )
    context.heartbeat("report_generate_completed", 95)
    return _json_safe(result)


HANDLERS: dict[str, Handler] = {
    "clustering_calibrate": handle_clustering_calibrate,
    "clustering_finalize": handle_clustering_finalize,
    "clustering_incremental": handle_clustering_incremental,
    "report_generate": handle_report_generate,
}


def dispatch_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """依 job_type 找到對應 handler 並執行。"""
    handler = HANDLERS.get(context.job.job_type)
    if handler is None:
        raise ValueError(f"unsupported job_type: {context.job.job_type}")
    context.check_cancelled()
    return handler(payload, context)
