"""workspace 分群應用服務：候選、標籤、incremental、階層建議與人工合併。"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import json
import math
from typing import Any

import numpy as np
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.db.connection import get_connection_kwargs

from .artifacts import (
    WorkspaceTopicArtifact,
    artifact_key,
    artifact_path,
    load_artifact,
    resolve_artifact_path,
    save_artifact,
)
from .model import EmbeddingMatrix, ReducedEmbeddingMatrix, partial_fit_bertopic
from .runner import (
    CANDIDATE_REFERENCE_PARAMETER_KEY,
    ClusteringCorpus,
    load_clustering_corpus,
)
from .preprocessing import clean_patent_text, sha256_text
from .sources import get_source_spec, source_fields


LLM_REPRESENTATIVE_DOC_LIMIT = 15

# DB 保留較多代表專利供追蹤；正式 topic 標籤/摘要階段才給 LLM 前 5 筆全文。
TOPIC_LABELING_DOC_LIMIT = 5

# LLM 產出的建議字數（寫進 instruction）與 apply 端硬上限（2 倍建議上限）。
# 超過硬上限視為 LLM 未遵循指示，直接 raise 讓呼叫端重生，不靜默截斷。
LABEL_SUGGESTED_RANGE = "4 到 8"
SUMMARY_SUGGESTED_RANGE = "20 到 40"
EXPLANATION_SUGGESTED_RANGE = "25 到 40"
LABEL_MAX_CHARS = 16
SUMMARY_MAX_CHARS = 80
EXPLANATION_MAX_CHARS = 80


# 本路徑只允許 AI 產出（llm）與程式後備（fallback）；manual 僅能由
# 前端 rename endpoint 寫入，避免 AI 通道把標籤自我升級成人工定案。
APPLY_LABEL_SOURCES = ("llm", "fallback")


@dataclass(frozen=True)
class IncrementalSummary:
    """回報單一 workspace 通道 incremental 更新結果。"""

    run_id: int | None
    workspace_id: int
    source_field: str
    new_document_count: int
    assignment_count: int
    artifact_version: int
    pca_updated: bool
    status: str


@dataclass(frozen=True)
class MergeSummary:
    """回報人工合併後的新永久主題與新版 artifact。"""

    run_id: int
    workspace_id: int
    source_field: str
    source_topic_ids: list[int]
    merged_topic_id: int
    artifact_version: int
    status: str


@dataclass(frozen=True)
class UnmergeSummary:
    """回報依 merge run 復原後的來源主題與新版 artifact。"""

    run_id: int
    workspace_id: int
    source_field: str
    target_merge_run_id: int
    restored_topic_ids: list[int]
    reverted_topic_id: int
    artifact_version: int
    status: str


def create_workspace(
    *,
    workspace_name: str,
    patent_ids: list[int],
    created_by: str,
    description: str | None = None,
) -> int:
    """建立 workspace 並加入明確專利集合；不複製或修改核心專利值。"""
    unique_patent_ids = list(dict.fromkeys(int(value) for value in patent_ids))
    if not unique_patent_ids:
        raise ValueError("workspace requires at least one patent")
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_layer.workspaces (
                    workspace_name, description, created_by, parameters_json
                ) VALUES (%s, %s, %s, %s)
                RETURNING workspace_id
                """,
                (
                    workspace_name.strip(),
                    description,
                    created_by,
                    Jsonb({"clustering_sources": list(source_fields())}),
                ),
            )
            workspace_id = int(cur.fetchone()[0])
            cur.executemany(
                """
                INSERT INTO app_layer.workspace_patents (
                    workspace_id, patent_id, source_type, added_by
                ) VALUES (%s, %s, 'manual', %s)
                """,
                [(workspace_id, patent_id, created_by) for patent_id in unique_patent_ids],
            )
    return workspace_id


def add_workspace_patents(
    *,
    workspace_id: int,
    patent_ids: list[int],
    added_by: str,
) -> dict[str, int]:
    """把新專利加入既有 workspace，後續由雙通道 incremental API 接手。"""
    unique_patent_ids = list(dict.fromkeys(int(value) for value in patent_ids))
    if not unique_patent_ids:
        raise ValueError("at least one patent_id is required")
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM core_layer.patents WHERE id = ANY(%s)",
                (unique_patent_ids,),
            )
            existing_count = int(cur.fetchone()[0])
            if existing_count != len(unique_patent_ids):
                raise ValueError("one or more patent_ids do not exist in core_layer.patents")
            cur.executemany(
                """
                INSERT INTO app_layer.workspace_patents (
                    workspace_id, patent_id, source_type, added_by
                ) VALUES (%s, %s, 'manual', %s)
                ON CONFLICT (workspace_id, patent_id) DO NOTHING
                """,
                [(workspace_id, patent_id, added_by) for patent_id in unique_patent_ids],
            )
            cur.execute(
                "SELECT count(*) FROM app_layer.workspace_patents WHERE workspace_id = %s",
                (workspace_id,),
            )
            workspace_count = int(cur.fetchone()[0])
    return {
        "requested_count": len(unique_patent_ids),
        "workspace_patent_count": workspace_count,
    }


