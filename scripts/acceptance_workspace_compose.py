"""Workspace compose API 與真實 PostgreSQL 的端到端驗收腳本。

腳本建立三個具重疊專利的來源 workspace，經 FastAPI TestClient 呼叫 compose API，
再直接回查資料庫驗證聯集、lineage、來源不變及未自動啟動分群。成功資料刻意保留，
供人工驗收；只有 rollback 故障注入案例必須確認沒有留下半成品。
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=False)

from fastapi.testclient import TestClient
import psycopg

from backend.app.app_layer import workspace_compose
from backend.app.clustering.workspace_service import create_workspace
from backend.app.db.connection import get_connection_kwargs
from backend.app.main import app


PREFIX = "/api/v1"
CREATED_BY = "codex-compose-acceptance"


def emit(step: str, payload: dict[str, Any]) -> None:
    """輸出單行 JSON 驗收節點，方便保留與比對結果。"""
    print(json.dumps({"step": step, **payload}, ensure_ascii=False, default=str))


def fetch_patent_ids(required: int = 6) -> list[int]:
    """取得可供驗收引用的既有專利 ID，不修改 core_layer 資料。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        rows = conn.execute(
            "SELECT id FROM core_layer.patents ORDER BY id LIMIT %s",
            (required,),
        ).fetchall()
    patent_ids = [int(row[0]) for row in rows]
    if len(patent_ids) < required:
        raise RuntimeError(f"need at least {required} patents, found {len(patent_ids)}")
    return patent_ids


