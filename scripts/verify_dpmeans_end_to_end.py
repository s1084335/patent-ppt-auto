"""DP-Means 端到端實物驗收（tasks 3.3）：拋棄式 workspace 跑完整流程。

## 為什麼用拋棄式 workspace

2026-08-09 使用者定案。正式 workspace（如 3 號滑雪機）已有人工命名的主題，
⚠ 切換引擎後 topic_code 對不上，那些命名等於作廢——驗證階段不得碰它。

## 驗收項目

1. **artifact**：檔案存在、`algorithm='dpmeans'`、`dpmeans_state` 可還原並繼續增量
2. **run metadata**：記錄 lambda 的**值與推導方法**（CLU-008）與掃描表
3. **topics**：每個主題有關鍵詞、`label_source='fallback'`（等 AI 命名）、有代表專利
4. **可再現**：同一批資料重跑一次，λ 與群數必須一致

## 用法

    PYTHONPATH=. CLUSTERING_ALGORITHM=dpmeans \
        uv run python scripts/verify_dpmeans_end_to_end.py --source-workspace 3

⚠ 腳本會**建立並刪除**一個名為 `_dpmeans_verify` 的 workspace。它只複製來源
workspace 的專利清單，不修改來源的任何資料。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.db.connection import get_connection_kwargs

# ⚠ 必須在任何連線之前載入 .env。`get_connection_kwargs()` 沒讀到環境變數時會
# 退回 localhost:5433（本機開發預設），而正式 DSN 在 .env 裡。
# 這個載入原本藏在 runner.py 的 import side effect 中——任何「連線發生在 import
# runner 之前」的腳本都會靜默連到錯的地方，而症狀是連線逾時，看起來像 DB 掛了。
load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env", override=False)

VERIFY_WORKSPACE_NAME = "_dpmeans_verify"


def _create_workspace(source_workspace_id: int) -> int:
    """複製來源 workspace 的專利清單建立拋棄式 workspace。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT patent_ids_json FROM app_layer.workspaces WHERE workspace_id = %s",
            (source_workspace_id,),
        ).fetchone()
        if row is None:
            raise SystemExit(f"source workspace not found: {source_workspace_id}")
        created = conn.execute(
            "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json) "
            "VALUES (%s, %s) RETURNING workspace_id",
            (VERIFY_WORKSPACE_NAME, Jsonb(row["patent_ids_json"])),
        ).fetchone()
        conn.commit()
        return int(created["workspace_id"])


def _drop_workspace(workspace_id: int) -> None:
    """清掉拋棄式 workspace 與其分群產物。

    ⚠ 只刪這個 workspace 自己的資料。刪除順序由外而內，避免 FK 擋住。
    """
    with psycopg.connect(**get_connection_kwargs()) as conn:
        conn.execute(
            "DELETE FROM derived_layer.topic_assignments WHERE run_id IN ("
            "  SELECT tr.run_id FROM derived_layer.topic_runs tr"
            "  JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id"
            "  WHERE wr.workspace_id = %s)", (workspace_id,))
        conn.execute(
            "DELETE FROM derived_layer.topic_runs WHERE workflow_run_id IN ("
            "  SELECT run_id FROM app_layer.workflow_runs WHERE workspace_id = %s)",
            (workspace_id,))
        conn.execute("DELETE FROM app_layer.workflow_runs WHERE workspace_id = %s",
                     (workspace_id,))
        conn.execute("DELETE FROM app_layer.workspaces WHERE workspace_id = %s",
                     (workspace_id,))
        conn.commit()