def demo_patent_ids(limit: int = 200) -> list[int]:
    """挑選技術、功效文本及兩種向量都齊全的專利，供臨時頁面驗證。"""
    if limit < 50:
        raise ValueError("demo workspace requires at least 50 patents")
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT p.id
                FROM core_layer.patents p
                JOIN core_layer.patent_technical_embeddings te ON te.patent_id = p.id
                JOIN core_layer.patent_effect_embeddings ee ON ee.patent_id = p.id
                WHERE NULLIF(BTRIM(p."獨立項[KR,JP,US,CN,EP,IN]"), '') IS NOT NULL
                  AND NULLIF(BTRIM(p."效果 摘要[US,EP,PCT,JP,KR,CN,TW]"), '') IS NOT NULL
                ORDER BY p.id
                LIMIT %s
                """,
                (limit,),
            )
            return [int(row[0]) for row in cur.fetchall()]


def candidate_review_payload(run_id: int) -> dict[str, Any]:
    """輸出候選主題數的指標解釋 payload，不展開代表文檔全文。

    主題數選擇階段以 coherence / diversity / balance / score 為主，
    Claude CLI 只協助說明三組候選的取捨；代表文檔保留到
    finalize 後的 topic_labeling_payload 使用。
    """
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM derived_layer.topic_runs WHERE run_id = %s", (run_id,))
            run = cur.fetchone()
            if run is None:
                raise ValueError(f"topic run not found: {run_id}")
            cur.execute(
                "SELECT * FROM derived_layer.topic_candidates WHERE run_id = %s ORDER BY candidate_k",
                (run_id,),
            )
            rows = [dict(row) for row in cur.fetchall()]
            spec = get_source_spec(str(run["source_field"]))

    candidate_payloads: list[dict[str, Any]] = []
    for row in rows:
        parameters = dict(row.get("parameters_json") or {})
        # DB 仍保存 c-TF-IDF refs 供後續追蹤，但候選主題數說明不把 refs 或全文交給 LLM。
        parameters.pop(CANDIDATE_REFERENCE_PARAMETER_KEY, None)
        candidate_payloads.append(
            {
                "candidate_id": int(row["candidate_id"]),
                "candidate_type": row["candidate_type"],
                "k": int(row["candidate_k"]),
                "coherence": float(row["coherence"]),
                "diversity": float(row["diversity"]),
                "balance": float(row["balance"]),
                "score": float(row["score"]),
                "parameters": parameters,
                "existing_explanation": row.get("llm_explanation"),
            }
        )

    return {
        "run_id": int(run["run_id"]),
        "workspace_id": int(run["workspace_id"]) if run["workspace_id"] is not None else None,
        "source_field": str(run["source_field"]),
        "source_label": spec.label_zh,
        "document_count": int(run["input_doc_count"]),
        "instruction": (
            "請只根據三組候選的 coherence、diversity、balance、score、k 與資料量，"
            "用一般使用者看得懂的方式說明各候選主題數的取捨。"
            f"每組 explanation 建議 {EXPLANATION_SUGGESTED_RANGE} 字，"
            f"不得超過 {EXPLANATION_MAX_CHARS} 字。"
            "不要要求或引用代表文檔，回傳 explanations 陣列，"
            "每筆包含 candidate_id 與 explanation，供系統寫回 topic_candidates.llm_explanation。"
        ),
        "candidates": candidate_payloads,
    }


def apply_candidate_explanations(
    *,
    run_id: int,
    explanations: list[dict[str, Any]],
) -> dict[str, int]:
    """把 Claude Code 產生的候選方案說明寫回 topic_candidates.llm_explanation。

    只保存說明文字，不代使用者選定候選方案；候選定案仍由使用者透過
    finalize_top_level 指定 candidate_id。空白說明、缺 candidate_id 或超過
    硬上限一律 raise（與 API 端 pydantic 驗證同一口徑），不靜默跳過；
    回傳 requested_count 與 updated_count，兩者不一致代表有 candidate_id
    不屬於此 run。
    """
    if not explanations:
        raise ValueError("explanations must not be empty")

    rows: list[tuple[str, int, int]] = []
    for item in explanations:
        if item.get("candidate_id") is None:
            raise ValueError("each explanation requires candidate_id")
        candidate_id = int(item["candidate_id"])
        explanation = str(item.get("explanation") or item.get("llm_explanation") or "").strip()
        if not explanation:
            raise ValueError(f"candidate {candidate_id} explanation must not be empty")
        if len(explanation) > EXPLANATION_MAX_CHARS:
            raise ValueError(
                f"candidate {candidate_id} explanation exceeds {EXPLANATION_MAX_CHARS} chars"
            )
        rows.append((explanation, run_id, candidate_id))

    updated_count = 0
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """
                    UPDATE derived_layer.topic_candidates
                    SET llm_explanation = %s
                    WHERE run_id = %s
                      AND candidate_id = %s
                    """,
                    row,
                )
                updated_count += cur.rowcount
    return {"requested_count": len(rows), "updated_count": updated_count}

def topic_labeling_payload(
    *,
    workspace_id: int,
    source_field: str,
    topic_ids: list[int] | None = None,
) -> dict[str, Any]:
    """輸出每個 topic 的代表文件，供 Claude Code 產生標籤與短摘要。

    Payload 刻意不輸出 keywords，避免 LLM 被停用詞、c-TF-IDF 或 ngram 切法帶偏。
    前端仍可另外讀 topics.keywords_json，供人工掃描 topic 用。
    """
    spec = get_source_spec(source_field)
    parameters: list[Any] = [workspace_id, source_field]
    topic_filter = sql.SQL("")
    if topic_ids:
        topic_filter = sql.SQL("AND topic_id = ANY(%s)")
        parameters.append(topic_ids)

    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT topic_id, representative_patent_ids_json, label_source
                    FROM derived_layer.topics
                    WHERE workspace_id = %s
                      AND source_field = %s
                      AND topic_kind = 'model'
                      AND status = 'active'
                      {topic_filter}
                    ORDER BY display_order
                    """
                ).format(topic_filter=topic_filter),
                parameters,
            )
            topics = [dict(row) for row in cur.fetchall()]

            payload_topics: list[dict[str, Any]] = []
            for topic in topics:
                patent_ids = [
                    int(value)
                    for value in topic["representative_patent_ids_json"]
                ][:TOPIC_LABELING_DOC_LIMIT]
                excerpts = _fetch_source_excerpts(cur, spec.source_column, patent_ids)
                payload_topics.append(
                    {
                        "topic_id": int(topic["topic_id"]),
                        "current_label_source": topic["label_source"],
                        "representative_patents": excerpts,
                    }
                )

    return {
        "workspace_id": workspace_id,
        "source_field": source_field,
        "source_label": spec.label_zh,
        "instruction": (
            f"請只根據每個 topic 的前 {TOPIC_LABELING_DOC_LIMIT} 筆代表性專利文件產生 "
            "topic_id、label、summary；不要依賴 keywords。"
            f"label 建議 {LABEL_SUGGESTED_RANGE} 個中文字（硬上限 {LABEL_MAX_CHARS} 字），"
            f"summary 建議 {SUMMARY_SUGGESTED_RANGE} 個中文字（硬上限 {SUMMARY_MAX_CHARS} 字），"
            f"超過硬上限會被拒收。{spec.naming_hint}"
        ),
        "topics": payload_topics,
    }