def fetch_workspace_patents(workspace_id: int) -> set[int]:
    """回讀指定 workspace 的完整 patent_id 集合。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        rows = conn.execute(
            "SELECT patent_id FROM app_layer.workspace_patents WHERE workspace_id = %s",
            (workspace_id,),
        ).fetchall()
    return {int(row[0]) for row in rows}


def verify_database_state(
    *,
    workspace_id: int,
    source_ids: list[int],
    expected_source_sets: dict[int, set[int]],
    expected_union: set[int],
) -> dict[str, Any]:
    """驗證組合成員、lineage、來源完整性及分群相關資料皆符合契約。"""
    actual_union = fetch_workspace_patents(workspace_id)
    if actual_union != expected_union:
        raise AssertionError(f"union mismatch: expected={expected_union}, actual={actual_union}")

    source_unchanged = {
        source_id: fetch_workspace_patents(source_id) == expected_source_sets[source_id]
        for source_id in source_ids
    }
    if not all(source_unchanged.values()):
        raise AssertionError(f"source workspace changed: {source_unchanged}")

    with psycopg.connect(**get_connection_kwargs()) as conn:
        lineage_rows = conn.execute(
            """
            SELECT source_workspace_id, source_patent_count
            FROM app_layer.workspace_compose_sources
            WHERE workspace_id = %s
            ORDER BY source_workspace_id
            """,
            (workspace_id,),
        ).fetchall()
        run_count = int(
            conn.execute(
                "SELECT count(*) FROM derived_layer.topic_runs WHERE workspace_id = %s",
                (workspace_id,),
            ).fetchone()[0]
        )
        topic_count = int(
            conn.execute(
                "SELECT count(*) FROM derived_layer.topics WHERE workspace_id = %s",
                (workspace_id,),
            ).fetchone()[0]
        )
        assignment_count = int(
            conn.execute(
                "SELECT count(*) FROM derived_layer.topic_assignments WHERE workspace_id = %s",
                (workspace_id,),
            ).fetchone()[0]
        )
        job_count = int(
            conn.execute(
                "SELECT count(*) FROM app_layer.processing_jobs WHERE workspace_id = %s",
                (workspace_id,),
            ).fetchone()[0]
        )
        # 現行 schema 將 artifact key/hash 直接存於 topic_runs，沒有獨立 artifact table。
        artifact_count = int(
            conn.execute(
                """
                SELECT count(*)
                FROM derived_layer.topic_runs
                WHERE workspace_id = %s
                  AND model_artifact_path IS NOT NULL
                """,
                (workspace_id,),
            ).fetchone()[0]
        )

    expected_lineage = sorted(
        (source_id, len(expected_source_sets[source_id])) for source_id in source_ids
    )
    actual_lineage = [(int(row[0]), int(row[1])) for row in lineage_rows]
    if actual_lineage != expected_lineage:
        raise AssertionError(
            f"lineage mismatch: expected={expected_lineage}, actual={actual_lineage}"
        )
    if any((run_count, topic_count, assignment_count, job_count, artifact_count)):
        raise AssertionError(
            "compose unexpectedly created clustering data: "
            f"runs={run_count}, topics={topic_count}, assignments={assignment_count}, "
            f"jobs={job_count}, artifacts={artifact_count}"
        )

    return {
        "workspace_id": workspace_id,
        "union_patent_ids": sorted(actual_union),
        "lineage": [
            {"source_workspace_id": source_id, "source_patent_count": count}
            for source_id, count in actual_lineage
        ],
        "source_unchanged": source_unchanged,
        "topic_runs": run_count,
        "topics": topic_count,
        "assignments": assignment_count,
        "jobs": job_count,
        "artifacts": artifact_count,
    }


def verify_rollback(source_ids: list[int], stamp: str) -> None:
    """在 lineage 寫入點注入錯誤，確認新 workspace 與成員一起 rollback。"""
    rollback_name = f"Compose rollback acceptance {stamp}"
    with mock.patch.object(
        workspace_compose,
        "_insert_lineage",
        side_effect=RuntimeError("acceptance rollback injection"),
    ):
        try:
            workspace_compose.compose_workspaces(
                workspace_name=rollback_name,
                source_workspace_ids=source_ids,
                created_by=CREATED_BY,
                description="rollback acceptance; must not persist",
            )
        except RuntimeError as exc:
            if str(exc) != "acceptance rollback injection":
                raise
        else:
            raise AssertionError("rollback injection unexpectedly succeeded")

    with psycopg.connect(**get_connection_kwargs()) as conn:
        remaining = int(
            conn.execute(
                "SELECT count(*) FROM app_layer.workspaces WHERE workspace_name = %s",
                (rollback_name,),
            ).fetchone()[0]
        )
    if remaining != 0:
        raise AssertionError(f"rollback left {remaining} partial workspace rows")
    emit("rollback_verified", {"workspace_name": rollback_name, "remaining_rows": remaining})


def main() -> None:
    """建立並保留 compose 驗收資料，輸出可供人工核對的完整結果。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    patent_ids = fetch_patent_ids()
    source_sets = [
        {patent_ids[0], patent_ids[1], patent_ids[2], patent_ids[3]},
        {patent_ids[2], patent_ids[3], patent_ids[4]},
        {patent_ids[4], patent_ids[5]},
    ]
    source_ids: list[int] = []
    expected_source_sets: dict[int, set[int]] = {}

    for index, patent_set in enumerate(source_sets, start=1):
        source_id = create_workspace(
            workspace_name=f"Compose acceptance {stamp} source {index}",
            patent_ids=sorted(patent_set),
            created_by=CREATED_BY,
            description="workspace compose E2E acceptance source; retain until approval",
        )
        source_ids.append(source_id)
        expected_source_sets[source_id] = patent_set
        emit(
            "source_created",
            {
                "workspace_id": source_id,
                "source_index": index,
                "patent_ids": sorted(patent_set),
            },
        )

    client = TestClient(app)
    response = client.post(
        f"{PREFIX}/workspaces/compose",
        json={
            "workspace_name": f"Compose acceptance {stamp} result",
            "source_workspace_ids": source_ids,
            "created_by": CREATED_BY,
            "description": "workspace compose E2E acceptance result; retain until approval",
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"compose API failed: status={response.status_code}, body={response.text}")

    body = response.json()
    expected_union = set().union(*source_sets)
    expected_duplicate_count = sum(len(item) for item in source_sets) - len(expected_union)
    if int(body["union_count"]) != len(expected_union):
        raise AssertionError(f"unexpected union_count: {body}")
    if int(body["duplicate_count"]) != expected_duplicate_count:
        raise AssertionError(f"unexpected duplicate_count: {body}")
    expected_counts = {source_id: len(expected_source_sets[source_id]) for source_id in source_ids}
    actual_counts = {
        int(item["source_workspace_id"]): int(item["patent_count"])
        for item in body["source_counts"]
    }
    if actual_counts != expected_counts:
        raise AssertionError(f"unexpected source_counts: {body}")
    emit("compose_api_succeeded", {"response": body})

    database_state = verify_database_state(
        workspace_id=int(body["workspace_id"]),
        source_ids=source_ids,
        expected_source_sets=expected_source_sets,
        expected_union=expected_union,
    )
    emit("database_verified", database_state)

    verify_rollback(source_ids[:2], stamp)
    emit(
        "acceptance_complete",
        {
            "status": "passed",
            "retained_source_workspace_ids": source_ids,
            "retained_composed_workspace_id": int(body["workspace_id"]),
            "cleanup_performed": False,
        },
    )


if __name__ == "__main__":
    main()
