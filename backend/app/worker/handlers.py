"""worker job_type 到實際業務模組的派發層。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
import logging
from pathlib import Path
import tempfile
from typing import Any, Callable

import psycopg

from backend.app.clustering.runner import calibrate_top_level, finalize_top_level
from backend.app.clustering.sources import SOURCE_FIELD_TECHNICAL, get_source_spec, source_fields
from backend.app.clustering.workspace_service import (
    incremental_workspace,
    merge_workspace_topics,
    unmerge_workspace_topics,
)
from backend.app.db import import_blob_store, report_artifact_store
from backend.app.db.connection import get_connection_kwargs
from backend.app.importers.import_paths import WEB_IMPORT_SUFFIXES
from backend.app.importers.wips_importer import import_wips_file
from backend.app.reports.chart_runner import run_chart_trial
from backend.app.reports.report_definitions import DEFAULT_REPORT_NAMES

from . import ai_narrative_runner
from .job_context import JobContext


LOGGER = logging.getLogger(__name__)

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
    """對 workspace 執行第一層候選分群；只產生候選，不會自動 finalize。

    階段文字採繁中可讀（2026-07-23 需求）：匯入後自動分群期間，使用者要能從任務列表看出
    系統正在做什麼，而不是只看到「尚未分群」而誤以為壞掉。
    """
    source_field = _source_field(payload.get("source_field"), default=SOURCE_FIELD_TECHNICAL)
    channel = get_source_spec(source_field).label_zh
    context.heartbeat(f"開始{channel}分群", 5)
    workspace_id = payload.get("workspace_id")
    if workspace_id is None:
        raise ValueError("clustering_calibrate requires workspace_id")
    # 分群掃 k 與 coherence 可能很久；背景 keeper 只補 heartbeat，不改模型結果。
    with context.keepalive(f"{channel}分群計算中（掃描主題數）", 20,
                           interval_seconds=_heartbeat_interval(payload)):
        summary = calibrate_top_level(
            workspace_id=int(workspace_id),
            source_field=source_field,
            batch_size=int(payload.get("batch_size") or 8),
            kmeans_batch_size=int(payload.get("kmeans_batch_size") or 128),
            # 分群 job 本身就是一筆 workflow_runs（job_id＝run_id），直接當 topic_runs.workflow_run_id
            workflow_run_id=context.job.job_id,
        )
    context.heartbeat(f"{channel}分群候選已產生", 100)
    # 候選產出後自動觸發「候選方案 AI 輔助說明」（讀法一，2026-07-24）：
    # AI 只解釋三組候選的指標取捨，輔助使用者判斷。失敗不得擋候選挑選（見 _enqueue_candidate_explanation）。
    _enqueue_candidate_explanation(summary)
    return _json_safe(summary)


def _summary_field(summary: Any, name: str) -> Any:
    """從 calibrate 回傳讀欄位：CalibrationSummary（dataclass）走屬性，dict（測試）走鍵。"""
    if isinstance(summary, dict):
        return summary.get(name)
    return getattr(summary, name, None)


def _enqueue_candidate_explanation(summary: Any) -> None:
    """calibrate 完成、候選產出後 enqueue 一筆 ai:candidate_explanation（走 Companion）。

    ⚠ 失敗隔離（沿既有 enqueue 失敗隔離模式）：分群本體已成功落庫，這裡的 AI 說明只是輔助；
    任何例外都只記 log、不 raise——AI 失敗時候選卡片照常顯示只是沒說明，絕不擋挑選。
    沒有候選（k 掃描無結果）就不 enqueue，沒東西可解釋。
    """
    from backend.app.db import job_repository as jr

    try:
        run_id = _summary_field(summary, "run_id")
        candidates = _summary_field(summary, "candidates") or []
        if run_id is None or not candidates:
            return
        jr.create_job(
            "ai:candidate_explanation",
            {"run_id": int(run_id)},
            workspace_id=_summary_field(summary, "workspace_id"),
        )
    except Exception:  # noqa: BLE001 - AI 說明是輔助，缺了照樣能挑候選
        LOGGER.exception("candidate explanation enqueue failed after calibrate")


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
    # finalize 完成後自動接續兩件事，皆為輔助、皆失敗隔離（只記 log、不 raise）：
    # - 不相干篩選（2026-07-24 第 2 題定案）
    # - AI 主題命名（2026-07-27 定案；分類區先顯示 fallback 標籤，命名完成後自動更新）
    _enqueue_irrelevant_filter(summary)
    _enqueue_topic_label(summary)
    context.heartbeat("clustering_finalize_completed", 95)
    return _json_safe(summary)


def _enqueue_irrelevant_filter(summary: Any) -> None:
    """分群 finalize 完成後 enqueue 一筆 ai:irrelevant_filter（走 Companion）。

    2026-07-24 定案（第 2 題）：分群完成自動接續——不需使用者手動點篩選。runner 內部會取
    每主題 c-TF-IDF 最低 N 筆、逐筆獨立判讀（見 ai_irrelevant_filter_runner）。

    ⚠ 失敗隔離（沿 _enqueue_candidate_explanation 模式）：分群本體已成功落庫，篩選只是輔助；
    任何例外都只記 log、不 raise——AI 失敗時分類區照常顯示、只是沒有可疑標記，絕不擋分群。
    無 workspace_id（理論上 finalize 一定有）時不 enqueue。
    """
    from backend.app.db import job_repository as jr

    try:
        workspace_id = _summary_field(summary, "workspace_id")
        if workspace_id is None:
            return
        jr.create_job(
            "ai:irrelevant_filter",
            {"workspace_id": int(workspace_id)},
            workspace_id=int(workspace_id),
        )
    except Exception:  # noqa: BLE001 - 篩選是輔助，缺了照樣有分群
        LOGGER.exception("irrelevant filter enqueue failed after finalize")


def _enqueue_topic_label(summary: Any) -> None:
    """finalize 完成後 enqueue 一筆 ai:topic_label（走 Companion 產中文主題名）。

    2026-07-27 定案：主題命名自動接續，不需使用者手動點。分類區採
    「先顯示 fallback 標籤（關鍵詞串接）、AI 命名完成後自動更新」——
    沿 2026-07-17「確定性結果先顯示、不等 AI、AI 掛掉也看得到」的定案精神，
    命名只是加值，絕不阻擋主題顯示。

    ⚠ 失敗隔離（沿 _enqueue_irrelevant_filter 模式）：任何例外只記 log、不 raise。
    ⚠ max_attempts=1：AI CLI 任務不自動重試（重跑要花 LLM 額度），與手動端點同口徑。
    雙通道各自 finalize，故技術／功效會各排一個命名 job。
    """
    from backend.app.db import job_repository as jr

    try:
        workspace_id = _summary_field(summary, "workspace_id")
        source_field = _summary_field(summary, "source_field")
        if workspace_id is None or source_field is None:
            return
        jr.create_job(
            "ai:topic_label",
            {
                "workspace_id": int(workspace_id),
                "source_field": str(source_field),
                "requested_by": "auto:finalize",
            },
            workspace_id=int(workspace_id),
            max_attempts=1,
        )
    except Exception:  # noqa: BLE001 - 命名是加值，缺了仍有 fallback 標籤
        LOGGER.exception("topic label enqueue failed after finalize")


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


def _load_report_cluster_data(workspace_id: int, source_field: str) -> dict[str, Any] | None:
    """取該 workspace／通道的分群資料供分群類圖表使用；無主題回 None。

    走既有 backend/app/reports/cluster_data_loader.py（已對齊 0021 schema，內含合併重映
    與 incremental fallback），不自寫 SQL。回傳形狀＝run_chart_trial 的 cluster_data 契約
    （topics／assignments／normalized_applicants／top_applicants_ws ＋ 算好的 topic_rows／
    opportunity_matrix／pain_point_matrix）。

    與 compute_and_save_cluster_analysis 的差異：那支會把三項分析寫進
    app_layer.analysis_outputs（需要 analysis_id）；報表 job 只是要畫圖，沒有 analysis 脈絡，
    故只載入＋計算不落庫，避免為了出圖而產生沒有歸屬的 analysis_outputs 列。
    """
    import psycopg
    from psycopg.rows import dict_row

    from backend.app.reports.cluster_analytics import (
        build_opportunity_matrix,
        build_pain_point_matrix,
        build_topic_effect_table,
    )
    from backend.app.reports.cluster_data_loader import load_cluster_workspace_data

    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row,
                         connect_timeout=15) as conn:
        cluster_data = load_cluster_workspace_data(workspace_id, source_field, conn)
    if not cluster_data["topics"]:
        # 尚未分群（或該通道無主題）：分群類圖表沒有輸入，靜默跳過。
        return None
    topic_rows = build_topic_effect_table(
        cluster_data["topics"],
        cluster_data["assignments"],
        cluster_data["normalized_applicants"],
    )
    opportunity = build_opportunity_matrix(topic_rows, cluster_data.get("top_applicants_ws", []))
    pain = build_pain_point_matrix(topic_rows, [], opportunity["patent_count_median"])
    return {
        **cluster_data,
        "topic_rows": topic_rows,
        "opportunity_matrix": opportunity,
        "pain_point_matrix": pain,
    }


def _resolve_report_cluster_data(payload: dict[str, Any], context: JobContext) -> dict[str, Any] | None:
    """解析報表 job 要用的 cluster_data；取不到一律回 None（不讓分群拖垮整張報表）。

    範圍取自 job 的 workspace_id（分群一律以 workspace 為單位）；通道由 payload.source_field
    指定，預設技術通道。沒有 workspace_id＝全庫報表，沒有分群範圍可談。
    載入失敗（無 topic run、DB 暫時不可用等）只記 log 並回 None——確定性報表本體不該
    因為分群輔助區塊而整張失敗。
    """
    workspace_id = payload.get("workspace_id") or context.job.workspace_id
    if workspace_id is None:
        return None
    source_field = _source_field(payload.get("source_field"), default=SOURCE_FIELD_TECHNICAL)
    try:
        return _load_report_cluster_data(int(workspace_id), source_field)
    except Exception:  # noqa: BLE001 - 分群區塊是輔助，缺了照樣出報表
        LOGGER.exception("report cluster_data load failed: workspace_id=%s", workspace_id)
        return None


def handle_report_generate(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """產製完整報表：跑報表引擎、渲染圖表，並把整包產物落 DB 供 backend 容器讀取。

    2026-07-23 修正：原本只呼叫 run_reports_batch（純查詢、回 rows），**不產任何檔案**，
    以致 /report-latest/content 一律 404、報表內嵌與匯出工作台沒內容、AI 解讀（需 run_dir
    實體檔）與 PPT 產生器（需 report_data.json）全部跑不了。改為呼叫 run_chart_trial，
    產出完整報表目錄（report_data.json ＋ SVG ＋ index.html ＋ artifact_manifest.json）。

    payload 語意保留：report_names（缺省＝DEFAULT_REPORT_NAMES）、filters、patent_ids
    直接轉給 run_chart_trial；limit 對應 ranking_limit（排名類報表的列數上限，引擎內
    唯一吃 limit 的地方）。

    分群類圖表（cluster_topic_table／opportunity_quadrant／pain_point_quadrant）需要
    cluster_data，由 _resolve_report_cluster_data 依 job 的 workspace_id 從 0021 schema
    取；尚未分群或載入失敗時傳 None，run_chart_trial 靜默跳過該區塊，不影響其餘報表。

    跨容器（2026-07-23 定案，同 import_blobs 的成因）：Railway 上 worker 與 backend 是
    不同容器、檔案系統不共享，worker 寫的目錄 backend 讀不到，故產完即整包上傳
    app_layer.report_artifacts；讀取端（/report-latest/content 等）本機找不到就改讀 DB。
    """
    context.heartbeat("開始產製報表", 5)
    report_names_payload = payload.get("report_names")
    if report_names_payload is None or report_names_payload == []:
        report_names = list(DEFAULT_REPORT_NAMES)
    elif isinstance(report_names_payload, list):
        report_names = report_names_payload
    else:
        raise ValueError("report_generate report_names must be a list when provided")

    context.heartbeat("載入分群資料", 15)
    cluster_data = _resolve_report_cluster_data(payload, context)

    limit = payload.get("limit")
    chart_kwargs: dict[str, Any] = {
        "report_names": [str(name) for name in report_names],
        "filters": payload.get("filters"),
        "patent_ids": payload.get("patent_ids"),
        "cluster_data": cluster_data,
    }
    if limit is not None:
        # payload.limit＝報表列數上限；引擎內唯一吃 limit 的是排名類報表的 ranking_limit。
        chart_kwargs["ranking_limit"] = int(limit)

    # 14 報表 ＋ 約 20 張圖，查詢與渲染都可能久，背景 keeper 只補 heartbeat。
    with context.keepalive("查詢資料並渲染圖表", 30,
                           interval_seconds=_heartbeat_interval(payload)):
        result = run_chart_trial(**chart_kwargs)

    context.heartbeat("保存報表產物", 85)
    run_dir = Path(result["output_dir"])
    result["artifacts_uploaded"] = report_artifact_store.upload_run_dir(run_dir)
    result["has_cluster_analytics"] = cluster_data is not None
    # 報表產完自動接 AI 解讀（2026-07-24 定案）：綁定該次報表版本 enqueue ai:narrative，
    # 由既有 handle_ai_narrative／ai_narrative_runner 消費（不另寫解讀邏輯）。解讀為非同步後續
    # job，報表本身已產完並上傳，不等解讀；enqueue 失敗只記 log、不擋報表（失敗隔離）。
    _enqueue_report_narrative(result, context)
    context.heartbeat("報表產製完成", 100)
    return _json_safe(result)


def _enqueue_report_narrative(result: dict[str, Any], context: JobContext) -> None:
    """報表產物落庫後 enqueue 一筆 ai:narrative，based_on_version 綁定該次報表版本。

    ⚠ 失敗隔離（沿 _enqueue_post_import_jobs／_enqueue_candidate_explanation 模式）：報表本體已
    產完並上傳，這裡的解讀只是後續補充；任何例外都只記 log、不 raise——AI 掛掉時報表照常看得到，
    使用者可重新觸發解讀。based_on_version＝run_chart_trial 回傳的 version（＝報表版本目錄名），
    對齊 ai_narrative_runner.resolve_run_dir 的版本識別（report_narrative_v1 契約）。缺 version
    （理論上不會，run_chart_trial 一律回）時不 enqueue，沒有可綁定的版本。
    """
    from backend.app.db import job_repository as jr

    try:
        version = result.get("version")
        if not version:
            return
        jr.create_job(
            "ai:narrative",
            {"based_on_version": version},
            workspace_id=context.job.workspace_id,
        )
        result["narrative_job_enqueued"] = True
    except Exception:  # noqa: BLE001 - 解讀是輔助，缺了報表照常顯示
        LOGGER.exception("report narrative enqueue failed after report_generate")
        result["narrative_job_enqueued"] = False


def handle_patent_import(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """匯入上傳的 WIPS 來源檔；複用 import_wips_file()，不重寫 mapping/去重。

    取檔改走 DB（2026-07-23 定案）：Railway 上 backend 與 worker 是**不同容器**、檔案系統
    不共享，原本 payload.path 指向的是 backend 容器的檔案，worker 一律找不到。改由 backend
    把內容存進 app_layer.import_blobs，worker 依 payload.blob_id 取回、落自己的暫存檔後餵給
    既有 import_wips_file(path)（importer 介面不動），用完即刪暫存檔。

    匯入前驗證：payload 必須帶 blob_id 與 file_hash、original_filename 副檔名在 Web 白名單；
    blob 取回時重算 SHA-256 並比對 file_hash，不符即 ValueError 讓 job failed，不進 importer。
    import_wips_file 內為單一 transaction（重複檔回 skipped_duplicate_file、錯誤整批 rollback）。

    blob 清理：匯入結束（成功或重複檔）即刪 blob——內容已無保存價值，追溯靠
    raw_records.source_file_hash（0019 起 source_files 表已移除）。匯入失敗時保留 blob，
    讓 job 重試可再取同一份內容。

    匯入圈 workspace（2026-07-22 定案）：成功匯入後，依 payload 的 new_workspace_name（新建，
    成員＝這次匯入 patent_ids）或 workspace_id（既有，union 去重）圈進 workspace；purpose
    （general／case_comparison）落新 workspace settings_json。走 app_layer.workspaces 服務，
    不自寫 SQL 繞過。重複檔/dry-run 無 patent_ids，不圈 workspace。

    匯入後自動分群（2026-07-23 定案）：自動 enqueue embeddings，並為**批次 workspace 與
    全庫 workspace**各排技術／功效兩通道的分群（一次匯入最多 4 個 job，全部背景跑），
    使用者不需手動點「執行分群」。每個 workspace＋通道**首次 calibrate、之後 incremental**。
    細節見 _enqueue_post_import_jobs 與 _enqueue_workspace_clustering。
    """
    context.heartbeat("開始匯入專利資料", 5)
    blob_id = payload.get("blob_id")
    if blob_id is None:
        raise ValueError("patent_import requires payload.blob_id")
    expected_hash = str(payload.get("file_hash") or "")
    if not expected_hash:
        raise ValueError("patent_import requires payload.file_hash")
    # 副檔名決定 importer 走哪個 parser，先擋不支援格式再取內容，避免白撈一次大 blob。
    original_filename = str(payload.get("original_filename") or "")
    suffix = Path(original_filename).suffix.lower()
    if suffix not in WEB_IMPORT_SUFFIXES:
        raise ValueError(f"patent_import unsupported format for worker: {suffix or '(none)'}")

    # 暫存檔落 worker 自己的檔案系統；用 TemporaryDirectory 確保任何結束路徑都清乾淨。
    with tempfile.TemporaryDirectory(prefix="patent_import_") as temp_dir:
        # 保留原檔名：importer 依副檔名選 parser，summary["file"] 也顯示得出來源。
        path = Path(temp_dir) / (Path(original_filename).name or "import.csv")
        # 取回內容並驗 SHA-256（不符即 raise，且不留半成品檔）。
        import_blob_store.write_blob_to_path(int(blob_id), path, expected_hash=expected_hash)
        # 大檔匯入可能久，背景 keeper 只補 heartbeat，不影響匯入結果。
        with context.keepalive("解析並寫入專利資料", 20,
                               interval_seconds=_heartbeat_interval(payload)):
            summary = import_wips_file(path)

    # 匯入已結束（暫存檔隨 with 區塊移除），blob 內容不再需要；重試情境只在失敗時發生，
    # 失敗會在上面直接 raise 而不執行到這裡，故此處刪除不影響重試。
    import_blob_store.delete_blob(int(blob_id))

    if summary.get("status") != "skipped_duplicate_file":
        # 成功匯入才圈 workspace；把 workspace 結果併入 summary 供前端顯示。
        context.heartbeat("建立分析範圍（workspace）", 90)
        workspace_result = _attach_import_workspace(payload, summary.get("patent_ids") or [])
        if workspace_result is not None:
            summary["workspace_id"] = workspace_result["workspace_id"]
            summary["workspace"] = workspace_result
        # 匯入後自動接線（2026-07-23 定案「分群改為匯入後自動觸發」）：
        # 匯入完成即在背景補 embeddings 並進分群，使用者不需手動點「執行分群」。
        # 案件比對匯入沿用同一條（原本就只補 embeddings，不分群）。
        _enqueue_post_import_jobs(payload, summary)
    # 匯入本身收 100；後續 embeddings／分群各自是獨立 job，有自己的進度條。
    context.heartbeat("匯入完成，已排入向量計算與分群", 100)
    return _json_safe(summary)


def _enqueue_case_comparison_embeddings() -> "jr.ProcessingJob":
    """為匯入批 enqueue 一個 embeddings job（技術＋功效兩通道，只算缺的）。

    以 job 觸發而非匯入 job 內直接計算：避免匯入 job 載入重權重、讓 embeddings 可獨立重試。
    write_patent_embeddings 內建「只算缺的」，故通道級補算不會重算既有 embeddings。
    """
    from backend.app.db import job_repository as jr

    return jr.create_job("embeddings", {"source_fields": list(source_fields())})


# 佔用同一通道的分群 job 型別：首次 calibrate、之後 incremental，兩者都算「該通道在跑」。
_CLUSTERING_JOB_TYPES = ("clustering_calibrate", "clustering_incremental")


def _active_clustering_source_fields(workspace_id: int) -> set[str]:
    """查該 workspace 目前 queued/running 的分群 job 各自佔用的通道。

    重複觸發防護用：同一 workspace 短時間多次匯入時，已在排隊或執行中的通道不再建新 job。
    不用 request_key 冪等鍵——那是「同一請求永久只有一筆」，會讓第二批匯入永遠分不了群；
    這裡要的是「同時只有一筆在跑」，故以佇列現況判斷。走既有 list_jobs（workspace_id 過濾
    走 workflow_runs.workspace_id），不自寫 SQL。

    calibrate 與 incremental 皆納入判斷：同一通道無論在跑哪一種，都不該再疊第二個。
    """
    from backend.app.db import job_repository as jr

    active: set[str] = set()
    for status in ("queued", "running"):
        for job in jr.list_jobs(workspace_id=workspace_id, status=status, limit=200):
            if job.job_type not in _CLUSTERING_JOB_TYPES:
                continue
            field = (job.payload_json or {}).get("source_field")
            if field:
                active.add(str(field))
    return active


def _has_completed_clustering_run(*, workspace_id: int, source_field: str) -> bool:
    """該 workspace＋通道是否已有已完成的分群 run（即已有可套用的 artifact）。

    判斷依據沿用 clustering 既有的 _latest_completed_run（incremental_workspace 內部
    本來就用它取 artifact），不新增第二套判斷機制；該函式在查無 artifact 時 raise
    ValueError，這裡把它轉成布林。
    """
    from backend.app.clustering.workspace_service import _latest_completed_run

    try:
        _latest_completed_run(workspace_id=workspace_id, source_field=source_field)
    except ValueError:
        return False
    return True


def _sync_global_workspace(patent_ids: list[int]) -> int:
    """把這批匯入專利同步進全庫 workspace（不存在則建立），回全庫 workspace_id。"""
    from backend.app.app_layer import global_workspace

    return global_workspace.sync_global_workspace_patents(patent_ids)


def _enqueue_post_import_jobs(payload: dict[str, Any], summary: dict[str, Any]) -> None:
    """匯入成功後自動接上 embeddings 與兩通道分群（2026-07-23 定案），結果併入 summary。

    順序依賴：分群要讀 embedding 表，embeddings 沒算完就分群會讀到空語料。解法是
    **先 enqueue embeddings、再 enqueue 分群**——worker 單程序且 claim_next_job 以
    ORDER BY run_id 取件（FIFO），run_id 較小的 embeddings 必定先跑完才輪到分群，
    不需要另造 job 相依機制或讓分群 job 自己輪詢等待。

    ⚠ 此保證**依賴單 worker 前提**：若日後開多 worker／多 replica，embeddings 與分群
    可能被不同 worker 同時領取而重現競態，屆時要改成顯式 job 相依（如分群 job 檢查
    前置 embeddings job 已 succeeded 才執行，否則 requeue）。

    兩通道：calibrate_top_level 一次只吃一個 source_field，故技術／功效各 enqueue 一個
    clustering_calibrate（分別重試、分別顯示進度），不是一個 job 內跑兩通道。

    失敗隔離：匯入本身已成功落庫，這裡的 enqueue 只是後續便利；任何例外都只記 log
    並回填 summary.auto_jobs_error，不 raise，避免把已成功的匯入 job 標成 failed。
    """
    patent_ids = summary.get("patent_ids") or []
    if not patent_ids:
        # 重複檔／dry-run 沒有新專利，不必補算也不必同步。
        return
    workspace_id = summary.get("workspace_id")
    # 分群全手動（2026-07-26 定案，推翻 07-23「匯入後自動分群」）：匯入完成不再自動
    # enqueue embeddings 與分群，改由使用者按「分類」鈕觸發（走 clustering/auto 端點，
    # 內部 embeddings→分群、判斷首次/增量）。此處只保留兩件與分群無關的必要接線：
    #   1. 全庫 workspace 成員同步：每批匯入的專利仍要 union 進全庫（專利總覽跨庫顯示的依據），
    #      這是資料歸屬、非分群，不隨「分群改手動」而停。
    #   2. 文獻備註 AI：獨立於分群，維持自動（只補無備註的新專利，見 ai_patent_note_runner）。
    try:
        global_workspace_id = _sync_global_workspace([int(pid) for pid in patent_ids])
        summary["global_workspace_id"] = global_workspace_id
    except Exception as exc:  # noqa: BLE001
        # 不 raise：匯入已成功，全庫同步失敗可由下次匯入補上。
        LOGGER.exception("global workspace sync failed after import: workspace_id=%s", workspace_id)
        summary["auto_jobs_error"] = f"{type(exc).__name__}: {exc}"
    # 刷新公司名收斂顯示（report_patent_base）：獨立 try/except，與分群無關但屬顯示必要一步。
    # 不 refresh 則申請人／專利權人／受讓人欄全空、以申請人過濾的搜尋也搜不到（2026-07-26 修斷點）。
    _enqueue_refresh_derived(summary)
    # 文獻備註 AI 任務獨立 try/except：與分群互不牽連，任一失敗不影響其餘。
    _enqueue_patent_note(summary)


def _enqueue_refresh_derived(summary: dict[str, Any]) -> None:
    """匯入成功後自動 enqueue 一筆 refresh_derived（刷新 report_patent_base 公司名收斂顯示）。

    ⚠ 失敗隔離（沿 _enqueue_patent_note 範本）：匯入本體已成功落庫，refresh 只是後續便利；
    任何例外只記 log 並回填 summary.refresh_derived_error，不 raise——不把已成功匯入標 failed。
    沒有新專利就不 enqueue（refresh 是全量重建，無新資料時由既有資料自然涵蓋，不必每次跑）。
    scope 'aliases'＝只刷 report_patent_base（公司名收斂那張表），成本最小。
    """
    from backend.app.db import job_repository as jr

    try:
        if not (summary.get("patent_ids") or []):
            return
        job = jr.create_job("refresh_derived", {"scope": "aliases"})
        summary["refresh_derived_job_id"] = job.job_id
    except Exception as exc:  # noqa: BLE001 - refresh 是輔助，缺了照樣完成匯入
        LOGGER.exception("refresh_derived enqueue failed after import")
        summary["refresh_derived_error"] = f"{type(exc).__name__}: {exc}"


def _enqueue_patent_note(summary: dict[str, Any]) -> None:
    """匯入成功後自動 enqueue 一筆 ai:patent_note（走 Companion 領取，headless CLI 產生備註）。

    ⚠ 失敗隔離（沿 _enqueue_candidate_explanation 範本）：匯入本體已成功落庫，文獻備註只是
    後續便利；任何例外都只記 log 並回填 summary.patent_note_error，不 raise——絕不把已成功的
    匯入 job 標成 failed。沒有新專利（重複檔／dry-run）就不 enqueue，沒東西可產生備註。

    skip_existing 沿預設 True：runner 只挑主表尚無備註者，重匯不重複燒 token。
    範圍帶批次 workspace_id（無圈 workspace 時為 None＝全庫），與其餘 post-import job 一致。
    """
    from backend.app.db import job_repository as jr

    try:
        if not (summary.get("patent_ids") or []):
            return
        workspace_id = summary.get("workspace_id")
        job = jr.create_job(
            "ai:patent_note",
            {"workspace_id": int(workspace_id)} if workspace_id is not None else {},
            workspace_id=int(workspace_id) if workspace_id is not None else None,
        )
        summary["patent_note_job_id"] = job.job_id
    except Exception as exc:  # noqa: BLE001 - 文獻備註是輔助，缺了照樣完成匯入
        LOGGER.exception("patent note enqueue failed after import")
        summary["patent_note_error"] = f"{type(exc).__name__}: {exc}"


def _enqueue_workspace_clustering(workspace_id: int, summary: dict[str, Any]) -> list[int]:
    """為 workspace 的技術／功效兩通道各 enqueue 一個分群 job，回新建的 job_id 清單。

    觸發策略（2026-07-23 定案）：**首次 calibrate、之後一律 incremental**。

    - 該 workspace＋通道**尚無**已完成 run → `clustering_calibrate`（完整分群，建立 artifact）。
    - **已有**已完成 run → `clustering_incremental`：只處理尚無 assignment 的新專利，載入既有
      artifact 做 `reducer.transform()` ＋ `partial_fit_bertopic()`，**主題結構不變**，新專利
      歸進既有主題。⚠ 這不是重跑分群——舊行為每次匯入都 calibrate，第二批匯入會把主題結構
      整個換掉，屬非預期。

    判斷逐通道獨立（技術可能已有 artifact 而功效還沒），且不同 workspace 各查各的
    （批次 workspace 與全庫是兩份 artifact），故此函式對任一 workspace_id 都成立，
    呼叫端不需傳額外旗標。已在排隊／執行中的通道一律跳過，避免重複堆疊。
    """
    from backend.app.db import job_repository as jr

    active = _active_clustering_source_fields(workspace_id)
    job_ids: list[int] = []
    for field in source_fields():
        if field in active:
            LOGGER.info(
                "skip auto clustering: workspace_id=%s source_field=%s already queued/running",
                workspace_id, field)
            continue
        job_type = (
            "clustering_incremental"
            if _has_completed_clustering_run(workspace_id=workspace_id, source_field=field)
            else "clustering_calibrate"
        )
        job = jr.create_job(
            job_type,
            {"workspace_id": workspace_id, "source_field": field},
            workspace_id=workspace_id,
        )
        job_ids.append(job.job_id)
    return job_ids


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
    """topic_code 的 active 驗證層（2026-07-21 裁決；原為 code→topic_id 解析層）。

    引擎 merge_workspace_topics 已改以 topic_keys（code 字串）為介面，故回傳的 int
    topic_id 不再是必要輸出；本函式現在的價值是**指名錯誤**——查不到、非 active 或缺
    topic_id 時可明確指出是哪個 code。呼叫端保留它作為引擎前的前置診斷。
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


