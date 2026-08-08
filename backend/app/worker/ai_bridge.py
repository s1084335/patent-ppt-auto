"""Host-side AI bridge runner。

這個模組是正式的 AI CLI 橋接器入口：它不放進一般 backend/worker 容器的必要
路徑，而是跑在「有 Claude CLI / OpenCode CLI 的受控主機」上，透過同一個
workflow_runs queue claim AI 任務，執行既有 AI handler，再把結果寫回
workflow_outputs。

典型開發環境：
    uv run python -m backend.app.worker.ai_bridge run-once
    uv run python -m backend.app.worker.ai_bridge serve

正式部署時可放在同 server 或內網機器，靠 .env 的 PGHOST/PGPORT/PGDATABASE 等
變數連資料庫；不綁本機路徑、不要求 Lightning 容器能看到使用者電腦上的 CLI。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from psycopg import OperationalError

from backend.app.clustering.workspace_service import select_irrelevant_candidates
from backend.app.db import job_repository

from .job_context import JobCancelledError, JobContext
from .queue_client import WorkerQueueClient


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
# AI job 集合的唯一事實來源在 job_repository；bridge 與一般 worker 由同一份常數推導分工，
# 不再各自維護字面值（以往兩處字面值重複，新增 AI 任務時容易漏改而讓一般 worker 誤領）。
AI_JOB_TYPES: tuple[str, ...] = tuple(sorted(job_repository.AI_JOB_TYPES))
SMOKE_VERSION = "ai_bridge_db_smoke_v1"
CLI_BINARIES: dict[str, str] = {
    "claude": "claude",
    "opencode": "opencode",
}

# ── 常駐（Companion）行為參數 ───────────────────────────────
# DB 斷線退避：1、2、4…秒指數成長，最多 60 秒重試一次。斷線期間不退出進程，
# 因為使用者本機的 Companion 一旦退出就沒人領 AI job，且沒有容器平台會把它拉回來。
BASE_DB_BACKOFF_SECONDS = 1.0
MAX_DB_BACKOFF_SECONDS = 60.0
# heartbeat 判活門檻：超過這個秒數沒更新即視為 Companion 已死（預設輪詢 3 秒，
# 留足夠餘裕給單筆長時 AI job——job 執行中 serve 不會更新 heartbeat）。
HEARTBEAT_STALE_SECONDS = 900.0
# 停止訊號：POSIX 部署與前景 Ctrl+C 用得到；Windows 排程停止收不到（見下方說明）。
_SHUTDOWN_SIGNALS = ("SIGINT", "SIGTERM", "SIGBREAK")
# 停止旗標檔名（放在狀態目錄，與 heartbeat 同層）。
#
# 為什麼需要檔案而不是只靠訊號：Windows 上實測（子行程對照）確認——
#   os.kill(pid, SIGINT)  → exit 2、handler 不執行
#   os.kill(pid, SIGTERM) → exit 15、handler 不執行
#   Stop-ScheduledTask（排程器停止，即 uninstall 走的路徑）→ handler 不執行
#   只有 CREATE_NEW_PROCESS_GROUP ＋ CTRL_BREAK_EVENT → handler 才會執行
# 排程器不會建立 process group、也不送 CTRL_BREAK，而 Companion 是被排程器啟動的，
# 沒有任何一方能對它送 CTRL_BREAK。因此 graceful shutdown 只能靠 serve 自己輪詢
# 一個「有人要求停止」的旗標檔——這是唯一在所有主動停止路徑都會生效的機制，
# 且同時適用 Windows 與 POSIX、不需要額外相依。
STOP_FILE_NAME = "ai_bridge_stop"


def load_local_env() -> None:
    """載入專案 .env；正式環境可用系統 env 覆蓋，不把部署位置寫死。"""
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def default_bridge_id() -> str:
    """建立可追蹤的 bridge id，會寫進 workflow_runs 的 locked_by。"""
    return os.getenv("AI_BRIDGE_ID") or f"ai-bridge-{socket.gethostname()}-{os.getpid()}"


def default_state_dir() -> Path:
    """狀態／heartbeat 目錄：優先 AI_BRIDGE_STATE_DIR，否則專案根目錄下的 var/。

    路徑一律由環境變數或 __file__ 推導，不寫死磁碟位置——安裝腳本可把狀態與日誌
    指到使用者可寫的位置（例如 %LOCALAPPDATA%），不必假設專案目錄可寫。
    """
    override = os.getenv("AI_BRIDGE_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return PROJECT_ROOT / "var"


def default_heartbeat_path() -> Path:
    """heartbeat 檔完整路徑（狀態目錄下的 ai_bridge_heartbeat.json）。"""
    return default_state_dir() / "ai_bridge_heartbeat.json"


def default_stop_file_path() -> Path:
    """停止旗標檔完整路徑；與 heartbeat 同目錄，安裝腳本只需指定一個 StateDir。"""
    return default_state_dir() / STOP_FILE_NAME


def request_stop(path: Path | None = None) -> Path:
    """建立停止旗標檔，要求常駐 serve 做完手上的 job 後退出。

    供 uninstall 腳本與手動停止使用；回傳實際寫入的路徑便於呼叫端顯示。
    """
    target = path if path is not None else default_stop_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    return target


class ShutdownSignal:
    """graceful shutdown 旗標：只記「有人要求停止」，不中斷正在跑的 job。

    serve 在每輪迴圈開頭檢查；正在執行中的 AI job 一律讓它跑完再退出，
    避免留下 running 狀態卻無人接手的半途 job（要等 30 分鐘 stale 回收才會被救）。
    """

    def __init__(self) -> None:
        """初始化為未要求停止。"""
        self._requested = False
        self._reason: str | None = None

    def request(self, reason: str = "manual") -> None:
        """標記要求停止；重複呼叫保留第一個理由（第一個訊號才是真正原因）。"""
        if not self._requested:
            self._requested = True
            self._reason = reason
            LOGGER.info("AI bridge shutdown requested: reason=%s", reason)

    @property
    def requested(self) -> bool:
        """是否已被要求停止。"""
        return self._requested

    @property
    def reason(self) -> str | None:
        """停止原因（訊號名或 manual）。"""
        return self._reason

    def install_handlers(self) -> None:
        """把可用的停止訊號接到本旗標；非主執行緒或平台不支援時安靜略過。"""
        for name in _SHUTDOWN_SIGNALS:
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, lambda _s, _f, _n=name: self.request(_n))
            except (ValueError, OSError):  # 非主執行緒或該平台不支援
                LOGGER.debug("signal %s not installable on this platform", name)


def compute_backoff_seconds(attempt: int) -> float:
    """DB 斷線第 attempt 次重試要等幾秒（指數退避，帶上限）。"""
    if attempt < 1:
        return BASE_DB_BACKOFF_SECONDS
    return min(BASE_DB_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_DB_BACKOFF_SECONDS)


def write_heartbeat(path: Path | None, payload: dict[str, Any]) -> None:
    """把 heartbeat 落檔（先寫暫存再改名，避免讀到寫一半的內容）。

    heartbeat 刻意落**檔案**而非資料庫：Companion 最需要被觀測的故障就是「DB 連不上」，
    此時任何寫 DB 的心跳都寫不進去。落檔另有一個好處——不需要新表或新欄位、零 migration。
    """
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:  # heartbeat 失敗不得拖垮常駐本體
        LOGGER.warning("heartbeat write failed: path=%s error=%s", path, exc)


def read_heartbeat(path: Path | None = None) -> dict[str, Any]:
    """讀 heartbeat 檔並判斷 Companion 是否還活著（供 doctor／前端查詢）。

    回傳 alive 與 reason：missing（沒跑過）、stale（太久沒更新，多半已掛）、
    stopped（正常關閉）、unreadable（檔壞了）、ok。
    """
    target = path if path is not None else default_heartbeat_path()
    if not target.exists():
        return {"alive": False, "reason": "missing", "path": str(target)}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        updated_at = datetime.fromisoformat(str(data["updated_at"]))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {"alive": False, "reason": "unreadable", "path": str(target), "error": str(exc)}

    age = (datetime.now(UTC) - updated_at).total_seconds()
    if age > HEARTBEAT_STALE_SECONDS:
        reason = "stale"
    elif str(data.get("status")) == "stopped":
        reason = "stopped"
    else:
        reason = "ok"
    return {
        "alive": reason == "ok",
        "reason": reason,
        "path": str(target),
        "age_seconds": round(age, 1),
        "worker_id": data.get("worker_id"),
        "pid": data.get("pid"),
        "status": data.get("status"),
        "stats": data.get("stats"),
    }


def run_once(
    *,
    worker_id: str,
    stale_after_seconds: int,
    store: WorkerQueueClient | None = None,
) -> dict[str, Any]:
    """claim 並執行一筆 AI job；沒有 AI job 時回 idle。

    store 可注入是為了單元測試；正式執行使用 WorkerQueueClient 連資料庫。
    """
    queue = store if store is not None else WorkerQueueClient()
    stale = queue.requeue_stale_jobs(stale_after_seconds=stale_after_seconds)
    job = queue.claim_next_job(worker_id=worker_id, job_types=AI_JOB_TYPES)
    if job is None:
        return {"status": "idle", "stale": stale}
    LOGGER.info("AI job claimed: id=%s type=%s", job.job_id, job.job_type)
    return execute_ai_job(job, worker_id=worker_id, store=queue)


def _run_ai_narrative_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行 AI 敘事任務；延遲載入 handler，避免 bridge 啟動時拉進一般 worker 依賴。"""
    from .handlers import handle_ai_narrative

    return handle_ai_narrative(payload, context)