def apply_topic_labels(
    *,
    workspace_id: int,
    source_field: str,
    labels: list[dict[str, Any]],
    updated_by: str = "claude-cli",
) -> dict[str, int]:
    """寫入 Claude CLI 或批次流程產出的 topic label/summary。

    source 只接受 llm/fallback（預設 llm，符合 0010 topics_label_source_check）；
    manual 只能走前端 rename endpoint。label/summary 超過硬上限直接 raise，
    要求 LLM 重生，不靜默截斷。
    """
    if not labels:
        return {"updated_count": 0}

    rows: list[tuple[str, str, str, Jsonb, int, int, str]] = []
    for item in labels:
        topic_id = int(item["topic_id"])
        label = str(item["label"]).strip()
        summary = str(item.get("summary") or "").strip()
        source = str(item.get("source") or "llm")
        if not label:
            raise ValueError(f"topic {topic_id} label must not be empty")
        if len(label) > LABEL_MAX_CHARS:
            raise ValueError(
                f"topic {topic_id} label exceeds {LABEL_MAX_CHARS} chars"
            )
        if len(summary) > SUMMARY_MAX_CHARS:
            raise ValueError(
                f"topic {topic_id} summary exceeds {SUMMARY_MAX_CHARS} chars"
            )
        if source not in APPLY_LABEL_SOURCES:
            raise ValueError(
                f"topic {topic_id} source must be one of {APPLY_LABEL_SOURCES}"
            )
        rows.append(
            (
                label,
                summary,
                source,
                Jsonb({"updated_by": updated_by}),
                topic_id,
                workspace_id,
                source_field,
            )
        )

    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE derived_layer.topics
                SET label = %s,
                    summary = %s,
                    label_source = %s,
                    label_metadata_json = label_metadata_json || %s,
                    updated_at = now()
                WHERE topic_id = %s
                  AND workspace_id = %s
                  AND source_field = %s
                  AND topic_kind = 'model'
                  AND status = 'active'
                  AND label_source <> 'manual'
                """,
                rows,
            )
            updated_count = cur.rowcount
    return {"updated_count": int(updated_count)}


def backfill_representative_patents(
    *,
    workspace_id: int,
    source_field: str,
) -> dict[str, int]:
    """把舊 run 產生、少於目前上限的代表專利清單補到 15 筆。

    舊 finalize 只存 5 筆代表專利，與新 instruction 的「前 15 筆」不一致。
    這裡依 is_current assignment 的 distance_to_centroid 由小到大重取前
    LLM_REPRESENTATIVE_DOC_LIMIT 筆（合併鏈解析到 root topic），只更新
    active model topics 的 representative_patent_ids_json，不動 assignment
    與 label；已達上限的 topic 不重寫。
    """
    get_source_spec(source_field)
    root_by_patent = _resolved_topic_by_patent(
        workspace_id=workspace_id, source_field=source_field
    )
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        assignment_rows = conn.execute(
            """
            SELECT patent_id, distance_to_centroid
            FROM derived_layer.topic_assignments
            WHERE workspace_id = %s AND source_field = %s AND is_current
            """,
            (workspace_id, source_field),
        ).fetchall()
        topic_rows = conn.execute(
            """
            SELECT topic_id, representative_patent_ids_json
            FROM derived_layer.topics
            WHERE workspace_id = %s AND source_field = %s
              AND topic_kind = 'model' AND status = 'active'
            """,
            (workspace_id, source_field),
        ).fetchall()

    # 依 root topic 聚集 (distance, patent_id)，distance 缺值排最後。
    ranked_by_topic: dict[int, list[tuple[float, int]]] = {}
    for row in assignment_rows:
        patent_id = int(row["patent_id"])
        root_topic_id = root_by_patent.get(patent_id)
        if root_topic_id is None:
            continue
        distance = (
            float(row["distance_to_centroid"])
            if row["distance_to_centroid"] is not None
            else math.inf
        )
        ranked_by_topic.setdefault(root_topic_id, []).append((distance, patent_id))

    updates: list[tuple[Jsonb, int]] = []
    for row in topic_rows:
        topic_id = int(row["topic_id"])
        existing = [int(value) for value in row["representative_patent_ids_json"]]
        if len(existing) >= LLM_REPRESENTATIVE_DOC_LIMIT:
            continue
        ranked = sorted(ranked_by_topic.get(topic_id, []))
        selected = [patent_id for _, patent_id in ranked[:LLM_REPRESENTATIVE_DOC_LIMIT]]
        if not selected or selected == existing:
            continue
        updates.append((Jsonb(selected), topic_id))

    if updates:
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    UPDATE derived_layer.topics
                    SET representative_patent_ids_json = %s, updated_at = now()
                    WHERE topic_id = %s AND topic_kind = 'model' AND status = 'active'
                    """,
                    updates,
                )
    return {"topic_count": len(topic_rows), "updated_count": len(updates)}


