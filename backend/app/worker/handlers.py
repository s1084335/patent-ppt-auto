"""worker job_type 到實際業務模組的派發層。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import psycopg

from backend.app.clustering.runner import calibrate_top_level, finalize_top_level
from backend.app.clustering.sources import SOURCE_FIELD_TECHNICAL, get_source_spec
from backend.app.clustering.workspace_service import (
    incremental_workspace,
    merge_workspace_topics,
    unmerge_workspace_topics,
)
from backend.app.db.connection import get_connection_kwargs
from backend.app.importers.import_paths import (
    WEB_IMPORT_SUFFIXES,
    is_within_imports_root,
    remove_import_dir,
)
from backend.app.importers.wips_importer import file_sha256, import_wips_file
from backend.app.reports.report_definitions import DEFAULT_REPORT_NAMES
from backend.app.reports.report_engine import run_reports_batch

from . import ai_narrative_runner
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
    """執行報表引擎批次查詢，報表 JSON 由 runner 經 workflow_outputs 版本化保存。"""
    context.heartbeat("report_generate_started", 10)
    report_names_payload = payload.get("report_names")
    if report_names_payload is None or report_names_payload == []:
        report_names = list(DEFAULT_REPORT_NAMES)
    elif isinstance(report_names_payload, list):
        report_names = report_names_payload
    else:
        raise ValueError("report_generate report_names must be a list when provided")
    result = run_reports_batch(
        [str(name) for name in report_names],
        filters=payload.get("filters"),
        limit=payload.get("limit"),
        patent_ids=payload.get("patent_ids"),
    )
    context.heartbeat("report_generate_completed", 95)
    return _json_safe(result)


def handle_patent_import(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """匯入上傳的 WIPS 來源檔；複用 import_wips_file()，不重寫 mapping/去重。

    匯入前重新驗證受控檔案：位於 imports root、存在且為 regular file、副檔名在 Web 白名單、
    實際 SHA-256 等於 payload.file_hash；任一失敗直接 raise 讓 job failed，不進 importer。
    import_wips_file 內為單一 transaction（重複檔回 skipped_duplicate_file、錯誤整批 rollback）。
    重複檔時安全刪除本次上傳目錄；成功匯入則保留（source_files.file_path 需追溯）。

    匯入圈 workspace（2026-07-22 定案）：成功匯入後，依 payload 的 new_workspace_name（新建，
    成員＝這次匯入 patent_ids）或 workspace_id（既有，union 去重）圈進 workspace；purpose
    （general／case_comparison）落新 workspace settings_json。走 app_layer.workspaces 服務，
    不自寫 SQL 繞過。重複檔/dry-run 無 patent_ids，不圈 workspace。
    """
    context.heartbeat("patent_import_started", 5)
    raw_path = payload.get("path")
    if not raw_path:
        raise ValueError("patent_import requires payload.path")
    path = Path(raw_path)
    if not is_within_imports_root(path):
        raise ValueError(f"patent_import path escapes imports root: {raw_path}")
    if not path.is_file():
        raise ValueError(f"patent_import file missing or not a regular file: {raw_path}")
    if path.suffix.lower() not in WEB_IMPORT_SUFFIXES:
        raise ValueError(f"patent_import unsupported format for worker: {path.suffix}")
    expected_hash = str(payload.get("file_hash") or "")
    if not expected_hash or file_sha256(path) != expected_hash:
        raise ValueError("patent_import file hash mismatch")

    # 大檔匯入可能久，背景 keeper 只補 heartbeat，不影響匯入結果。
    with context.keepalive("patent_import_running", 20, interval_seconds=_heartbeat_interval(payload)):
        summary = import_wips_file(path)
    if summary.get("status") == "skipped_duplicate_file":
        # 重複檔不需保留這份上傳副本；只刪本次上傳目錄（remove_import_dir 會確認位於 imports root）。
        remove_import_dir(path.parent)
    else:
        # 成功匯入才圈 workspace；把 workspace 結果併入 summary 供前端顯示。
        context.heartbeat("patent_import_workspace", 90)
        workspace_result = _attach_import_workspace(payload, summary.get("patent_ids") or [])
        if workspace_result is not None:
            summary["workspace_id"] = workspace_result["workspace_id"]
            summary["workspace"] = workspace_result
    context.heartbeat("patent_import_completed", 95)
    return _json_safe(summary)


def _attach_import_workspace(
    payload: dict[str, Any], patent_ids: list[int]
) -> dict[str, Any] | None:
    """依 payload 把這次匯入的 patent_ids 圈進 workspace（新建或既有 union），回結果或 None。

    new_workspace_name → 建新 workspace（成員＝patent_ids，purpose 落 settings_json）；
    workspace_id → union 去重進既有 workspace（purpose 於既有 workspace 已定，不重寫）。
    兩者皆缺 → 不圈 workspace（向後相容既有純匯入）。兩者皆給屬呼叫端錯誤，raise ValueError。
    走 app_layer.workspace_create 服務，不自寫 SQL。
    """
    from backend.app.app_layer import workspace_create

    new_name = payload.get("new_workspace_name")
    workspace_id = payload.get("workspace_id")
    if new_name and workspace_id is not None:
        raise ValueError("patent_import accepts either new_workspace_name or workspace_id, not both")

    if new_name:
        return workspace_create.create_workspace(
            workspace_name=str(new_name),
            patent_ids=patent_ids,
            purpose=payload.get("purpose"),
        )
    if workspace_id is not None:
        return workspace_create.add_patents_to_workspace(
            workspace_id=int(workspace_id),
            patent_ids=patent_ids,
        )
    return None


def _resolve_active_topic_ids(
    *, workspace_id: int, source_field: str, topic_codes: list[str]
) -> list[int]:
    """topic_code→topic_id 解析層（2026-07-21 裁決）。

    佇列 request_json 存的是 topic_code（str），引擎 merge_workspace_topics 要 int topic_ids；
    從最新「topic_state_json->'topics' 非空」的 derived_layer.topic_runs（該 workspace_id＋
    source_field，經 app_layer.workflow_runs 連 workspace）反查。incremental run 的 state 不帶
    topics，須沿 run_id 由大到小 fallback，否則會抓到空 state 而誤判 code 不存在。
    只認同時帶 topic_id 且 status='active' 的條目；任一 code 查不到、非 active 或缺 topic_id
    一律 raise ValueError（runner 會把該 run 標 failed 並保存明確錯誤），不猜測。
    """
    with psycopg.connect(**get_connection_kwargs()) as conn:
        row = conn.execute(
            """
            SELECT tr.run_id, tr.topic_state_json
            FROM derived_layer.topic_runs tr
            JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
            WHERE wr.workspace_id = %s AND tr.source_field = %s
              AND jsonb_array_length(COALESCE(tr.topic_state_json->'topics', '[]'::jsonb)) > 0
            ORDER BY tr.run_id DESC
            LIMIT 1
            """,
            (workspace_id, source_field),
        ).fetchone()
    if row is None:
        raise ValueError(
            f"topic merge/unmerge: no topic run for workspace {workspace_id} "
            f"/ {source_field}; cannot resolve topic codes {topic_codes}"
        )
    topic_run_id, state = row
    by_code = {t.get("topic_code"): t for t in (state or {}).get("topics") or []}
    resolved: list[int] = []
    for code in topic_codes:
        entry = by_code.get(code)
        if entry is None:
            raise ValueError(
                f"topic code {code!r} not found in latest topic run {topic_run_id}")
        if entry.get("status") != "active":
            raise ValueError(
                f"topic code {code!r} is not active (status={entry.get('status')!r}) "
                f"in topic run {topic_run_id}")
        if entry.get("topic_id") is None:
            raise ValueError(
                f"topic code {code!r} has no topic_id in topic run {topic_run_id}")
        resolved.append(int(entry["topic_id"]))
    return resolved


def handle_topic_merge(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行佇列中的主題合併：解析 topic_code→topic_id 後交給分群引擎（引擎本體不改）。

    request_json 形狀依 PostgresTopicRepository.queue_merge：
    {source_field, topic_keys:[a,b], label, requested_by}；workspace_id 由 run 帶。
    """
    context.heartbeat("topic_merge_started", 5)
    workspace_id = context.job.workspace_id
    if workspace_id is None:
        raise ValueError("topic_merge requires workspace_id on the workflow run")
    source_field = _source_field(payload.get("source_field"))
    topic_keys = payload.get("topic_keys")
    if not isinstance(topic_keys, list) or len(topic_keys) != 2:
        raise ValueError("topic_merge requires topic_keys with exactly two topic codes")
    topic_ids = _resolve_active_topic_ids(
        workspace_id=int(workspace_id),
        source_field=source_field,
        topic_codes=[str(key) for key in topic_keys],
    )
    context.heartbeat("topic_merge_resolved", 15)
    # 合併會重載模型 artifact 並重算 assignment，時間隨語料量成長，需要保活。
    with context.keepalive("topic_merge_running", 35, interval_seconds=_heartbeat_interval(payload)):
        summary = merge_workspace_topics(
            workspace_id=int(workspace_id),
            source_field=source_field,
            topic_ids=topic_ids,
            merged_by=str(payload.get("requested_by") or "worker"),
            label=payload.get("label"),
        )
    context.heartbeat("topic_merge_completed", 95)
    return _json_safe(summary)