def _run_ai_topic_label_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行主題標籤／摘要任務：驅動 headless CLI 讀代表性專利文檔後命名。

    payload：workspace_id、source_field（必要）；topic_keys（可選，不給＝全部 active 主題）、
    cli_kind／model／cli_timeout_seconds（沿用 ai:narrative 的 payload 慣例）。

    🔴 keywords 不會出現在 payload 內：CLI 看得到的內容由 ai_topic_label_runner 組裝，
    只含代表性專利文檔與必要 metadata（使用者定案）。延遲載入 runner，理由同上。

    階段映射（AI 任務無內部百分比，用階段緩進）：開始 15 →（runner 內 30→85）→ 回填 90 → 100。
    """
    from . import ai_topic_label_runner

    context.heartbeat("開始 AI 主題標籤", 15)
    workspace_id = payload.get("workspace_id")
    if workspace_id is None:
        raise ValueError("ai:topic_label payload requires workspace_id")
    source_field = payload.get("source_field")
    if not source_field:
        raise ValueError("ai:topic_label payload requires source_field")

    def _progress(stage: str, percent: int) -> None:
        """把 runner 的 CLI 執行進度轉成 worker heartbeat（繁中階段文字）。"""
        context.heartbeat("CLI 主題命名執行中", percent)

    result = ai_topic_label_runner.run_topic_label(
        workspace_id=int(workspace_id),
        source_field=str(source_field),
        topic_keys=payload.get("topic_keys") or None,
        cli_kind=str(payload.get("cli_kind") or "claude"),
        model=payload.get("model") or None,
        # _cli_runner 供測試／Companion 注入假或替代執行器；正式跑真實 subprocess。
        cli_runner=payload.get("_cli_runner"),
        timeout_seconds=float(
            payload.get("cli_timeout_seconds")
            or ai_topic_label_runner.DEFAULT_CLI_TIMEOUT_SECONDS
        ),
        progress=_progress,
    )
    context.heartbeat("標籤已回存", 90)
    context.heartbeat("完成", 100)
    return result


def _run_ai_patent_note_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行文獻備註任務：驅動 headless CLI 讀專利獨立項摘要成備註後回填。

    payload：workspace_id（可選，不給＝全庫）、char_budget／limit／skip_existing（可選）、
    cli_kind／model／cli_timeout_seconds（沿用 ai:narrative 的 payload 慣例）。

    進度：runner 內部每批回報一次（5→95，帶「第 n/N 批」文字），直接轉成 heartbeat；
    1900 件會分成數十批，使用者看得到 0→100 推進，不是無限 spinner。
    """
    from . import ai_patent_note_runner

    context.heartbeat("開始產生文獻備註", 1)

    def _progress(stage: str, percent: int) -> None:
        """把 runner 的分批進度轉成 worker heartbeat（繁中階段文字直接沿用）。"""
        context.heartbeat(stage, percent)

    workspace_id = payload.get("workspace_id")
    return ai_patent_note_runner.run_patent_note(
        workspace_id=int(workspace_id) if workspace_id is not None else None,
        cli_kind=str(payload.get("cli_kind") or "claude"),
        model=payload.get("model") or None,
        # _cli_runner 供測試／Companion 注入假或替代執行器；正式跑真實 subprocess。
        cli_runner=payload.get("_cli_runner"),
        char_budget=int(
            payload.get("char_budget") or ai_patent_note_runner.DEFAULT_CHAR_BUDGET
        ),
        skip_existing=bool(payload.get("skip_existing", True)),
        limit=int(payload["limit"]) if payload.get("limit") else None,
        timeout_seconds=float(
            payload.get("cli_timeout_seconds")
            or ai_patent_note_runner.DEFAULT_CLI_TIMEOUT_SECONDS
        ),
        progress=_progress,
    )