def incremental_workspace(
    *,
    workspace_id: int,
    source_field: str,
) -> IncrementalSummary:
    """只處理尚無 assignment 的 workspace 新專利，更新既有 online artifact。"""
    latest = _latest_completed_run(workspace_id=workspace_id, source_field=source_field)
    artifact = load_artifact(
        resolve_artifact_path(str(latest["model_artifact_path"])),
        expected_hash=str(latest["model_artifact_hash"]),
    )
    with psycopg.connect(**get_connection_kwargs()) as conn:
        corpus = load_clustering_corpus(conn, workspace_id=workspace_id, source_field=source_field)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT patent_id
                FROM derived_layer.topic_assignments
                WHERE workspace_id = %s AND source_field = %s AND is_current
                """,
                (workspace_id, source_field),
            )
            assigned = {int(row[0]) for row in cur.fetchall()}
    indexes = [index for index, patent_id in enumerate(corpus.patent_ids) if patent_id not in assigned]
    if not indexes:
        return IncrementalSummary(
            run_id=None,
            workspace_id=workspace_id,
            source_field=source_field,
            new_document_count=0,
            assignment_count=0,
            artifact_version=int(latest["artifact_version"]),
            pca_updated=False,
            status="no_new_documents",
        )

    batch = _subset_corpus(corpus, indexes)
    run_id = _create_incremental_run(latest=latest, new_document_count=len(indexes))
    values = np.asarray(batch.matrix.vectors, dtype=float)
    pca_updated = len(values) >= int(getattr(artifact.reducer, "n_components_", 100))
    if pca_updated:
        artifact.reducer.partial_fit(values)
    reduced_values = artifact.reducer.transform(values)
    reduced = ReducedEmbeddingMatrix(
        row_numbers=batch.matrix.row_numbers,
        patent_numbers=batch.matrix.patent_numbers,
        vectors=reduced_values.tolist(),
        reducer="IncrementalPCA",
        n_components=int(reduced_values.shape[1]),
    )

    try:
        predicted_topics = partial_fit_bertopic(artifact.topic_model, batch.documents, reduced)
        assignment_count = _persist_incremental_assignments(
            run_id=run_id,
            workspace_id=workspace_id,
            source_field=source_field,
            corpus=batch,
            reduced=reduced,
            predicted_topics=predicted_topics,
        )
        artifact.run_id = run_id
        artifact.artifact_version = int(latest["artifact_version"]) + 1
        next_key = artifact_key(
            workspace_id=workspace_id,
            source_field=source_field,
            run_id=run_id,
        )
        next_path = artifact_path(
            workspace_id=workspace_id,
            source_field=source_field,
            run_id=run_id,
        )
        next_hash = save_artifact(artifact, next_path)
        _complete_incremental_run(
            run_id=run_id,
            artifact_key_value=next_key,
            artifact_hash=next_hash,
            artifact_version=artifact.artifact_version,
            pca_updated=pca_updated,
        )
        refresh_topic_counts(workspace_id=workspace_id, source_field=source_field)
    except Exception as exc:
        _fail_run(run_id, exc)
        raise

    return IncrementalSummary(
        run_id=run_id,
        workspace_id=workspace_id,
        source_field=source_field,
        new_document_count=len(indexes),
        assignment_count=assignment_count,
        artifact_version=artifact.artifact_version,
        pca_updated=pca_updated,
        status="completed",
    )


def hierarchy_merge_suggestions(
    *,
    workspace_id: int,
    source_field: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """使用 BERTopic 官方 hierarchical_topics，僅回傳可由使用者判斷的相近主題組。"""
    latest = _latest_completed_run(workspace_id=workspace_id, source_field=source_field)
    artifact = load_artifact(
        resolve_artifact_path(str(latest["model_artifact_path"])),
        expected_hash=str(latest["model_artifact_hash"]),
    )
    with psycopg.connect(**get_connection_kwargs()) as conn:
        corpus = load_clustering_corpus(conn, workspace_id=workspace_id, source_field=source_field)
    reduced = artifact.reducer.transform(np.asarray(corpus.matrix.vectors, dtype=float))
    predictions, _ = artifact.topic_model.transform(corpus.documents, embeddings=reduced)
    artifact.topic_model.topics_ = [int(value) for value in predictions]
    hierarchy = artifact.topic_model.hierarchical_topics(corpus.documents)

    active = _active_model_topics(workspace_id=workspace_id, source_field=source_field)
    model_to_db = {
        int(model_topic_id): row
        for row in active
        for model_topic_id in row["model_topic_ids"]
    }
    suggestions: list[dict[str, Any]] = []
    for row in hierarchy.sort_values("Distance").to_dict(orient="records"):
        model_ids = _hierarchy_model_ids(row.get("Topics"))
        db_topics = {int(model_to_db[value]["topic_id"]): model_to_db[value] for value in model_ids if value in model_to_db}
        if len(db_topics) != 2:
            continue
        pair = list(db_topics.values())
        suggestions.append(
            {
                "topic_ids": [int(item["topic_id"]) for item in pair],
                "labels": [str(item["label"] or item["topic_code"]) for item in pair],
                "distance": float(row["Distance"]),
            }
        )
        if len(suggestions) >= limit:
            break
    return suggestions


def merge_workspace_topics(
    *,
    workspace_id: int,
    source_field: str,
    topic_ids: list[int],
    merged_by: str,
    label: str | None = None,
) -> MergeSummary:
    """以 BERTopic 官方 merge_topics 更新模型，DB 另建永久合併主題保留歷史。"""
    selected_ids = list(dict.fromkeys(int(value) for value in topic_ids))
    if len(selected_ids) != 2:
        raise ValueError("exactly two active topics are required for a merge")
    selected = _load_merge_topics(
        workspace_id=workspace_id,
        source_field=source_field,
        topic_ids=selected_ids,
    )
    latest = _latest_completed_run(workspace_id=workspace_id, source_field=source_field)
    artifact = load_artifact(
        resolve_artifact_path(str(latest["model_artifact_path"])),
        expected_hash=str(latest["model_artifact_hash"]),
    )
    with psycopg.connect(**get_connection_kwargs()) as conn:
        corpus = load_clustering_corpus(conn, workspace_id=workspace_id, source_field=source_field)
    reduced = artifact.reducer.transform(np.asarray(corpus.matrix.vectors, dtype=float))
    predictions, _ = artifact.topic_model.transform(corpus.documents, embeddings=reduced)
    artifact.topic_model.topics_ = [int(value) for value in predictions]
    model_topic_ids = sorted(
        {int(value) for row in selected for value in row["model_topic_ids"]}
    )
    if len(model_topic_ids) < 2:
        raise ValueError("selected topics no longer map to two distinct model topics")

    run_id = _create_merge_run(latest=latest, source_topic_ids=selected_ids, merged_by=merged_by)
    try:
        artifact.topic_model.merge_topics(corpus.documents, model_topic_ids)
        merged_predictions = [int(value) for value in artifact.topic_model.topics_]
        root_by_patent = _resolved_topic_by_patent(workspace_id=workspace_id, source_field=source_field)
        selected_indexes = [
            index
            for index, patent_id in enumerate(corpus.patent_ids)
            if root_by_patent.get(patent_id) in selected_ids
        ]
        merged_model_ids = sorted({merged_predictions[index] for index in selected_indexes})
        if len(merged_model_ids) != 1:
            raise ValueError(f"BERTopic merge did not resolve to one topic: {merged_model_ids}")
        merged_topic_id = _persist_topic_merge(
            run_id=run_id,
            workspace_id=workspace_id,
            source_field=source_field,
            selected=selected,
            selected_ids=selected_ids,
            merged_model_id=merged_model_ids[0],
            selected_patent_ids=[corpus.patent_ids[index] for index in selected_indexes],
            merged_by=merged_by,
            label=label,
            topic_model=artifact.topic_model,
            corpus_patent_ids=corpus.patent_ids,
            merged_predictions=merged_predictions,
            root_by_patent=root_by_patent,
        )
        artifact.run_id = run_id
        artifact.artifact_version = int(latest["artifact_version"]) + 1
        next_key = artifact_key(
            workspace_id=workspace_id,
            source_field=source_field,
            run_id=run_id,
        )
        next_path = artifact_path(
            workspace_id=workspace_id,
            source_field=source_field,
            run_id=run_id,
        )
        next_hash = save_artifact(artifact, next_path)
        _complete_merge_run(
            run_id=run_id,
            artifact_key_value=next_key,
            file_hash=next_hash,
            artifact_version=artifact.artifact_version,
        )
        refresh_topic_counts(workspace_id=workspace_id, source_field=source_field)
    except Exception as exc:
        _fail_run(run_id, exc)
        raise

    return MergeSummary(
        run_id=run_id,
        workspace_id=workspace_id,
        source_field=source_field,
        source_topic_ids=selected_ids,
        merged_topic_id=merged_topic_id,
        artifact_version=artifact.artifact_version,
        status="completed",
    )


def merge_history(*, workspace_id: int, source_field: str) -> list[dict[str, Any]]:
    """列出每筆完成的 merge、來源 topics、結果 topic 與目前可否獨立復原。"""
    get_source_spec(source_field)
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        merge_rows = conn.execute(
            """
            SELECT r.*, t.topic_id AS result_topic_id, t.label AS result_label,
                   t.doc_count AS result_doc_count, t.status AS result_status
            FROM derived_layer.topic_runs r
            JOIN derived_layer.topics t ON t.created_run_id = r.run_id
            WHERE r.workspace_id = %s AND r.source_field = %s
              AND r.run_mode = 'merge' AND r.status = 'completed'
            ORDER BY r.run_id DESC
            """,
            (workspace_id, source_field),
        ).fetchall()
        all_completed = conn.execute(
            """
            SELECT run_id, run_mode, parameters_json, reverted_at
            FROM derived_layer.topic_runs
            WHERE workspace_id = %s AND source_field = %s AND status = 'completed'
            ORDER BY run_id
            """,
            (workspace_id, source_field),
        ).fetchall()

        history: list[dict[str, Any]] = []
        for row in merge_rows:
            source_ids = [int(value) for value in row["parameters_json"]["source_topic_ids"]]
            source_rows = conn.execute(
                """
                SELECT topic_id, label, doc_count
                FROM derived_layer.topics
                WHERE topic_id = ANY(%s)
                ORDER BY topic_id
                """,
                (source_ids,),
            ).fetchall()
            result_topic_id = int(row["result_topic_id"])
            blocked_reason = _unmerge_blocked_reason(
                merge_run=dict(row),
                result_topic_id=result_topic_id,
                completed_runs=[dict(item) for item in all_completed],
            )
            history.append(
                {
                    "merge_run_id": int(row["run_id"]),
                    "artifact_version": int(row["artifact_version"]),
                    "merged_by": row["parameters_json"].get("merged_by"),
                    "merged_at": row["completed_at"],
                    "source_topics": [dict(item) for item in source_rows],
                    "result_topic": {
                        "topic_id": result_topic_id,
                        "label": row["result_label"],
                        "doc_count": int(row["result_doc_count"]),
                        "status": row["result_status"],
                    },
                    "is_reverted": row["reverted_at"] is not None,
                    "reverted_at": row["reverted_at"],
                    "reverted_by": row["reverted_by"],
                    "can_unmerge": blocked_reason is None,
                    "blocked_reason": blocked_reason,
                }
            )
    return history


def unmerge_workspace_topics(
    *,
    workspace_id: int,
    source_field: str,
    merge_run_id: int,
    reverted_by: str,
) -> UnmergeSummary:
    """從基底 artifact 重播其餘 merge，獨立復原指定合併紀錄。"""
    history = merge_history(workspace_id=workspace_id, source_field=source_field)
    target = next((item for item in history if item["merge_run_id"] == merge_run_id), None)
    if target is None:
        raise ValueError(f"completed merge run not found: {merge_run_id}")
    if not target["can_unmerge"]:
        raise ValueError(str(target["blocked_reason"] or "merge run cannot be restored"))

    restored_topic_ids = [int(item["topic_id"]) for item in target["source_topics"]]
    reverted_topic_id = int(target["result_topic"]["topic_id"])
    latest = _latest_completed_run(workspace_id=workspace_id, source_field=source_field)
    base_run = _unmerge_base_run(
        workspace_id=workspace_id,
        source_field=source_field,
    )
    artifact = load_artifact(
        resolve_artifact_path(str(base_run["model_artifact_path"])),
        expected_hash=str(base_run["model_artifact_hash"]),
    )
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        corpus = load_clustering_corpus(conn, workspace_id=workspace_id, source_field=source_field)
        assignment_rows = conn.execute(
            """
            SELECT patent_id, topic_id
            FROM derived_layer.topic_assignments
            WHERE workspace_id = %s AND source_field = %s AND is_current
            """,
            (workspace_id, source_field),
        ).fetchall()
    original_topic_by_patent = {
        int(row["patent_id"]): int(row["topic_id"])
        for row in assignment_rows
    }

    run_id = _create_unmerge_run(
        latest=latest,
        target_merge_run_id=merge_run_id,
        restored_topic_ids=restored_topic_ids,
        reverted_topic_id=reverted_topic_id,
        reverted_by=reverted_by,
    )
    try:
        predictions, groups = _replay_active_merges(
            artifact=artifact,
            corpus=corpus,
            original_topic_by_patent=original_topic_by_patent,
            workspace_id=workspace_id,
            source_field=source_field,
            excluded_merge_run_id=merge_run_id,
        )
        desired_active_topic_ids = _desired_active_model_topic_ids(
            workspace_id=workspace_id,
            source_field=source_field,
            restored_topic_ids=restored_topic_ids,
            reverted_topic_id=reverted_topic_id,
        )
        model_ids_by_topic = _model_ids_for_stable_topics(
            stable_topic_ids=desired_active_topic_ids,
            groups=groups,
            corpus_patent_ids=corpus.patent_ids,
            original_topic_by_patent=original_topic_by_patent,
            predictions=predictions,
        )
        artifact.run_id = run_id
        artifact.artifact_version = int(latest["artifact_version"]) + 1
        next_key = artifact_key(
            workspace_id=workspace_id,
            source_field=source_field,
            run_id=run_id,
        )
        next_path = artifact_path(
            workspace_id=workspace_id,
            source_field=source_field,
            run_id=run_id,
        )
        next_hash = save_artifact(artifact, next_path)
        _persist_unmerge(
            run_id=run_id,
            workspace_id=workspace_id,
            source_field=source_field,
            target_merge_run_id=merge_run_id,
            restored_topic_ids=restored_topic_ids,
            reverted_topic_id=reverted_topic_id,
            reverted_by=reverted_by,
            model_ids_by_topic=model_ids_by_topic,
            artifact_key_value=next_key,
            file_hash=next_hash,
            artifact_version=artifact.artifact_version,
        )
        refresh_topic_counts(workspace_id=workspace_id, source_field=source_field)
    except Exception as exc:
        _fail_run(run_id, exc)
        raise

    return UnmergeSummary(
        run_id=run_id,
        workspace_id=workspace_id,
        source_field=source_field,
        target_merge_run_id=merge_run_id,
        restored_topic_ids=restored_topic_ids,
        reverted_topic_id=reverted_topic_id,
        artifact_version=artifact.artifact_version,
        status="completed",
    )


def workspace_dashboard(workspace_id: int) -> dict[str, Any]:
    """輸出臨時前端所需的 workspace、雙通道 chips 與專利列表。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM app_layer.workspaces WHERE workspace_id = %s", (workspace_id,))
            workspace = cur.fetchone()
            if workspace is None:
                raise ValueError(f"workspace not found: {workspace_id}")
            topic_rows = _dashboard_topics(cur, workspace_id)
            cur.execute(
                """
                SELECT
                    p.id AS patent_id,
                    COALESCE(
                        NULLIF(BTRIM(p."授權公告號"), ''),
                        NULLIF(BTRIM(p."審查的公告號"), ''),
                        NULLIF(BTRIM(p."未審查的公開號(轉換後)"), ''),
                        NULLIF(BTRIM(p."未審查的公開號"), ''),
                        NULLIF(BTRIM(p."申請號(轉換後)"), ''),
                        NULLIF(BTRIM(p."申請號"), '')
                    ) AS patent_number,
                    p.title,
                    p.country_code
                FROM app_layer.workspace_patents wp
                JOIN core_layer.patents p ON p.id = wp.patent_id
                WHERE wp.workspace_id = %s
                ORDER BY p.id
                """,
                (workspace_id,),
            )
            patents = [dict(row) for row in cur.fetchall()]

    assignments = {
        source_field: _resolved_topic_by_patent(workspace_id=workspace_id, source_field=source_field)
        for source_field in source_fields()
    }
    labels = {int(row["topic_id"]): row["label"] for row in topic_rows}
    for patent in patents:
        patent_id = int(patent["patent_id"])
        technical_id = assignments["wips_independent_claims"].get(patent_id)
        effect_id = assignments["effect_summary"].get(patent_id)
        patent["technical_topic_id"] = technical_id
        patent["technical_topic"] = labels.get(technical_id, "未分類")
        patent["effect_topic_id"] = effect_id
        patent["effect_topic"] = labels.get(effect_id, "未分類")

    return {
        "workspace": dict(workspace),
        "sources": [
            {
                "source_field": source_field,
                "label": get_source_spec(source_field).label_zh,
                "topics": [row for row in topic_rows if row["source_field"] == source_field],
            }
            for source_field in source_fields()
        ],
        "patents": patents,
    }


