"""透過已啟動的 Backend／Worker 容器驗證完整 job 流程。

本腳本不直接呼叫 worker ``run_once``。workspace 建立完成後，所有 job 都經由
Backend HTTP API 建立，等待常駐 Worker 容器自動領取並寫回 PostgreSQL。
驗收資料刻意保留，必須由使用者看過結果並明確批准後才能清除。
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys
import time
from typing import Any

import httpx
import psycopg
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env", override=False)

from backend.app.clustering.workspace_service import create_workspace, demo_patent_ids
from backend.app.clustering.artifacts import file_sha256, resolve_artifact_path
from backend.app.db.connection import get_connection_kwargs


SOURCE_FIELD = "wips_independent_claims"


def emit(step: str, payload: dict[str, Any]) -> None:
    """輸出單行 JSON 驗收節點，方便 log 與人工驗收查找。"""
    print(json.dumps({"step": step, **payload}, ensure_ascii=False, default=str), flush=True)


def create_clean_workspace(patent_count: int) -> int:
    """建立本次專用 workspace，不覆蓋既有正式或驗收分群。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    patent_ids = demo_patent_ids(patent_count)
    workspace_id = create_workspace(
        workspace_name=f"Container integration {stamp}",
        patent_ids=patent_ids,
        created_by="container-integration-acceptance",
        description="Backend container API + Worker container acceptance",
    )
    emit("workspace_created", {"workspace_id": workspace_id, "patent_count": len(patent_ids)})
    return workspace_id


def wait_for_job(
    client: httpx.Client,
    *,
    job_id: int,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    """只透過 Backend API 輪詢，等待容器 Worker 將 job 寫成終態。"""
    deadline = time.monotonic() + timeout_seconds
    last_stage: tuple[str, int] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        stage = (str(job["current_stage"]), int(job["progress_percent"]))
        if stage != last_stage:
            emit("job_progress", {"job_id": job_id, "status": job["status"], "stage": stage[0], "progress": stage[1]})
            last_stage = stage
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            if job["status"] != "succeeded":
                raise RuntimeError(f"job {job_id} ended as {job['status']}: {job}")
            return job
        time.sleep(poll_seconds)
    raise TimeoutError(f"job {job_id} did not finish within {timeout_seconds} seconds")


def verify_db_state(*, workspace_id: int, run_id: int) -> dict[str, Any]:
    """回讀 topics、assignments 與 artifact，驗證結果及路徑可攜性。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        topic_counts = conn.execute(
            """
            SELECT
                count(*) FILTER (WHERE topic_kind = 'model' AND status = 'active'),
                count(*) FILTER (WHERE topic_kind = 'other' AND status = 'active'),
                count(*) FILTER (WHERE topic_kind = 'unclassified' AND status = 'active')
            FROM derived_layer.topics
            WHERE workspace_id = %s AND source_field = %s
            """,
            (workspace_id, SOURCE_FIELD),
        ).fetchone()
        assignments = conn.execute(
            """
            SELECT count(*) FROM derived_layer.topic_assignments
            WHERE workspace_id = %s AND source_field = %s AND is_current
            """,
            (workspace_id, SOURCE_FIELD),
        ).fetchone()[0]
        artifact_row = conn.execute(
            """
            SELECT model_artifact_path, model_artifact_hash
            FROM derived_layer.topic_runs
            WHERE run_id = %s
            """,
            (run_id,),
        ).fetchone()

    artifact_key = str(artifact_row[0])
    if PurePosixPath(artifact_key).is_absolute() or PureWindowsPath(artifact_key).is_absolute():
        raise AssertionError(f"DB stored an absolute model artifact path: {artifact_key}")
    artifact_file = resolve_artifact_path(artifact_key)
    if not artifact_file.is_file():
        raise AssertionError(f"model artifact file is missing: {artifact_file}")
    expected_hash = str(artifact_row[1])
    actual_hash = file_sha256(artifact_file)
    if actual_hash != expected_hash:
        raise AssertionError(
            f"model artifact hash mismatch: expected={expected_hash}, actual={actual_hash}"
        )
    return {
        "workspace_id": workspace_id,
        "run_id": run_id,
        "active_model_topics": int(topic_counts[0]),
        "active_other_topics": int(topic_counts[1]),
        "active_unclassified_topics": int(topic_counts[2]),
        "current_assignments": int(assignments),
        "model_artifact_key": artifact_key,
        "model_artifact_file": str(artifact_file),
        "model_artifact_hash": actual_hash,
    }


def run(args: argparse.Namespace) -> None:
    """執行 report、calibrate、candidate、finalize 與 DB 回讀。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with httpx.Client(base_url=args.base_url.rstrip("/") + "/api/v1", timeout=30) as client:
        ready = client.get("/ready")
        ready.raise_for_status()
        emit("ready_before_flow", {"ready": ready.json()})
        workspace_id = create_clean_workspace(args.patent_count)

        report = client.post(
            "/reports",
            json={"report_names": ["application_trend"], "idempotency_key": f"container-report-{stamp}"},
        )
        report.raise_for_status()
        report_job = report.json()
        emit("report_job_created", {"job": report_job})
        report_done = wait_for_job(client, job_id=int(report_job["job_id"]), timeout_seconds=args.timeout_seconds, poll_seconds=args.poll_seconds)
        emit("report_job_succeeded", {"job": report_done})

        calibrate = client.post(
            f"/workspaces/{workspace_id}/clustering/calibrate",
            json={"source_field": SOURCE_FIELD, "idempotency_key": f"container-calibrate-{stamp}"},
        )
        calibrate.raise_for_status()
        calibrate_job = calibrate.json()
        emit("calibrate_job_created", {"job": calibrate_job})
        calibrate_done = wait_for_job(client, job_id=int(calibrate_job["job_id"]), timeout_seconds=args.timeout_seconds, poll_seconds=args.poll_seconds)
        emit("calibrate_job_succeeded", {"job": calibrate_done})

        run_id = int(calibrate_done["result"]["run_id"])
        candidates_response = client.get(f"/clustering/runs/{run_id}/candidates")
        candidates_response.raise_for_status()
        candidates = candidates_response.json()["candidates"]
        emit("candidates_loaded", {"run_id": run_id, "candidates": candidates})
        selected = next(item for item in candidates if item["candidate_type"] == "conservative")

        finalize = client.post(
            f"/clustering/runs/{run_id}/finalize",
            json={
                "candidate_id": selected["candidate_id"],
                "selected_by": "container-integration-acceptance",
                "idempotency_key": f"container-finalize-{stamp}",
            },
        )
        finalize.raise_for_status()
        finalize_job = finalize.json()
        emit("finalize_job_created", {"selected_candidate": selected, "job": finalize_job})
        finalize_done = wait_for_job(client, job_id=int(finalize_job["job_id"]), timeout_seconds=args.timeout_seconds, poll_seconds=args.poll_seconds)
        emit("finalize_job_succeeded", {"job": finalize_done})

    emit("db_verification", verify_db_state(workspace_id=workspace_id, run_id=run_id))


def build_parser() -> argparse.ArgumentParser:
    """建立可供本機與伺服器覆寫的驗收參數。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument("--patent-count", type=int, default=200)
    parser.add_argument("--timeout-seconds", type=float, default=900)
    parser.add_argument("--poll-seconds", type=float, default=2)
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