def _run_ai_candidate_explanation_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行候選方案 AI 輔助說明：驅動 headless CLI 解釋分群候選的指標意義後回填。

    payload：run_id（必要，calibrate 完成後由 _enqueue_candidate_explanation 帶入）、
    cli_kind／model／cli_timeout_seconds（沿用 ai:narrative 的 payload 慣例）。

    ⚠ 底層取指標／寫回一律走 ai_candidate_explanation_runner 的既有 domain 函式
    （candidate_review_payload／apply_candidate_explanations，＝tools_clustering 薄包的同一份）；
    未來全線切 MCP 時只換該 runner 的注入入口，此 dispatch 不動。

    進度：runner 內部 0→100 緩進（AI 任務無內部百分比），直接轉成 heartbeat。
    """
    from . import ai_candidate_explanation_runner

    context.heartbeat("開始產生候選方案說明", 1)

    def _progress(stage: str, percent: int) -> None:
        """把 runner 的緩進進度轉成 worker heartbeat（繁中階段文字直接沿用）。"""
        context.heartbeat(stage, percent)

    run_id = payload.get("run_id")
    if run_id is None:
        raise ValueError("ai:candidate_explanation payload requires run_id")
    return ai_candidate_explanation_runner.run_candidate_explanation(
        run_id=int(run_id),
        cli_kind=str(payload.get("cli_kind") or "claude"),
        model=payload.get("model") or None,
        # _cli_runner 供測試／Companion 注入假或替代執行器；正式跑真實 subprocess。
        cli_runner=payload.get("_cli_runner"),
        timeout_seconds=float(
            payload.get("cli_timeout_seconds")
            or ai_candidate_explanation_runner.DEFAULT_CLI_TIMEOUT_SECONDS
        ),
        progress=_progress,
    )


def _run_ai_company_zh_name_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行公司中文名草稿任務：驅動 headless CLI 為待中文化的公司產市場慣用中文名草稿。

    payload：limit（可選）、cli_kind／model／cli_timeout_seconds（沿用 ai:narrative 的 payload 慣例）。
    無 workspace_id——全庫掃 needs_zh_name 的公司代碼（見 CompanyZhNameStore.fetch_pending）。

    ⚠ AI 只產草稿（review_status='ai_suggested'），不進正式顯示欄；使用者確認才走
    apply_confirmed_display_names。refresh 的 code_alias_names 只採 confirmed，草稿天然被排除。
    """
    from . import ai_company_zh_name_runner

    context.heartbeat("開始產生公司中文名草稿", 1)

    def _progress(stage: str, percent: int) -> None:
        """把 runner 的進度轉成 worker heartbeat（繁中階段文字直接沿用）。"""
        context.heartbeat(stage, percent)

    return ai_company_zh_name_runner.run_company_zh_name(
        cli_kind=str(payload.get("cli_kind") or "claude"),
        model=payload.get("model") or None,
        # _cli_runner 供測試／Companion 注入假或替代執行器；正式跑真實 subprocess。
        cli_runner=payload.get("_cli_runner"),
        limit=int(payload["limit"]) if payload.get("limit") else None,
        timeout_seconds=float(
            payload.get("cli_timeout_seconds")
            or ai_company_zh_name_runner.DEFAULT_CLI_TIMEOUT_SECONDS
        ),
        progress=_progress,
    )