def refresh_topic_counts(*, workspace_id: int, source_field: str) -> None:
    """依 assignment 的最終合併 root 重算 active topic 件數，不改原始 assignment。"""
    root_by_patent = _resolved_topic_by_patent(workspace_id=workspace_id, source_field=source_field)
    counts: dict[int, int] = {}
    for topic_id in root_by_patent.values():
        counts[topic_id] = counts.get(topic_id, 0) + 1
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE derived_layer.topics
                SET doc_count = 0, updated_at = now()
                WHERE workspace_id = %s AND source_field = %s AND status = 'active'
                """,
                (workspace_id, source_field),
            )
            cur.executemany(
                "UPDATE derived_layer.topics SET doc_count = %s, updated_at = now() WHERE topic_id = %s",
                [(count, topic_id) for topic_id, count in counts.items()],
            )


def _fetch_source_excerpts(cur: Any, source_column: str, patent_ids: list[int]) -> list[str]:
    """讀取代表性文本全文；正式標籤/摘要階段不截斷獨立項內容。"""
    if not patent_ids:
        return []
    cur.execute(
        sql.SQL(
            "SELECT id, {column} AS source_text "
            "FROM core_layer.patents WHERE id = ANY(%s) ORDER BY id"
        ).format(
            column=sql.Identifier(source_column)
        ),
        (patent_ids,),
    )
    return [
        str(row["source_text"])
        for row in cur.fetchall()
        if row["source_text"]
    ]


def _latest_completed_run(*, workspace_id: int, source_field: str) -> dict[str, Any]:
    """取得具有效 artifact 的最新完成 run。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM derived_layer.topic_runs
            WHERE workspace_id = %s
              AND source_field = %s
              AND status = 'completed'
              AND model_artifact_path IS NOT NULL
            ORDER BY artifact_version DESC, run_id DESC
            LIMIT 1
            """,
            (workspace_id, source_field),
        ).fetchone()
    if row is None:
        raise ValueError("workspace source has no completed clustering artifact")
    return dict(row)


def _subset_corpus(corpus: ClusteringCorpus, indexes: list[int]) -> ClusteringCorpus:
    """依相同索引切出文本、專利與向量，保持三者對齊。"""
    return ClusteringCorpus(
        patent_ids=[corpus.patent_ids[index] for index in indexes],
        documents=[corpus.documents[index] for index in indexes],
        matrix=EmbeddingMatrix(
            row_numbers=[corpus.matrix.row_numbers[index] for index in indexes],
            patent_numbers=[corpus.matrix.patent_numbers[index] for index in indexes],
            vectors=[corpus.matrix.vectors[index] for index in indexes],
        ),
        embedding_model=corpus.embedding_model,
        model_version=corpus.model_version,
        preprocessing_version=corpus.preprocessing_version,
    )


def _create_incremental_run(*, latest: dict[str, Any], new_document_count: int) -> int:
    """建立指向上一 artifact 的 incremental run。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        row = conn.execute(
            """
            INSERT INTO derived_layer.topic_runs (
                workspace_id, source_field, run_mode, previous_run_id, status,
                input_doc_count, new_doc_count, topic_count,
                parameters_json, artifact_version
            ) VALUES (%s, %s, 'incremental', %s, 'running', %s, %s, %s, %s, %s)
            RETURNING run_id
            """,
            (
                latest["workspace_id"],
                latest["source_field"],
                latest["run_id"],
                int(latest["input_doc_count"]) + new_document_count,
                new_document_count,
                latest["topic_count"],
                Jsonb({"method": "BERTopic.partial_fit"}),
                int(latest["artifact_version"]) + 1,
            ),
        ).fetchone()
    return int(row[0])


