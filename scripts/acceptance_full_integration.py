"""後端 API 與 Python worker 的完整串接驗收腳本。

流程會建立一個新的乾淨 workspace，透過 FastAPI TestClient 建立 job，
再呼叫 worker run_once 消化 job，最後回讀 API 與 DB 狀態供人工驗收。
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient
import psycopg

from backend.app.clustering.workspace_service import create_workspace, demo_patent_ids
from backend.app.db.connection import get_connection_kwargs
from backend.app.main import app
from backend.app.worker.runner import run_once


PREFIX = "/api/v1"
SOURCE_FIELD = "wips_independent_claims"


def emit(step: str, payload: dict[str, Any]) -> None:
    """用一行 JSON 輸出驗收節點，方便直接貼回或搜尋 log。"""
    print(json.dumps({"step": step, **payload}, ensure_ascii=False, default=str))


def run_worker_until_job_done(
    *,
    client: TestClient,
    worker_id: str,
    job_id: int,
    max_runs: int = 10,
) -> dict[str, Any]:
    """反覆呼叫 run_once，直到指定 job 進入 terminal status。"""
    for index in range(max_runs):
        worker_result = run_once(worker_id=worker_id, stale_after_seconds=1800)
        emit(
            "worker_run_once",
            {"target_job_id": job_id, "attempt": index + 1, "result": worker_result},
        )
        job = client.get(f"{PREFIX}/jobs/{job_id}").json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
    raise RuntimeError(f"job {job_id} did not finish after {max_runs} worker runs")


def create_clean_workspace() -> int:
    """建立獨立驗收用 workspace，不沿用舊 workspace 避免 finalize 被既有 topic 擋下。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    patent_ids = demo_patent_ids(200)
    workspace_id = create_workspace(
        workspace_name=f"Codex full integration {stamp}",
        patent_ids=patent_ids,
        created_by="codex-full-integration",
        description="backend API + worker full integration acceptance",
    )
    emit(
        "workspace_created",
        {"workspace_id": workspace_id, "patent_count": len(patent_ids), "stamp": stamp},
    )
    return workspace_id


def verify_db_state(*, workspace_id: int, run_id: int) -> dict[str, Any]:
    """查核 finalize 後 topic 與 assignment 是否真的落到資料庫。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        topic_counts = conn.execute(
            """
            SELECT
                count(*) FILTER (WHERE topic_kind = 'model' AND status = 'active')
                    AS active_model_topics,
                count(*) FILTER (WHERE topic_kind = 'other' AND status = 'active')
                    AS active_other_topics,
                count(*) FILTER (WHERE topic_kind = 'unclassified' AND status = 'active')
                    AS active_unclassified_topics
            FROM derived_layer.topics
            WHERE workspace_id = %s AND source_field = %s
            """,
            (workspace_id, SOURCE_FIELD),
        ).fetchone()
        assignment_count = conn.execute(
            """
            SELECT count(*)
            FROM derived_layer.topic_assignments
            WHERE workspace_id = %s AND source_field = %s AND is_current
            """,
            (workspace_id, SOURCE_FIELD),
        ).fetchone()[0]
    return {
        "workspace_id": workspace_id,
        "run_id": run_id,
        "active_model_topics": int(topic_counts[0]),
        "active_other_topics": int(topic_counts[1]),
        "active_unclassified_topics": int(topic_counts[2]),
        "current_assignments": int(assignment_count),
    }


def main() -> None:
    """執行完整串接驗收流程。"""
    client = TestClient(app)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    worker_id = f"codex-full-integration-{stamp}"
    workspace_id = create_clean_workspace()

    report_job = client.post(
        f"{PREFIX}/reports",
        json={
            "report_names": ["application_trend"],
            "idempotency_key": f"codex-report-{stamp}",
        },
    ).json()
    emit("report_job_created", {"job": report_job})
    report_done = run_worker_until_job_done(
        client=client, worker_id=worker_id, job_id=int(report_job["job_id"])
    )
    emit("report_job_after_worker", {"job": report_done})

    calibrate_job = client.post(
        f"{PREFIX}/workspaces/{workspace_id}/clustering/calibrate",
        json={
            "source_field": SOURCE_FIELD,
            "idempotency_key": f"codex-calibrate-{stamp}",
        },
    ).json()
    emit("calibrate_job_created", {"job": calibrate_job})
    calibrate_done = run_worker_until_job_done(
        client=client, worker_id=worker_id, job_id=int(calibrate_job["job_id"])
    )
    emit("calibrate_job_after_worker", {"job": calibrate_done})

    run_id = int(calibrate_done["result"]["run_id"])
    candidates = client.get(f"{PREFIX}/clustering/runs/{run_id}/candidates").json()
    emit("candidates_after_calibrate", {"run_id": run_id, "candidates": candidates["candidates"]})
    selected = sorted(
        candidates["candidates"],
        key=lambda item: (item["candidate_type"] != "conservative", -item["score"]),
    )[0]

    finalize_job = client.post(
        f"{PREFIX}/clustering/runs/{run_id}/finalize",
        json={
            "candidate_id": selected["candidate_id"],
            "selected_by": "codex-full-integration",
            "idempotency_key": f"codex-finalize-{stamp}",
        },
    ).json()
    emit("finalize_job_created", {"selected_candidate": selected, "job": finalize_job})
    finalize_done = run_worker_until_job_done(
        client=client, worker_id=worker_id, job_id=int(finalize_job["job_id"])
    )
    emit("finalize_job_after_worker", {"job": finalize_done})

    ready = client.get(f"{PREFIX}/ready").json()
    emit("ready_after_flow", {"ready": ready})
    emit("db_verification", verify_db_state(workspace_id=workspace_id, run_id=run_id))


if __name__ == "__main__":
    main()