def handle_topic_unmerge(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行佇列中的主題解除合併：merge_run_id 已是 int，直接交給分群引擎。

    request_json 形狀依 PostgresTopicRepository.queue_unmerge：
    {source_field, merge_run_id, requested_by}；workspace_id 由 run 帶。
    """
    context.heartbeat("topic_unmerge_started", 5)
    workspace_id = context.job.workspace_id
    if workspace_id is None:
        raise ValueError("topic_unmerge requires workspace_id on the workflow run")
    source_field = _source_field(payload.get("source_field"))
    merge_run_id = payload.get("merge_run_id")
    if merge_run_id is None:
        raise ValueError("topic_unmerge requires merge_run_id")
    # unmerge 需從基底 artifact 重播其餘 merge，與 merge 同樣屬長任務。
    with context.keepalive("topic_unmerge_running", 35, interval_seconds=_heartbeat_interval(payload)):
        summary = unmerge_workspace_topics(
            workspace_id=int(workspace_id),
            source_field=source_field,
            merge_run_id=int(merge_run_id),
            reverted_by=str(payload.get("requested_by") or "worker"),
        )
    context.heartbeat("topic_unmerge_completed", 95)
    return _json_safe(summary)


def handle_ai_narrative(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """報表解讀 AI 任務消費者（E2E 鏈：前端按 AI 解讀 → 佇列 → 本 handler → headless CLI
    → narratives.json → refresh-index → SSE 回推）。

    payload：based_on_version（要解讀的報表版本目錄名；缺省取 full_report_latest 最新
    report_trial_）、cli_kind（claude／opencode，預設 claude，介面留給 Companion 雙 CLI 對接）、
    可選 cli_timeout_seconds。

    階段映射（decisions.md「長時任務前端進度顯示」，AI 任務無內部百分比，用階段緩進）：
    ai_narrative_started 15 →（runner 內 cli_running 30，CLI 跑期間緩進到 ~85）→
    narrative_saved 90 → completed 100。CLI 呼叫由 ai_narrative_runner 抽成可注入函式，
    測試以 fake runner 取代，不真跑 CLI。
    """
    context.heartbeat("開始 AI 報表解讀", 15)
    based_on_version = payload.get("based_on_version")
    cli_kind = str(payload.get("cli_kind") or "claude")
    # model 由任務 payload 帶（如 claude-opus-4-8）；未給則用 CLI 預設模型。
    model = payload.get("model") or None
    timeout_seconds = float(
        payload.get("cli_timeout_seconds") or ai_narrative_runner.DEFAULT_CLI_TIMEOUT_SECONDS
    )

    def _progress(stage: str, percent: int) -> None:
        """把 runner 的 CLI 執行進度轉成 worker heartbeat（繁中階段文字）。"""
        context.heartbeat("CLI 解讀執行中", percent)

    summary = ai_narrative_runner.run_narrative(
        based_on_version,
        cli_kind=cli_kind,
        model=model,
        cli_runner=payload.get("_cli_runner"),
        timeout_seconds=timeout_seconds,
        progress=_progress,
    )
    context.heartbeat("解讀已回存", 90)
    result = {
        "based_on_version": summary.get("based_on_version"),
        "variants_narrated": summary.get("narrated"),
        "variants_total": summary.get("variants_total"),
        "pending": summary.get("pending", []),
        "cli_kind": summary.get("cli_kind"),
        "prompt_version": summary.get("prompt_version"),
        "narratives_path": summary.get("narratives_path"),
    }
    context.heartbeat("完成", 100)
    return _json_safe(result)


HANDLERS: dict[str, Handler] = {
    "clustering_calibrate": handle_clustering_calibrate,
    "clustering_finalize": handle_clustering_finalize,
    "clustering_incremental": handle_clustering_incremental,
    "report_generate": handle_report_generate,
    "patent_import": handle_patent_import,
    "topic_merge": handle_topic_merge,
    "topic_unmerge": handle_topic_unmerge,
    "ai:narrative": handle_ai_narrative,
}


def dispatch_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """依 job_type 找到對應 handler 並執行。"""
    handler = HANDLERS.get(context.job.job_type)
    if handler is None:
        raise ValueError(f"unsupported job_type: {context.job.job_type}")
    context.check_cancelled()
    return handler(payload, context)