def _persist_incremental_assignments(
    *,
    run_id: int,
    workspace_id: int,
    source_field: str,
    corpus: ClusteringCorpus,
    reduced: ReducedEmbeddingMatrix,
    predicted_topics: list[int],
) -> int:
    """把本批模型 topic 映射到永久 topic；未知 ID 進 Other 系統桶。"""
    active = _active_model_topics(workspace_id=workspace_id, source_field=source_field)
    model_to_db = {
        int(model_id): int(row["topic_id"])
        for row in active
        for model_id in row["model_topic_ids"]
    }
    with psycopg.connect(**get_connection_kwargs()) as conn:
        other_topic_id = int(
            conn.execute(
                """
                SELECT topic_id FROM derived_layer.topics
                WHERE workspace_id = %s AND source_field = %s
                  AND topic_kind = 'other' AND status = 'active'
                """,
                (workspace_id, source_field),
            ).fetchone()[0]
        )
        vectors = np.asarray(reduced.vectors, dtype=float)
        centers = {
            topic_id: vectors[[i for i, value in enumerate(predicted_topics) if value == topic_id]].mean(axis=0)
            for topic_id in set(predicted_topics)
        }
        rows = []
        for index, model_topic_id in enumerate(predicted_topics):
            db_topic_id = model_to_db.get(model_topic_id, other_topic_id)
            distance = float(np.linalg.norm(vectors[index] - centers[model_topic_id]))
            rows.append(
                (workspace_id, source_field, corpus.patent_ids[index], db_topic_id, run_id, distance)
            )
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO derived_layer.topic_assignments (
                    workspace_id, source_field, patent_id, topic_id,
                    assigned_run_id, distance_to_centroid
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
    return len(rows)


def _complete_incremental_run(
    *,
    run_id: int,
    artifact_key_value: str,
    artifact_hash: str,
    artifact_version: int,
    pca_updated: bool,
) -> None:
    """完成 incremental run 並保存 artifact 位置與 PCA 更新狀態。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        conn.execute(
            """
            UPDATE derived_layer.topic_runs
            SET status = 'completed', model_artifact_path = %s,
                model_artifact_hash = %s, artifact_version = %s,
                metrics_json = metrics_json || %s,
                completed_at = now(), updated_at = now()
            WHERE run_id = %s
            """,
            (
                artifact_key_value,
                artifact_hash,
                artifact_version,
                Jsonb({"pca_updated": pca_updated}),
                run_id,
            ),
        )


def _active_model_topics(*, workspace_id: int, source_field: str) -> list[dict[str, Any]]:
    """取得目前可顯示且對應模型 ID 的 active topics。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT * FROM derived_layer.topics
            WHERE workspace_id = %s AND source_field = %s
              AND topic_kind = 'model' AND status = 'active'
            ORDER BY display_order
            """,
            (workspace_id, source_field),
        ).fetchall()
    return [dict(row) for row in rows]


def _hierarchy_model_ids(value: Any) -> list[int]:
    """解析 BERTopic hierarchy DataFrame 的 Topics 欄。"""
    if isinstance(value, (list, tuple)):
        return [int(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return []
        if isinstance(parsed, (list, tuple)):
            return [int(item) for item in parsed]
    return []


def _load_merge_topics(
    *,
    workspace_id: int,
    source_field: str,
    topic_ids: list[int],
) -> list[dict[str, Any]]:
    """鎖定兩個 active model topics，拒絕跨 workspace 或系統桶合併。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT * FROM derived_layer.topics
            WHERE workspace_id = %s AND source_field = %s
              AND topic_id = ANY(%s)
              AND topic_kind = 'model' AND status = 'active'
            FOR UPDATE
            """,
            (workspace_id, source_field, topic_ids),
        ).fetchall()
    if len(rows) != 2:
        raise ValueError("merge topics must be active model topics in the same workspace source")
    return [dict(row) for row in rows]


def _create_merge_run(
    *,
    latest: dict[str, Any],
    source_topic_ids: list[int],
    merged_by: str,
) -> int:
    """建立人工 merge run，保留來源 topics 與操作者。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        row = conn.execute(
            """
            INSERT INTO derived_layer.topic_runs (
                workspace_id, source_field, run_mode, previous_run_id,
                status, input_doc_count, topic_count, parameters_json,
                artifact_version
            ) VALUES (%s, %s, 'merge', %s, 'running', %s, %s, %s, %s)
            RETURNING run_id
            """,
            (
                latest["workspace_id"],
                latest["source_field"],
                latest["run_id"],
                latest["input_doc_count"],
                max(0, int(latest["topic_count"]) - 1),
                Jsonb({"source_topic_ids": source_topic_ids, "merged_by": merged_by}),
                int(latest["artifact_version"]) + 1,
            ),
        ).fetchone()
    return int(row[0])


def _persist_topic_merge(
    *,
    run_id: int,
    workspace_id: int,
    source_field: str,
    selected: list[dict[str, Any]],
    selected_ids: list[int],
    merged_model_id: int,
    selected_patent_ids: list[int],
    merged_by: str,
    label: str | None,
    topic_model: Any,
    corpus_patent_ids: list[int],
    merged_predictions: list[int],
    root_by_patent: dict[int, int],
) -> int:
    """建立新合併 topic，並原子更新所有 active topic 的模型 ID 映射。"""
    terms = [
        {"term": term, "weight": float(weight)}
        for term, weight in (topic_model.get_topic(merged_model_id) or [])[:10]
    ]
    fallback_label = label or " / ".join(item["term"] for item in terms[:3]) or "合併主題"
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(max(display_order), 0) + 1 FROM derived_layer.topics WHERE workspace_id=%s AND source_field=%s",
                (workspace_id, source_field),
            )
            display_order = int(cur.fetchone()[0])
            cur.execute(
                """
                INSERT INTO derived_layer.topics (
                    workspace_id, source_field, created_run_id, topic_code,
                    model_topic_ids, topic_kind, doc_count, keywords_json,
                    representative_patent_ids_json, label, label_source,
                    display_order, status
                ) VALUES (%s, %s, %s, %s, %s, 'model', %s, %s, %s, %s, %s, %s, 'active')
                RETURNING topic_id
                """,
                (
                    workspace_id,
                    source_field,
                    run_id,
                    f"M{run_id:05d}",
                    [merged_model_id],
                    len(selected_patent_ids),
                    Jsonb(terms),
                    Jsonb(selected_patent_ids[:LLM_REPRESENTATIVE_DOC_LIMIT]),
                    fallback_label,
                    "manual" if label else "fallback",
                    display_order,
                ),
            )
            merged_topic_id = int(cur.fetchone()[0])
            cur.execute(
                """
                UPDATE derived_layer.topics
                SET status = 'merged', merged_into_topic_id = %s,
                    merged_by = %s, merged_at = now(), updated_at = now()
                WHERE topic_id = ANY(%s) AND status = 'active'
                """,
                (merged_topic_id, merged_by, selected_ids),
            )
            if cur.rowcount != 2:
                raise ValueError("merge topics changed concurrently")

            # BERTopic merge 後可能重編未合併的 topic ID；若只更新新主題，
            # 下一批 partial_fit 會把既有模型 ID 對到錯誤的永久 topic。
            model_ids_by_topic: dict[int, set[int]] = {}
            for patent_id, model_topic_id in zip(
                corpus_patent_ids,
                merged_predictions,
                strict=True,
            ):
                root_topic_id = root_by_patent.get(patent_id)
                if root_topic_id in selected_ids:
                    root_topic_id = merged_topic_id
                if root_topic_id is not None:
                    model_ids_by_topic.setdefault(root_topic_id, set()).add(model_topic_id)
            cur.executemany(
                """
                UPDATE derived_layer.topics
                SET model_topic_ids = %s, updated_at = now()
                WHERE topic_id = %s AND topic_kind = 'model' AND status = 'active'
                """,
                [
                    (sorted(model_ids), topic_id)
                    for topic_id, model_ids in model_ids_by_topic.items()
                ],
            )
    return merged_topic_id