# 🔴 2026-08-04：市場線整個移除（使用者定案，含資料表）。

def _run_ai_report_ppt_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行報告 PPT 產製任務：AI 產各頁確認槽文案 → 寫 approvals.json → CLI 順手呼
    deterministic 的 build_ppt.py 組 .pptx → 進 report_artifacts。

    payload：based_on_version（可選，不給＝最新 report_trial_）、workspace_id（可選，
    全庫也能產、不擋）、cli_kind／model／cli_timeout_seconds（沿 ai:narrative 慣例）。

    ⚠ 分工：AI 只產文案 slots（不碰排版、不碰數字）；組版一律 deterministic build_ppt。
    ⚠ slot 命名／組版沿用既有 build_ppt.py（PAGE_LAYOUT 唯一來源），runner 不重寫。
    進度：runner 內部 0→100 緩進，直接轉成 heartbeat。
    """
    from . import ai_report_ppt_runner

    context.heartbeat("開始產生報告 PPT", 1)

    def _progress(stage: str, percent: int) -> None:
        """把 runner 的進度轉成 worker heartbeat（繁中階段文字直接沿用）。"""
        context.heartbeat(stage, percent)

    workspace_id = payload.get("workspace_id")
    return ai_report_ppt_runner.run_report_ppt(
        based_on_version=payload.get("based_on_version") or None,
        workspace_id=int(workspace_id) if workspace_id is not None else None,
        cli_kind=str(payload.get("cli_kind") or "claude"),
        model=payload.get("model") or None,
        # _cli_runner 供測試／Companion 注入假或替代執行器；正式跑真實 subprocess。
        cli_runner=payload.get("_cli_runner"),
        timeout_seconds=float(
            payload.get("cli_timeout_seconds")
            or ai_report_ppt_runner.DEFAULT_CLI_TIMEOUT_SECONDS
        ),
        approval_overrides=payload.get("approval_overrides") or None,
        progress=_progress,
    )


# job_type → 執行函式。值存「函式名」而非函式物件，讓 execute_ai_job 在呼叫當下才解析到
# 模組屬性——測試以 mock.patch.object 換掉 _run_ai_* 時才會生效（存物件會綁死原函式）。
def _source_field_for_filter(payload: dict[str, Any]) -> str:
    """決定不相干篩選要用哪個通道的主題來挑候選。

    payload 明給就用；未給時預設技術通道——候選是「離主題中心最遠」的專利，
    技術通道的主題結構直接反映技術領域，最適合判斷「這件是不是另一個產品類別」。
    不對兩通道各跑一次：同一批專利會被判讀兩次、token 加倍，而判準（是否同一產品類別）
    本來就與通道無關。使用者若要改用功效通道，前端可帶 source_field。
    """
    from backend.app.clustering.sources import SOURCE_FIELD_TECHNICAL, get_source_spec

    source_field = str(payload.get("source_field") or SOURCE_FIELD_TECHNICAL)
    get_source_spec(source_field)  # 非法值直接 raise，不默默 fallback
    return source_field


def _run_ai_irrelevant_filter_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行不相干篩選：逐筆判讀主題內低相似度專利的文獻備註。

    2026-07-27 補接：`ai:irrelevant_filter` 一直在 AI_JOB_TYPES 白名單內（Companion 領得走），
    但 `_AI_JOB_RUNNERS` 從未註冊 → 領到就丟
    `ValueError: unsupported AI bridge job_type`。之所以拖到今天才暴露，是因為它的
    上游 `_enqueue_irrelevant_filter` 讀 summary["workspace_id"]，而 FinalizationSummary
    直到今天才補上該欄位——在此之前這個 job **從未被建立過**（DB 歷來 0 筆）。

    payload：workspace_id（必要）；cli_kind／model／cli_timeout_seconds 沿用慣例。
    候選由 runner 內部依 c-TF-IDF 最低 N 筆取得，不在此組大 payload。
    """
    from . import ai_irrelevant_filter_runner

    context.heartbeat("開始不相干篩選", 15)
    workspace_id = payload.get("workspace_id")
    if workspace_id is None:
        raise ValueError("ai:irrelevant_filter payload requires workspace_id")

    # 候選挑選（2026-07-27 補斷鏈）：原本這裡沒傳 candidates，runner 也不會自己挑，
    # 導致 cand_list 恆空 → 走「無可判讀」early return → 回報 succeeded 但什麼都沒做
    # （實機 job 96 只跑 4.6 秒、candidates:0）。
    # 依主題分組，因為 prompt 要帶 topic_label 當「這件屬不屬於這個主題」的對照
    # （2026-07-24 第 1 題定案），不同主題不能混批。
    source_field = _source_field_for_filter(payload)
    groups = select_irrelevant_candidates(
        workspace_id=int(workspace_id), source_field=source_field)
    if not groups:
        # 尚未分群或各主題都小到取不出候選：不呼叫 CLI（不空燒 token）。
        context.heartbeat("無可判讀的候選", 100)
        return {
            "workspace_id": int(workspace_id),
            "source_field": source_field,
            "candidates": 0,
            "judged": 0,
            "undecidable": 0,
            "stored": 0,
            "results": [],
            "prompt_version": ai_irrelevant_filter_runner.PROMPT_VERSION,
            "cli_kind": str(payload.get("cli_kind") or "claude"),
        }

    cli_kind = str(payload.get("cli_kind") or "claude")
    timeout_seconds = float(
        payload.get("cli_timeout_seconds")
        or ai_irrelevant_filter_runner.DEFAULT_CLI_TIMEOUT_SECONDS
    )
    total_groups = len(groups)
    merged: dict[str, Any] = {
        "workspace_id": int(workspace_id),
        "source_field": source_field,
        "candidates": 0,
        "judged": 0,
        "undecidable": 0,
        "stored": 0,
        "results": [],
        "prompt_version": ai_irrelevant_filter_runner.PROMPT_VERSION,
        "cli_kind": cli_kind,
    }
    for index, group in enumerate(groups):
        base = 15 + int(80 * (index / total_groups))

        def _progress(stage: str, percent: int, _base: int = base) -> None:
            # 各主題內部 0→100 壓縮進本主題分到的進度區段，總進度單調不倒退。
            context.heartbeat(
                f"CLI 相干性判讀中（{_base}%）", min(95, _base + percent // total_groups))

        # note 一律傳 None，由 runner 的 fetch_notes 補讀文獻備註
        # （備註為空者 runner 自行標「無法判斷」、不進 prompt）。
        summary = ai_irrelevant_filter_runner.run_irrelevant_filter(
            workspace_id=int(workspace_id),
            candidates=[(pid, None) for pid in group["patent_ids"]],
            topic_label=group.get("topic_label"),
            cli_kind=cli_kind,
            model=payload.get("model") or None,
            fetch_notes=ai_irrelevant_filter_runner.fetch_notes,
            timeout_seconds=timeout_seconds,
            progress=_progress,
        )
        for key in ("candidates", "judged", "undecidable", "stored"):
            merged[key] += int(summary.get(key) or 0)
        merged["results"].extend(summary.get("results") or [])

    context.heartbeat("不相干篩選完成", 100)
    return merged


def _run_ai_topic_backfill_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """技術通道補分建議（openspec change add-technical-channel-ai-backfill 第二段）。

    payload：workspace_id（必填）、source_field（預設技術通道）、cli_kind／model／
    cli_timeout_seconds；_cli_runner 供測試注入（收 prompt 回字串）。
    建議只落 analysis_outputs，不碰 topic_assignments（批核走 API）。
    """
    from backend.app.app_layer import topic_backfill
    from backend.app.clustering.sources import SOURCE_FIELD_TECHNICAL

    from . import ai_topic_backfill_runner

    context.heartbeat("開始補分建議", 10)
    workspace_id = payload.get("workspace_id")
    if workspace_id is None:
        raise ValueError("ai:topic_backfill payload requires workspace_id")
    source_field = str(payload.get("source_field") or SOURCE_FIELD_TECHNICAL)
    cli_kind = str(payload.get("cli_kind") or "claude")
    model = payload.get("model") or None
    timeout = float(payload.get("cli_timeout_seconds")
                    or ai_topic_backfill_runner.DEFAULT_CLI_TIMEOUT_SECONDS)

    cli = payload.get("_cli_runner")
    if cli is None:
        from .ai_narrative_runner import (
            _subprocess_cli_runner,
            build_cli_command,
            parse_cli_result,
        )

        def cli(prompt: str, *, timeout_seconds: float) -> str:
            argv = build_cli_command(cli_kind, prompt, model=model)
            # ⚠ --output-format json 的 stdout 是 envelope（type/result/…），
            # AI 內文在 "result" 欄——直接回 stdout 會讓 runner 解析到外殼
            # 而非建議 JSON（2026-08-07 真資料首跑即踩）。
            parsed = parse_cli_result(_subprocess_cli_runner(argv, timeout_seconds))
            return str(parsed.get("result") or "")

    result = ai_topic_backfill_runner.run_topic_backfill(
        workspace_id=int(workspace_id),
        source_field=source_field,
        candidate_fetcher=lambda: topic_backfill.fetch_candidates(int(workspace_id), source_field),
        topics_fetcher=lambda: topic_backfill.fetch_topics(int(workspace_id), source_field),
        cli_runner=cli,
        ai_model=model or cli_kind,
        timeout_seconds=timeout,
        progress=lambda stage, pct: context.heartbeat(stage, pct),
    )
    context.heartbeat("補分建議完成", 100)
    return result


def _run_ai_report_plan_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """目標驅動報告規劃（P2）：brief＋選圖包 → CLI → 驗證後的候選 SlidePlan。

    payload：workspace_id／snapshot_id／north_star_goal／audience／page_budget／
    selected_charts（identity 清單）。選圖包由 runner 端 materialize——CLI 只看得到
    列入 manifest 的檔案，且**沒有任何寫入工具**。
    """
    from pathlib import Path

    from backend.app.reports.chart_bundle import build_selected_bundles
    from backend.app.reports.chart_runner import DEFAULT_OUTPUT_DIR

    from . import report_planning_runner as rp

    context.heartbeat("準備選圖資料包", 10)
    snapshot_id = str(payload.get("snapshot_id") or "")
    run_dir = Path(DEFAULT_OUTPUT_DIR) / snapshot_id
    work_dir = Path(DEFAULT_OUTPUT_DIR) / snapshot_id / "_planning"
    bundles = build_selected_bundles(
        run_dir, list(payload.get("selected_charts") or []), work_dir)

    brief = {
        "north_star_goal": payload.get("north_star_goal") or "",
        "audience": payload.get("audience") or "",
        "page_budget": int(payload.get("page_budget") or 12),
        "workspace_id": payload.get("workspace_id"),
        "snapshot_id": snapshot_id,
        "selected_charts": bundles,
    }

    cli = payload.get("_cli_runner")
    if cli is None:
        from .ai_narrative_runner import (
            _subprocess_cli_runner,
            build_cli_command,
            parse_cli_result,
        )

        cli_kind = str(payload.get("cli_kind") or "claude")
        model = payload.get("model") or None

        def cli(prompt: str, *, timeout_seconds: float) -> str:
            argv = build_cli_command(cli_kind, prompt, model=model)
            parsed = parse_cli_result(_subprocess_cli_runner(argv, timeout_seconds))
            return str(parsed.get("result") or "")

    result = rp.run_report_planning(
        brief=brief, cli_runner=cli,
        progress=lambda stage, pct: context.heartbeat(stage, pct))
    context.heartbeat("規劃完成", 100)
    return result


_AI_JOB_RUNNERS: dict[str, str] = {
    "ai:narrative": "_run_ai_narrative_job",
    "ai:topic_backfill": "_run_ai_topic_backfill_job",
    "ai:report_plan": "_run_ai_report_plan_job",
    "ai:topic_label": "_run_ai_topic_label_job",
    "ai:patent_note": "_run_ai_patent_note_job",
    "ai:candidate_explanation": "_run_ai_candidate_explanation_job",
    "ai:company_zh_name": "_run_ai_company_zh_name_job",
    "ai:report_ppt": "_run_ai_report_ppt_job",
    "ai:irrelevant_filter": "_run_ai_irrelevant_filter_job",
}


class _LateBoundHandlers:
    """依 job_type 取回當下模組屬性的小查表器（保持 execute_ai_job 讀起來像 dict）。"""

    def get(self, job_type: str):
        """回傳該 job_type 的執行函式；未支援時回 None。"""
        name = _AI_JOB_RUNNERS.get(job_type)
        return globals().get(name) if name else None


_AI_JOB_HANDLERS = _LateBoundHandlers()


def execute_ai_job(job: job_repository.ProcessingJob, *, worker_id: str, store: WorkerQueueClient) -> dict[str, Any]:
    """只執行 AI bridge 支援的 job，成功、失敗、取消都回寫 workflow queue。"""
    context = JobContext(job=job, worker_id=worker_id, store=store)
    try:
        handler = _AI_JOB_HANDLERS.get(job.job_type)
        if handler is None:
            raise ValueError(f"unsupported AI bridge job_type: {job.job_type}")
        context.heartbeat("running", 1)
        result = handler(job.payload_json, context)
        store.complete_job(job_id=job.job_id, worker_id=worker_id, result_json=result)
        LOGGER.info("AI job succeeded: id=%s type=%s", job.job_id, job.job_type)
        return {"job_id": job.job_id, "status": "succeeded", "result": result}
    except JobCancelledError as exc:
        LOGGER.warning("AI job cancelled: id=%s error=%s", job.job_id, exc)
        store.cancel_job(job_id=job.job_id, worker_id=worker_id, error_message=str(exc))
        return {"job_id": job.job_id, "status": "cancelled", "error": str(exc)}
    except Exception as exc:
        LOGGER.exception("AI job failed: id=%s type=%s", job.job_id, job.job_type)
        store.fail_job(
            job_id=job.job_id,
            worker_id=worker_id,
            error_message=f"{type(exc).__name__}: {exc}",
        )
        return {"job_id": job.job_id, "status": "failed", "error": str(exc)}


def _db_check() -> dict[str, Any]:
    """用唯讀列表查詢確認 workflow queue 可連線；doctor 不建立任何 job。"""
    try:
        job_repository.list_jobs(limit=1)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True}


def _cli_check(cli_kind: str) -> dict[str, Any]:
    """確認指定 headless CLI 是否存在於 PATH；不送 prompt、不消耗 LLM。"""
    binary = CLI_BINARIES.get(cli_kind)
    if binary is None:
        return {"ok": False, "binary": None, "error": f"unsupported cli_kind: {cli_kind}"}
    path = shutil.which(binary)
    return {"ok": path is not None, "binary": binary, "path": path}


def run_doctor(*, cli_kind: str = "claude", heartbeat_path: Path | None = None) -> dict[str, Any]:
    """正式部署前診斷 DB queue、本機 AI CLI 與常駐 heartbeat。

    heartbeat 一併回報，使用者只要跑 doctor 就知道「Companion 是否還活著」，
    不必另外去翻工作排程器或日誌。
    """
    return {
        "database": _db_check(),
        "cli": _cli_check(cli_kind),
        "heartbeat": read_heartbeat(heartbeat_path),
    }


def run_smoke(*, worker_id: str, store: WorkerQueueClient | None = None) -> dict[str, Any]:
    """執行受控 DB smoke，不呼叫外部 CLI。

    smoke 只驗 workflow_runs / workflow_outputs 的正式橋接路徑：建立專屬 AI job、
    exact-claim 該 job、heartbeat、complete。它不會 claim 其他 queued AI 任務，
    也不需要 report artifact 或 Claude CLI 登入狀態。
    """
    queue = store if store is not None else WorkerQueueClient()
    requested_at = datetime.now(UTC).isoformat()
    payload = {
        "smoke": True,
        "smoke_version": SMOKE_VERSION,
        "requested_at": requested_at,
        "requested_by": worker_id,
    }
    job = job_repository.create_job(
        "ai:narrative",
        payload,
        idempotency_key=f"ai-bridge-smoke-{requested_at}",
        max_attempts=1,
    )
    claimed = queue.claim_job_by_id(
        job_id=job.job_id,
        worker_id=worker_id,
        job_types=AI_JOB_TYPES,
    )
    if claimed is None:
        raise RuntimeError(f"AI bridge smoke job {job.job_id} was not claimable")
    queue.heartbeat(
        job_id=claimed.job_id,
        worker_id=worker_id,
        current_stage="bridge_smoke_completing",
        progress_percent=90,
    )
    result = {
        "smoke": True,
        "smoke_version": SMOKE_VERSION,
        "job_id": claimed.job_id,
        "job_type": claimed.job_type,
        "worker_id": worker_id,
        "requested_at": requested_at,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    queue.complete_job(job_id=claimed.job_id, worker_id=worker_id, result_json=result)
    return {"status": "succeeded", "job_id": claimed.job_id, "result": result}


def serve(
    *,
    worker_id: str,
    poll_seconds: float,
    stale_after_seconds: int,
    shutdown: ShutdownSignal | None = None,
    sleep=time.sleep,
    monotonic=time.monotonic,
    heartbeat_path: Path | None = None,
    stop_file: Path | None = None,
) -> dict[str, Any]:
    """常駐輪詢 AI queue（使用者本機 Patent Companion 的主迴圈）。

    正式版必要行為（缺一就會出現「沒人領 AI job」的靜默故障）：
    - graceful shutdown：停止訊號與**停止旗標檔**只在**兩輪之間**生效，
      手上的 job 一定跑完再退出。Windows 上排程器停止是 TerminateProcess、
      收不到訊號，因此旗標檔（stop_file）才是真實停止路徑（見 STOP_FILE_NAME 說明）。
    - job 失敗隔離：單筆 job 例外由 execute_ai_job 收斂；迴圈層再兜一層 try，
      任何非預期例外都只記一次錯誤並續跑，不讓常駐進程整個死掉。
    - DB 斷線重試：OperationalError 走指數退避（1→2→4…上限 60 秒）持續重試，
      恢復後退避計數歸零。
    - heartbeat：每輪落檔一次，doctor／前端可查 Companion 是否還活著。

    sleep／monotonic 可注入純為測試（不真的等待）；正式執行用 time 模組。
    回傳本次常駐的統計摘要，供結束時記錄與測試斷言。
    """
    signal_flag = shutdown if shutdown is not None else ShutdownSignal()
    started_at = datetime.now(UTC)
    stats = {
        "jobs_claimed": 0,
        "jobs_succeeded": 0,
        "jobs_failed": 0,
        "loop_errors": 0,
        "db_errors": 0,
    }
    db_failures = 0
    # 啟動時清掉上一輪殘留的旗標，否則重新登入／排程重啟後會立刻自殺，變成永遠沒人領 job。
    if stop_file is not None and stop_file.exists():
        try:
            stop_file.unlink()
        except OSError as exc:  # 清不掉就退化成「本輪不吃旗標」，不阻止啟動
            LOGGER.warning("stale stop file not removable: path=%s error=%s", stop_file, exc)
    LOGGER.info(
        "AI bridge started: worker_id=%s job_types=%s heartbeat=%s stop_file=%s",
        worker_id,
        AI_JOB_TYPES,
        heartbeat_path,
        stop_file,
    )

    def _stop_requested() -> bool:
        """兩輪之間檢查停止來源：訊號旗標或停止旗標檔（檔案先於訊號生效於 Windows）。"""
        if signal_flag.requested:
            return True
        if stop_file is not None and stop_file.exists():
            signal_flag.request("stop_file")
            return True
        return False

    def _beat(status: str) -> None:
        """更新 heartbeat 檔（含統計，便於日誌外的快速判活）。"""
        write_heartbeat(
            heartbeat_path,
            {
                "worker_id": worker_id,
                "pid": os.getpid(),
                "status": status,
                "started_at": started_at.isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "poll_seconds": poll_seconds,
                "job_types": list(AI_JOB_TYPES),
                "stats": dict(stats),
            },
        )

    while not _stop_requested():
        _beat("running")
        loop_started = monotonic()
        try:
            result = run_once(worker_id=worker_id, stale_after_seconds=stale_after_seconds)
        except OperationalError as exc:
            # DB 斷線：不退出，退避後重試；使用者本機沒有平台會把 Companion 拉回來。
            db_failures += 1
            stats["db_errors"] += 1
            wait = compute_backoff_seconds(db_failures)
            LOGGER.warning(
                "database unavailable (attempt %s), retrying in %.1fs: %s",
                db_failures,
                wait,
                exc,
            )
            _beat("db_error")
            sleep(wait)
            continue
        except Exception:
            # 單輪非預期例外（含 claim 前後的邊界錯誤）只記錄，不讓常駐進程死掉。
            stats["loop_errors"] += 1
            LOGGER.exception("AI bridge loop error; continuing")
            _beat("loop_error")
            sleep(poll_seconds)
            continue

        db_failures = 0
        status = result.get("status")
        if status == "idle":
            sleep(poll_seconds)
            continue

        stats["jobs_claimed"] += 1
        elapsed = monotonic() - loop_started
        if status == "succeeded":
            stats["jobs_succeeded"] += 1
        elif status == "failed":
            stats["jobs_failed"] += 1
        # 日誌要能看出：領到哪一筆、什麼類型、成功或失敗、整段（含 CLI 呼叫）耗時。
        LOGGER.info(
            "AI job finished: id=%s status=%s elapsed=%.1fs totals=%s",
            result.get("job_id"),
            status,
            elapsed,
            stats,
        )

    # 正常收尾就消耗掉旗標；留著會讓下次啟動誤判為「已被要求停止」。
    if stop_file is not None and stop_file.exists():
        try:
            stop_file.unlink()
        except OSError as exc:
            LOGGER.warning("stop file not removable: path=%s error=%s", stop_file, exc)

    stopped_at = datetime.now(UTC)
    summary = {
        **stats,
        "worker_id": worker_id,
        "stopped_by": signal_flag.reason,
        "started_at": started_at.isoformat(),
        "stopped_at": stopped_at.isoformat(),
    }
    _beat("stopped")
    LOGGER.info("AI bridge stopped gracefully: %s", summary)
    return summary


def configure_logging(*, level: str, log_file: str | None, max_bytes: int, backups: int) -> None:
    """設定 stdout ＋（可選）輪替檔案日誌。

    常駐服務看不到 console，日誌必須落檔才查得出為什麼掛掉；同時**一定要輪替**，
    否則長期常駐會把磁碟寫爆。用標準庫 RotatingFileHandler，不引入額外相依。
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        from logging.handlers import RotatingFileHandler

        target = Path(log_file).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                target, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
            )
        )
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


def build_parser() -> argparse.ArgumentParser:
    """建立 host-side AI bridge CLI 參數。"""
    parser = argparse.ArgumentParser(description="Run host-side patent AI bridge.")
    parser.add_argument("command", choices=("serve", "run-once", "smoke", "doctor"))
    parser.add_argument("--worker-id", default=default_bridge_id())
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--stale-after-seconds", type=int, default=1800)
    parser.add_argument("--cli-kind", choices=tuple(CLI_BINARIES), default="claude")
    parser.add_argument("--log-level", default="INFO")
    # 以下三項讓安裝腳本指定狀態／日誌落點，全部可設定，不寫死路徑。
    parser.add_argument(
        "--heartbeat-file",
        default=None,
        help="heartbeat JSON 路徑（預設 AI_BRIDGE_STATE_DIR 或專案 var/）",
    )
    parser.add_argument(
        "--stop-file",
        default=None,
        help="停止旗標檔路徑（建立此檔＝要求 serve 做完手上的 job 後退出）",
    )
    parser.add_argument(
        "--log-file", default=os.getenv("AI_BRIDGE_LOG_FILE"), help="輪替日誌檔路徑"
    )
    parser.add_argument("--log-max-bytes", type=int, default=5 * 1024 * 1024)
    parser.add_argument("--log-backups", type=int, default=5)
    return parser


def main() -> int:
    """CLI 入口：單步驗收或常駐服務。回傳行程 exit code。"""
    load_local_env()
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(
        level=args.log_level,
        log_file=args.log_file,
        max_bytes=args.log_max_bytes,
        backups=args.log_backups,
    )
    heartbeat_path = (
        Path(args.heartbeat_file).expanduser() if args.heartbeat_file else default_heartbeat_path()
    )
    if args.command == "doctor":
        result = run_doctor(cli_kind=args.cli_kind, heartbeat_path=heartbeat_path)
        LOGGER.info("ai-bridge doctor result: %s", result)
        # doctor 用 exit code 表達健康與否，讓 PowerShell／排程可直接判斷。
        #
        # heartbeat 納入判定，但要分辨兩種「不 alive」：
        #   missing（從未啟動過）、stopped（使用者自己停的）＝ 預期狀態，不算故障，
        #     否則安裝前跑 doctor 就會回 1，使用者無從分辨環境問題與尚未安裝。
        #   stale（曾啟動但心跳過期＝該活著卻死了）、unreadable（狀態檔壞掉）＝ 故障，
        #     這正是「Companion 死掉但沒人發現」的靜默故障，必須回非零。
        heartbeat = result["heartbeat"]
        companion_ok = heartbeat.get("alive") or heartbeat.get("reason") in {
            "missing",
            "stopped",
        }
        healthy = result["database"]["ok"] and result["cli"]["ok"] and companion_ok
        return 0 if healthy else 1
    if args.command == "smoke":
        result = run_smoke(worker_id=args.worker_id)
        LOGGER.info("ai-bridge smoke result: %s", result)
        return 0
    if args.command == "run-once":
        result = run_once(
            worker_id=args.worker_id,
            stale_after_seconds=args.stale_after_seconds,
        )
        LOGGER.info("ai-bridge run-once result: %s", result)
        return 0
    shutdown = ShutdownSignal()
    shutdown.install_handlers()
    stop_file = Path(args.stop_file).expanduser() if args.stop_file else default_stop_file_path()
    serve(
        worker_id=args.worker_id,
        poll_seconds=args.poll_seconds,
        stale_after_seconds=args.stale_after_seconds,
        shutdown=shutdown,
        heartbeat_path=heartbeat_path,
        stop_file=stop_file,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