def handle_refresh_derived(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """刷新 derived 層（report_patent_base）：公司名收斂顯示的必要一步。

    背景（2026-07-26 修斷點）：report_patent_base 由 refresh_report_patent_base() 全量重建，
    第一收斂順位＝「代碼→confirmed 對照名」（即使用者的代碼機制），其後是別稱／代碼統計名／
    標準化名。此表原本只有 MCP 手動觸發，匯入流程沒接，導致匯入後公司名（申請人／專利權人／
    受讓人）顯示欄全空、以申請人過濾的搜尋也搜不到。故匯入完成自動 enqueue 此 job 補上。

    payload.scope（缺省 'aliases'＝只刷 report_patent_base；'all' 另刷 family_country）。
    複用既有 refresh 函式，不重寫收斂邏輯。refresh 是全量重建，成本隨全庫筆數成長。
    """
    from backend.app.derived.refresh_report_patent_base import refresh_report_patent_base

    context.heartbeat("刷新公司名收斂顯示", 20)
    scope = str(payload.get("scope") or "aliases")
    result = refresh_report_patent_base()
    if scope == "all":
        context.heartbeat("刷新家族國別報表視圖", 60)
        from backend.app.derived.refresh_report_family_country import refresh_report_family_country
        family = refresh_report_family_country()
        result = {"report_patent_base": result, "report_family_country": family}
    context.heartbeat("完成", 100)
    return _json_safe({"scope": scope, "result": result})


def handle_embeddings(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """對指定 source_fields 補算 patent-level embeddings；複用既有 write_patent_embeddings。

    payload.source_fields（list[str]，缺省＝技術＋功效兩通道）。逐通道呼叫既有正式流程，
    該流程內建「只算缺的」（find_pending_embedding_indices：DB 已有相同 model＋text_hash 者直接
    重用，不重算），故對已算過的專利不重複進 GPU。embeddings 屬全庫通道級補算（案件比對匯入的
    新專利也在該通道表內），符合「不重算已有 embeddings」。回各通道 upsert/reuse 統計摘要。

    模型權重不存在（開發/測試機無本地 PatentSBERTa）時 write_patent_embeddings 拋
    FileNotFoundError，job 直接 failed 並保存原因，不半途落庫。

    階段文字採繁中可讀且百分比單調遞增（2026-07-23 需求）：匯入後 embeddings 可能跑數分鐘，
    這段期間分類區還沒有主題，使用者要能從任務狀態看出系統在動而不是卡住。keepalive 的
    progress 需帶該通道的實際百分比，否則背景 keeper 會把進度打回固定值而出現倒退。
    """
    from backend.app.clustering.db_writer import EmbeddingWriteConfig, write_patent_embeddings

    context.heartbeat("開始計算專利向量", 5)
    raw_fields = payload.get("source_fields")
    if raw_fields is None:
        fields = list(source_fields())
    else:
        fields = [_source_field(value) for value in raw_fields]
    if not fields:
        raise ValueError("embeddings requires at least one source_field")

    results: dict[str, Any] = {}
    total = len(fields)
    for index, field in enumerate(fields):
        context.check_cancelled()
        channel = get_source_spec(field).label_zh
        # 每個通道分到 [10, 90) 區間的一段，通道內以區段起點回報，確保跨通道不倒退。
        percent = 10 + int(80 * index / total)
        context.heartbeat(f"{channel}向量：載入模型（{index + 1}/{total} 通道）", percent)
        with context.keepalive(f"{channel}向量編碼中（{index + 1}/{total} 通道）", percent,
                               interval_seconds=LONG_TASK_HEARTBEAT_SECONDS):
            summary = write_patent_embeddings(EmbeddingWriteConfig(source_field=field))
        results[field] = {
            "source_rows": summary.source_rows,
            "reused_rows": summary.reused_rows,
            "upserted_rows": summary.upserted_rows,
            "table_rows_for_source": summary.table_rows_for_source,
        }
        context.heartbeat(
            f"{channel}向量已寫入（新增 {summary.upserted_rows} 筆）",
            10 + int(80 * (index + 1) / total))
    context.heartbeat("專利向量計算完成", 100)
    return _json_safe({"source_fields": fields, "results": results})


def handle_topic_merge(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行佇列中的主題合併：topic_code 原樣交給分群引擎（引擎本體不改）。

    request_json 形狀依 PostgresTopicRepository.queue_merge：
    {source_field, topic_keys:[a,b], label, requested_by}；workspace_id 由 run 帶。

    0021 後主題以 topic_code（字串）為唯一識別，引擎 merge_workspace_topics 的參數即
    topic_keys，故 code 原樣往下傳，不做 code→int topic_id 轉換。

    仍先跑 _resolve_active_topic_ids 當**前置診斷**：引擎的 _load_merge_topics 只會回
    籠統的「must be active topics」，查不出是哪個 code 出問題；這裡先驗一次可在 run 的
    error_message 指名該 code 與其實際 status（裁決：不猜、留明確錯誤）。解析出的
    int 僅用於驗證，不傳給引擎。
    """
    context.heartbeat("topic_merge_started", 5)
    workspace_id = context.job.workspace_id
    if workspace_id is None:
        raise ValueError("topic_merge requires workspace_id on the workflow run")
    source_field = _source_field(payload.get("source_field"))
    topic_keys = payload.get("topic_keys")
    if not isinstance(topic_keys, list) or len(topic_keys) != 2:
        raise ValueError("topic_merge requires topic_keys with exactly two topic codes")
    topic_codes = [str(key) for key in topic_keys]
    _resolve_active_topic_ids(
        workspace_id=int(workspace_id),
        source_field=source_field,
        topic_codes=topic_codes,
    )
    context.heartbeat("topic_merge_resolved", 15)
    # 合併會重載模型 artifact 並重算 assignment，時間隨語料量成長，需要保活。
    with context.keepalive("topic_merge_running", 35, interval_seconds=_heartbeat_interval(payload)):
        summary = merge_workspace_topics(
            workspace_id=int(workspace_id),
            source_field=source_field,
            topic_keys=topic_codes,
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
        # 使用者在右欄輸入框打的修改需求；2026-07-27 前 payload 有存但一路沒傳到
        # runner，打了完全沒作用（待辦 C-7b bug ②）。
        instruction=payload.get("instruction"),
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
    "embeddings": handle_embeddings,
    "refresh_derived": handle_refresh_derived,
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