def _complete_merge_run(
    *, run_id: int, artifact_key_value: str, file_hash: str, artifact_version: int
) -> None:
    """完成 merge run 並保存新版 artifact。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        conn.execute(
            """
            UPDATE derived_layer.topic_runs
            SET status='completed', model_artifact_path=%s,
                model_artifact_hash=%s, artifact_version=%s,
                completed_at=now(), updated_at=now()
            WHERE run_id=%s
            """,
            (artifact_key_value, file_hash, artifact_version, run_id),
        )


def _unmerge_blocked_reason(
    *,
    merge_run: dict[str, Any],
    result_topic_id: int,
    completed_runs: list[dict[str, Any]],
) -> str | None:
    """判斷指定 merge 是否可獨立復原，避免破壞後續依賴。"""
    if merge_run.get("reverted_at") is not None:
        return "此合併紀錄已復原"
    if merge_run.get("result_status") != "active":
        return "合併結果已被後續主題操作使用"
    merge_run_id = int(merge_run["run_id"])
    for row in completed_runs:
        if int(row["run_id"]) <= merge_run_id:
            continue
        if row["run_mode"] in {"full", "incremental"}:
            return "合併後已有 full 或 incremental 更新，需先建立重建策略"
        if row["run_mode"] != "merge" or row.get("reverted_at") is not None:
            continue
        source_ids = [
            int(value)
            for value in row["parameters_json"].get("source_topic_ids", [])
        ]
        if result_topic_id in source_ids:
            return "此合併結果已被後續合併使用，需先復原下游紀錄"
    return None


def _unmerge_base_run(*, workspace_id: int, source_field: str) -> dict[str, Any]:
    """取得第一筆仍有效 merge 前的最近 full/incremental artifact。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        first_merge = conn.execute(
            """
            SELECT min(run_id) AS first_merge_run_id
            FROM derived_layer.topic_runs
            WHERE workspace_id = %s AND source_field = %s
              AND run_mode = 'merge' AND status = 'completed'
              AND reverted_at IS NULL
            """,
            (workspace_id, source_field),
        ).fetchone()["first_merge_run_id"]
        if first_merge is None:
            raise ValueError("workspace source has no active merge history")
        row = conn.execute(
            """
            SELECT * FROM derived_layer.topic_runs
            WHERE workspace_id = %s AND source_field = %s
              AND run_mode IN ('full', 'incremental')
              AND status = 'completed' AND run_id < %s
              AND model_artifact_path IS NOT NULL
            ORDER BY run_id DESC
            LIMIT 1
            """,
            (workspace_id, source_field, first_merge),
        ).fetchone()
    if row is None:
        raise ValueError("unmerge base full/incremental artifact not found")
    return dict(row)


def _create_unmerge_run(
    *,
    latest: dict[str, Any],
    target_merge_run_id: int,
    restored_topic_ids: list[int],
    reverted_topic_id: int,
    reverted_by: str,
) -> int:
    """建立 unmerge run，先保存目標 merge 與預計恢復的永久 topic IDs。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        row = conn.execute(
            """
            INSERT INTO derived_layer.topic_runs (
                workspace_id, source_field, run_mode, previous_run_id,
                status, input_doc_count, topic_count, parameters_json,
                artifact_version
            ) VALUES (%s, %s, 'unmerge', %s, 'running', %s, %s, %s, %s)
            RETURNING run_id
            """,
            (
                latest["workspace_id"],
                latest["source_field"],
                latest["run_id"],
                latest["input_doc_count"],
                int(latest["topic_count"]) + 1,
                Jsonb(
                    {
                        "target_merge_run_id": target_merge_run_id,
                        "restored_topic_ids": restored_topic_ids,
                        "reverted_topic_id": reverted_topic_id,
                        "reverted_by": reverted_by,
                    }
                ),
                int(latest["artifact_version"]) + 1,
            ),
        ).fetchone()
    return int(row[0])


def _replay_active_merges(
    *,
    artifact: WorkspaceTopicArtifact,
    corpus: ClusteringCorpus,
    original_topic_by_patent: dict[int, int],
    workspace_id: int,
    source_field: str,
    excluded_merge_run_id: int,
) -> tuple[list[int], dict[int, set[int]]]:
    """從基底模型依時間重播其他有效 merge，產生排除目標後的模型狀態。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT r.run_id, r.parameters_json, t.topic_id AS result_topic_id
            FROM derived_layer.topic_runs r
            JOIN derived_layer.topics t ON t.created_run_id = r.run_id
            WHERE r.workspace_id = %s AND r.source_field = %s
              AND r.run_mode = 'merge' AND r.status = 'completed'
              AND r.reverted_at IS NULL AND r.run_id <> %s
            ORDER BY r.run_id
            """,
            (workspace_id, source_field, excluded_merge_run_id),
        ).fetchall()

    reduced = artifact.reducer.transform(np.asarray(corpus.matrix.vectors, dtype=float))
    predictions, _ = artifact.topic_model.transform(corpus.documents, embeddings=reduced)
    current_predictions = [int(value) for value in predictions]
    artifact.topic_model.topics_ = current_predictions
    original_topic_ids = set(original_topic_by_patent.values())
    groups: dict[int, set[int]] = {
        topic_id: {topic_id}
        for topic_id in original_topic_ids
    }

    for row in rows:
        source_ids = [
            int(value)
            for value in row["parameters_json"].get("source_topic_ids", [])
        ]
        missing = [topic_id for topic_id in source_ids if topic_id not in groups]
        if missing:
            raise ValueError(f"merge replay source topics are unavailable: {missing}")
        original_group = set().union(*(groups[topic_id] for topic_id in source_ids))
        indexes = [
            index
            for index, patent_id in enumerate(corpus.patent_ids)
            if original_topic_by_patent.get(patent_id) in original_group
        ]
        model_ids = sorted({current_predictions[index] for index in indexes})
        if len(model_ids) < 2:
            raise ValueError(
                f"merge replay run {row['run_id']} no longer maps to two model topics"
            )
        artifact.topic_model.merge_topics(corpus.documents, model_ids)
        current_predictions = [int(value) for value in artifact.topic_model.topics_]
        groups[int(row["result_topic_id"])] = original_group
    return current_predictions, groups


def _desired_active_model_topic_ids(
    *,
    workspace_id: int,
    source_field: str,
    restored_topic_ids: list[int],
    reverted_topic_id: int,
) -> list[int]:
    """計算 unmerge transaction 完成後應保持 active 的 model topic IDs。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        active_ids = {
            int(row[0])
            for row in conn.execute(
                """
                SELECT topic_id FROM derived_layer.topics
                WHERE workspace_id = %s AND source_field = %s
                  AND topic_kind = 'model' AND status = 'active'
                """,
                (workspace_id, source_field),
            ).fetchall()
        }
    active_ids.discard(reverted_topic_id)
    active_ids.update(restored_topic_ids)
    return sorted(active_ids)