def _run_channel(workspace_id: int, source_field: str) -> dict[str, Any]:
    """對一個通道跑 calibrate → finalize，回傳驗收所需的事實。"""
    from backend.app.clustering.artifacts import (
        ALGORITHM_DPMEANS, deserialize_dpmeans_state, load_artifact, resolve_artifact_path,
    )
    from backend.app.clustering.runner import calibrate_top_level, finalize_top_level

    calibration = calibrate_top_level(
        workspace_id=workspace_id, source_field=source_field)
    candidate_id = int(calibration.candidates[0]["candidate_id"])
    finalization = finalize_top_level(
        run_id=calibration.run_id, candidate_id=candidate_id,
        selected_by="verify:dpmeans")

    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        run = conn.execute(
            "SELECT topic_state_json, artifact_key FROM derived_layer.topic_runs "
            "WHERE run_id = %s", (finalization.run_id,)).fetchone()
    state = dict(run["topic_state_json"] or {})
    topics = state.get("topics") or []

    artifact = load_artifact(resolve_artifact_path(str(run["artifact_key"])))
    dp_state, lambda_ = deserialize_dpmeans_state(artifact.dpmeans_state)

    candidate = (state.get("candidates") or [{}])[0]
    parameters = candidate.get("parameters") or {}

    return {
        "source_field": source_field,
        "run_id": finalization.run_id,
        "topic_count": finalization.topic_count,
        "assignment_count": finalization.assignment_count,
        # 1. artifact
        "artifact_algorithm": artifact.algorithm,
        "artifact_is_dpmeans": artifact.algorithm == ALGORITHM_DPMEANS,
        "artifact_center_count": len(dp_state.centers),
        "artifact_lambda": round(lambda_, 6),
        # 2. run metadata
        "metadata_lambda": parameters.get("lambda"),
        "metadata_lambda_method": parameters.get("lambda_method"),
        "metadata_sweep_rows": len(parameters.get("lambda_sweep") or []),
        # 3. topics
        "topics_with_keywords": sum(1 for t in topics if t.get("keywords")),
        "topics_awaiting_label": sum(1 for t in topics
                                     if t.get("label_source") == "fallback"),
        "topics_with_representatives": sum(
            1 for t in topics if t.get("representative_patent_ids")),
        "topic_sizes": sorted((int(t.get("doc_count") or 0) for t in topics), reverse=True),
    }


def _check(result: dict[str, Any]) -> list[str]:
    """逐項判定，回傳未過的項目。⚠ 不做加總，任一不過即驗收失敗。"""
    failed = []
    if not result["artifact_is_dpmeans"]:
        failed.append("artifact.algorithm 不是 dpmeans")
    if result["artifact_center_count"] != result["topic_count"]:
        failed.append("artifact 中心數與主題數不一致")
    if not result["metadata_lambda"]:
        failed.append("run metadata 缺 lambda")
    if not result["metadata_lambda_method"]:
        failed.append("run metadata 缺 lambda 推導方法（CLU-008）")
    if result["topics_with_keywords"] != result["topic_count"]:
        failed.append("有主題沒有關鍵詞")
    if result["topics_awaiting_label"] != result["topic_count"]:
        failed.append("有主題的 label_source 不是 fallback")
    if result["topics_with_representatives"] != result["topic_count"]:
        failed.append("有主題沒有代表專利，AI 命名無從下手")
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-workspace", type=int, required=True)
    parser.add_argument("--keep", action="store_true",
                        help="驗收後保留 workspace（預設刪除）")
    args = parser.parse_args()

    if os.getenv("CLUSTERING_ALGORITHM") != "dpmeans":
        raise SystemExit("請設 CLUSTERING_ALGORITHM=dpmeans 再執行")

    workspace_id = _create_workspace(args.source_workspace)
    report: dict[str, Any] = {"workspace_id": workspace_id, "channels": []}
    try:
        for source_field in ("wips_independent_claims", "effect_summary"):
            result = _run_channel(workspace_id, source_field)
            result["failed"] = _check(result)
            report["channels"].append(result)
    finally:
        if not args.keep:
            _drop_workspace(workspace_id)
            report["workspace_dropped"] = True

    report["all_passed"] = all(not c["failed"] for c in report["channels"])
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