def _model_ids_for_stable_topics(
    *,
    stable_topic_ids: list[int],
    groups: dict[int, set[int]],
    corpus_patent_ids: list[int],
    original_topic_by_patent: dict[int, int],
    predictions: list[int],
) -> dict[int, list[int]]:
    """把重播後模型 topic IDs 對回每個 active 永久 topic。"""
    result: dict[int, list[int]] = {}
    for stable_topic_id in stable_topic_ids:
        original_group = groups.get(stable_topic_id)
        if not original_group:
            raise ValueError(f"stable topic lacks replay group: {stable_topic_id}")
        model_ids = {
            predictions[index]
            for index, patent_id in enumerate(corpus_patent_ids)
            if original_topic_by_patent.get(patent_id) in original_group
        }
        if not model_ids:
            raise ValueError(f"stable topic has no model predictions: {stable_topic_id}")
        result[stable_topic_id] = sorted(model_ids)
    return result


def _persist_unmerge(
    *,
    run_id: int,
    workspace_id: int,
    source_field: str,
    target_merge_run_id: int,
    restored_topic_ids: list[int],
    reverted_topic_id: int,
    reverted_by: str,
    model_ids_by_topic: dict[int, list[int]],
    artifact_key_value: str,
    file_hash: str,
    artifact_version: int,
) -> None:
    """原子恢復來源 topics、封存合併結果並完成 unmerge 稽核。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id FROM derived_layer.topic_runs
                WHERE run_id = %s AND workspace_id = %s AND source_field = %s
                  AND run_mode = 'merge' AND status = 'completed'
                  AND reverted_at IS NULL
                FOR UPDATE
                """,
                (target_merge_run_id, workspace_id, source_field),
            )
            if cur.fetchone() is None:
                raise ValueError("merge run changed concurrently or was already restored")
            cur.execute(
                """
                UPDATE derived_layer.topics
                SET status = 'active', merged_into_topic_id = NULL,
                    merged_by = NULL, merged_at = NULL, updated_at = now()
                WHERE topic_id = ANY(%s) AND status = 'merged'
                  AND merged_into_topic_id = %s
                """,
                (restored_topic_ids, reverted_topic_id),
            )
            if cur.rowcount != len(restored_topic_ids):
                raise ValueError("merge source topics changed concurrently")
            cur.execute(
                """
                UPDATE derived_layer.topics
                SET status = 'reverted', reverted_by = %s, reverted_at = now(),
                    doc_count = 0, updated_at = now()
                WHERE topic_id = %s AND status = 'active'
                  AND created_run_id = %s
                """,
                (reverted_by, reverted_topic_id, target_merge_run_id),
            )
            if cur.rowcount != 1:
                raise ValueError("merge result topic changed concurrently")
            cur.execute(
                """
                UPDATE derived_layer.topic_runs
                SET reverted_at = now(), reverted_by = %s, reverted_by_run_id = %s,
                    updated_at = now()
                WHERE run_id = %s AND reverted_at IS NULL
                """,
                (reverted_by, run_id, target_merge_run_id),
            )
            if cur.rowcount != 1:
                raise ValueError("merge run was restored concurrently")
            cur.executemany(
                """
                UPDATE derived_layer.topics
                SET model_topic_ids = %s, updated_at = now()
                WHERE topic_id = %s AND topic_kind = 'model'
                """,
                [
                    (model_ids, topic_id)
                    for topic_id, model_ids in model_ids_by_topic.items()
                ],
            )
            cur.execute(
                """
                UPDATE derived_layer.topic_runs
                SET status = 'completed', model_artifact_path = %s,
                    model_artifact_hash = %s, artifact_version = %s,
                    completed_at = now(), updated_at = now()
                WHERE run_id = %s AND status = 'running'
                """,
                (artifact_key_value, file_hash, artifact_version, run_id),
            )
            if cur.rowcount != 1:
                raise ValueError("unmerge run changed concurrently")


def _resolved_topic_by_patent(*, workspace_id: int, source_field: str) -> dict[int, int]:
    """在 Python 解析 merged_into 鏈，保留 assignment 的原始模型判斷。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        topic_rows = conn.execute(
            "SELECT topic_id, merged_into_topic_id FROM derived_layer.topics WHERE workspace_id=%s AND source_field=%s",
            (workspace_id, source_field),
        ).fetchall()
        assignment_rows = conn.execute(
            """
            SELECT patent_id, topic_id FROM derived_layer.topic_assignments
            WHERE workspace_id=%s AND source_field=%s AND is_current
            """,
            (workspace_id, source_field),
        ).fetchall()
    parent = {
        int(row["topic_id"]): (
            int(row["merged_into_topic_id"]) if row["merged_into_topic_id"] is not None else None
        )
        for row in topic_rows
    }

    def resolve(topic_id: int) -> int:
        visited: set[int] = set()
        while parent.get(topic_id) is not None:
            if topic_id in visited:
                raise ValueError("topic merge cycle detected")
            visited.add(topic_id)
            topic_id = int(parent[topic_id])
        return topic_id

    return {int(row["patent_id"]): resolve(int(row["topic_id"])) for row in assignment_rows}


def _dashboard_topics(cur: Any, workspace_id: int) -> list[dict[str, Any]]:
    """取得前端 chips，摘要仍存 DB 但不輸出到第一版頁面。"""
    cur.execute(
        """
        SELECT topic_id, source_field, topic_code, topic_kind,
               doc_count, label, display_order, status
        FROM derived_layer.topics
        WHERE workspace_id=%s AND status='active'
        ORDER BY source_field, display_order, topic_id
        """,
        (workspace_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def _fail_run(run_id: int, error: Exception) -> None:
    """保留失敗 run 與錯誤，禁止半套狀態被當成完成。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        conn.execute(
            """
            UPDATE derived_layer.topic_runs
            SET status='failed', error_message=%s, completed_at=now(), updated_at=now()
            WHERE run_id=%s
            """,
            (str(error)[:4000], run_id),
        )
