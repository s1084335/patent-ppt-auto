"""第一層 BERTopic 正式管線：DB corpus、k 掃描、候選與定案落庫。"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import logging
import math
from pathlib import Path
import time
from typing import Any

import numpy as np
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.db.connection import get_connection_kwargs

from .model import (
    EmbeddingMatrix,
    ModelConfig,
    ReducedEmbeddingMatrix,
    fit_bertopic,
    fit_incremental_pca,
    reduce_with_incremental_pca,
    topic_cv_coherence_per_topic,
)
from .artifacts import WorkspaceTopicArtifact, artifact_path, save_artifact
from .preprocessing import clean_patent_text, sha256_text
from .sources import SOURCE_FIELD_TECHNICAL, get_source_spec


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_FIELD_WIPS_INDEPENDENT_CLAIMS = SOURCE_FIELD_TECHNICAL
PCA_COMPONENTS = 100
MIN_CLUSTERING_DOCUMENTS = 50
REPRESENTATIVE_DOC_LIMIT_FOR_LLM = 15

# CLI 直接執行時載入專案 .env；容器正式部署仍可用環境變數覆蓋。
load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class ClusteringCorpus:
    """保存 DB 文本、專利 ID 與已重用 embedding 的對齊結果。"""

    patent_ids: list[int]
    documents: list[str]
    matrix: EmbeddingMatrix
    embedding_model: str
    model_version: str
    preprocessing_version: str


@dataclass
class KScanResult:
    """保存單一候選 k 的指標、分群數與比較分數。"""

    k: int
    topic_count: int
    coherence: float
    diversity: float
    balance: float
    small_topic_ratio: float
    elapsed_seconds: float
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """轉成 DB metrics JSON 與 CLI 輸出的字典。"""
        return asdict(self)


@dataclass(frozen=True)
class CandidateProfile:
    """保存要送到前端的保守、平衡或細分候選。"""

    candidate_type: str
    result: KScanResult

    def to_dict(self) -> dict[str, Any]:
        """輸出候選類型與完整 k 指標。"""
        return {"candidate_type": self.candidate_type, **self.result.to_dict()}


@dataclass(frozen=True)
class CalibrationSummary:
    """回報第一層七組 k 掃描與三組候選的持久化結果。"""

    run_id: int
    scope: str
    workspace_id: int | None
    source_field: str
    input_doc_count: int
    reduced_dimensions: int
    k_scan: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    status: str

    def to_dict(self) -> dict[str, Any]:
        """轉成 CLI 可直接輸出的 JSON payload。"""
        return asdict(self)


@dataclass(frozen=True)
class FinalizationSummary:
    """回報使用者選定候選後第一層 topic 與 assignment 筆數。"""

    run_id: int
    candidate_id: int
    candidate_type: str
    selected_k: int
    topic_count: int
    assignment_count: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        """轉成 CLI 可直接輸出的 JSON payload。"""
        return asdict(self)


def top_level_k_values(document_count: int) -> tuple[int, ...]:
    """依 workspace 可用文件數回傳階梯式 k；小資料不測不合理的大主題數。"""
    if document_count < MIN_CLUSTERING_DOCUMENTS:
        raise ValueError(f"clustering requires at least {MIN_CLUSTERING_DOCUMENTS} documents")
    if document_count < 100:
        return (5, 10)
    maximum_k = min(40, 15 + 5 * ((document_count - 100) // 100))
    return tuple(range(10, maximum_k + 1, 5))


def load_clustering_corpus(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: int | None,
    source_field: str,
) -> ClusteringCorpus:
    """從 global 或 workspace 的技術／功效向量表讀取對齊文本與 embedding。"""
    from psycopg import sql

    spec = get_source_spec(source_field)
    embedding_schema, embedding_table = spec.embedding_table.split(".", maxsplit=1)

    workspace_join = ""
    parameters: tuple[Any, ...] = ()
    if workspace_id is not None:
        workspace_join = """
            JOIN app_layer.workspace_patents wp
              ON wp.patent_id = p.id
             AND wp.workspace_id = %s
        """
        parameters = (workspace_id,)

    query = sql.SQL(
        """
        SELECT
            p.id AS patent_id,
            p.{source_column} AS source_text,
            COALESCE(
                NULLIF(BTRIM(p."授權公告號"), ''),
                NULLIF(BTRIM(p."審查的公告號"), ''),
                NULLIF(BTRIM(p."未審查的公開號(轉換後)"), ''),
                NULLIF(BTRIM(p."未審查的公開號"), ''),
                NULLIF(BTRIM(p."申請號(轉換後)"), ''),
                NULLIF(BTRIM(p."申請號"), '')
            ) AS patent_number,
            e.embedding_model,
            e.model_version,
            e.preprocessing_version,
            e.text_hash,
            e.embedding_vector::text AS vector_text
        FROM core_layer.patents p
        {workspace_join}
        JOIN LATERAL (
            SELECT embedding.*
            FROM {embedding_table} embedding
            WHERE embedding.patent_id = p.id
            ORDER BY embedding.created_at DESC, embedding.embedding_id DESC
            LIMIT 1
        ) e ON true
        WHERE NULLIF(BTRIM(p.{source_column}), '') IS NOT NULL
        ORDER BY p.id
        """
    ).format(
        source_column=sql.Identifier(spec.source_column),
        workspace_join=sql.SQL(workspace_join),
        embedding_table=sql.Identifier(embedding_schema, embedding_table),
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, parameters)
        rows = cur.fetchall()
    if not rows:
        raise ValueError("no patents with reusable embeddings were found for this scope")

    patent_ids: list[int] = []
    documents: list[str] = []
    patent_numbers: list[str] = []
    vectors: list[list[float]] = []
    model_keys: set[tuple[str, str, str]] = set()
    for row in rows:
        cleaned_text = clean_patent_text(row["source_text"])
        if sha256_text(cleaned_text) != row["text_hash"]:
            raise ValueError(f"patent {row['patent_id']} embedding is stale for current source text")
        vector = json.loads(row["vector_text"])
        if not isinstance(vector, list) or len(vector) != 768:
            raise ValueError(f"patent {row['patent_id']} does not have a 768-dimensional embedding")

        patent_ids.append(int(row["patent_id"]))
        documents.append(cleaned_text)
        patent_numbers.append(str(row["patent_number"]))
        vectors.append([float(value) for value in vector])
        model_keys.add(
            (
                str(row["embedding_model"]),
                str(row["model_version"]),
                str(row["preprocessing_version"]),
            )
        )

    if len(model_keys) != 1:
        raise ValueError(f"scope contains incompatible embedding versions: {sorted(model_keys)}")
    embedding_model, model_version, preprocessing_version = next(iter(model_keys))
    return ClusteringCorpus(
        patent_ids=patent_ids,
        documents=documents,
        matrix=EmbeddingMatrix(
            row_numbers=patent_ids,
            patent_numbers=patent_numbers,
            vectors=vectors,
        ),
        embedding_model=embedding_model,
        model_version=model_version,
        preprocessing_version=preprocessing_version,
    )


def scan_top_level_k(
    corpus: ClusteringCorpus,
    *,
    reduced_matrix: ReducedEmbeddingMatrix,
    batch_size: int,
    kmeans_batch_size: int,
    k_values: tuple[int, ...] | None = None,
) -> list[KScanResult]:
    """依資料量逐一執行候選 k，計算 coherence、diversity、balance 與 score。"""
    k_values = k_values or top_level_k_values(len(corpus.documents))
    if len(corpus.documents) <= max(k_values):
        raise ValueError("document count must be greater than the maximum candidate k")

    results: list[KScanResult] = []
    for k in k_values:
        LOGGER.info("第一層候選分群開始：k=%d", k)
        config = ModelConfig(
            n_components=PCA_COMPONENTS,
            n_clusters=k,
            batch_size=batch_size,
            kmeans_batch_size=kmeans_batch_size,
            show_progress_bar=False,
        )
        result = fit_bertopic(
            corpus.documents,
            reduced_matrix,
            scheme_name=f"PCA{PCA_COMPONENTS}D-k{k}",
            config=config,
        )
        actual_topic_count = len({topic for topic in result.topics if topic != -1})
        scan_result = KScanResult(
            k=k,
            topic_count=actual_topic_count,
            coherence=float(result.metrics["coherence"]),
            diversity=float(result.metrics["diversity"]),
            balance=float(result.metrics["balance"]),
            small_topic_ratio=float(result.metrics["small_topic_ratio"]),
            elapsed_seconds=float(result.elapsed_seconds or 0.0),
        )
        LOGGER.info(
            "第一層候選完成：k=%d coherence=%.4f diversity=%.4f balance=%.4f",
            k,
            scan_result.coherence,
            scan_result.diversity,
            scan_result.balance,
        )
        results.append(scan_result)
    attach_k_scan_scores(results)
    return results


def attach_k_scan_scores(results: list[KScanResult]) -> None:
    """以既定權重正規化七組指標，寫回只供候選排序使用的 score。"""
    coherence = _normalize_values([item.coherence for item in results], higher_is_better=True)
    diversity = _normalize_values([item.diversity for item in results], higher_is_better=True)
    balance = _normalize_values([item.balance for item in results], higher_is_better=True)
    small = _normalize_values([item.small_topic_ratio for item in results], higher_is_better=False)
    for index, item in enumerate(results):
        item.score = float(
            0.40 * coherence[index]
            + 0.25 * diversity[index]
            + 0.25 * balance[index]
            + 0.10 * small[index]
        )


def select_candidate_profiles(results: list[KScanResult]) -> list[CandidateProfile]:
    """將實際 k 範圍分成低、中、高區；兩個 k 時只提供兩組有效候選。"""
    ordered = sorted(results, key=lambda item: item.k)
    if len(ordered) < 2:
        raise ValueError("at least two k scan results are required")
    if len({item.k for item in ordered}) != len(ordered):
        raise ValueError("k scan results must not contain duplicate k values")

    if len(ordered) == 2:
        partitions: tuple[tuple[str, list[KScanResult]], ...] = (
            ("conservative", [ordered[0]]),
            ("detailed", [ordered[1]]),
        )
    else:
        edge_size = max(1, round(len(ordered) / 3))
        partitions = (
            ("conservative", ordered[:edge_size]),
            ("balanced", ordered[edge_size:-edge_size]),
            ("detailed", ordered[-edge_size:]),
        )
    candidates: list[CandidateProfile] = []
    for candidate_type, group in partitions:
        if candidate_type == "detailed":
            selected = max(group, key=lambda item: (item.score, item.k))
        elif candidate_type == "balanced":
            selected = max(group, key=lambda item: (item.score, -abs(item.k - 25)))
        else:
            selected = max(group, key=lambda item: (item.score, -item.k))
        candidates.append(CandidateProfile(candidate_type=candidate_type, result=selected))
    return candidates


def calibrate_top_level(
    *,
    workspace_id: int | None,
    source_field: str = SOURCE_FIELD_WIPS_INDEPENDENT_CLAIMS,
    batch_size: int = 8,
    kmeans_batch_size: int = 128,
) -> CalibrationSummary:
    """建立正式 run，掃描七組 k 並將三組候選寫入 DB 等待使用者選擇。"""
    scope = "workspace" if workspace_id is not None else "global"
    with psycopg.connect(**get_connection_kwargs()) as conn:
        corpus = load_clustering_corpus(conn, workspace_id=workspace_id, source_field=source_field)
        k_values = top_level_k_values(len(corpus.documents))
        parameters = {
            "stage": "top_level_calibration",
            "scope": scope,
            "k_values": list(k_values),
            "pca_components": PCA_COMPONENTS,
            "embedding_model": corpus.embedding_model,
            "model_version": corpus.model_version,
            "preprocessing_version": corpus.preprocessing_version,
            "batch_size": batch_size,
            "kmeans_batch_size": kmeans_batch_size,
        }
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO derived_layer.topic_runs (
                    workspace_id, source_field, run_mode, status,
                    input_doc_count, parameters_json
                ) VALUES (%s, %s, 'full', 'running', %s, %s)
                RETURNING run_id
                """,
                (workspace_id, source_field, len(corpus.documents), Jsonb(parameters)),
            )
            run_id = int(cur.fetchone()[0])

    try:
        LOGGER.info("第一層 PCA%dD 開始：documents=%d", PCA_COMPONENTS, len(corpus.documents))
        reduced_matrix = reduce_with_incremental_pca(
            corpus.matrix,
            n_components=PCA_COMPONENTS,
            batch_size=min(kmeans_batch_size, len(corpus.documents)),
        )
        scan_results = scan_top_level_k(
            corpus,
            reduced_matrix=reduced_matrix,
            batch_size=batch_size,
            kmeans_batch_size=kmeans_batch_size,
            k_values=k_values,
        )
        candidates = select_candidate_profiles(scan_results)
        persisted_candidates = _persist_calibration(
            run_id=run_id,
            scan_results=scan_results,
            candidates=candidates,
        )
    except Exception as exc:
        _mark_run_failed(run_id, exc)
        raise

    return CalibrationSummary(
        run_id=run_id,
        scope=scope,
        workspace_id=workspace_id,
        source_field=source_field,
        input_doc_count=len(corpus.documents),
        reduced_dimensions=reduced_matrix.n_components,
        k_scan=[item.to_dict() for item in scan_results],
        candidates=persisted_candidates,
        status="needs_review",
    )


def finalize_top_level(
    *,
    run_id: int,
    candidate_id: int,
    selected_by: str,
    batch_size: int = 8,
    kmeans_batch_size: int = 128,
) -> FinalizationSummary:
    """依使用者選定 candidate 重跑該 k，正式寫入第一層 topics 與 assignments。"""
    run_row, candidate_row = _load_run_and_candidate(run_id=run_id, candidate_id=candidate_id)
    workspace_id = run_row["workspace_id"]
    if workspace_id is None:
        raise ValueError("first release finalization requires a workspace run")
    source_field = str(run_row["source_field"])
    selected_k = int(candidate_row["candidate_k"])

    with psycopg.connect(**get_connection_kwargs()) as conn:
        corpus = load_clustering_corpus(conn, workspace_id=workspace_id, source_field=source_field)
    if len(corpus.documents) != int(run_row["input_doc_count"]):
        raise ValueError("scope membership changed after calibration; create a new calibration run")

    _mark_run_running(run_id)
    try:
        reduced_matrix, reducer = fit_incremental_pca(
            corpus.matrix,
            n_components=PCA_COMPONENTS,
            batch_size=min(kmeans_batch_size, len(corpus.documents)),
        )
        config = ModelConfig(
            n_components=PCA_COMPONENTS,
            n_clusters=selected_k,
            batch_size=batch_size,
            kmeans_batch_size=kmeans_batch_size,
            show_progress_bar=False,
        )
        result = fit_bertopic(
            corpus.documents,
            reduced_matrix,
            scheme_name=f"PCA{PCA_COMPONENTS}D-k{selected_k}",
            config=config,
        )
        model_path = artifact_path(
            workspace_id=int(workspace_id),
            source_field=source_field,
            run_id=run_id,
        ).resolve()
        model_hash = save_artifact(
            WorkspaceTopicArtifact(
                workspace_id=int(workspace_id),
                source_field=source_field,
                run_id=run_id,
                artifact_version=int(run_row["artifact_version"]),
                reducer=reducer,
                topic_model=result.topic_model,
                embedding_model=corpus.embedding_model,
                embedding_model_version=corpus.model_version,
                preprocessing_version=corpus.preprocessing_version,
            ),
            model_path,
        )
        topic_count, assignment_count = _persist_final_topics(
            run_id=run_id,
            candidate_id=candidate_id,
            selected_by=selected_by,
            corpus=corpus,
            reduced_matrix=reduced_matrix,
            result=result,
            selected_score=float(candidate_row["score"]),
            model_path=model_path,
            model_hash=model_hash,
        )
    except Exception as exc:
        _mark_run_failed(run_id, exc)
        raise

    return FinalizationSummary(
        run_id=run_id,
        candidate_id=candidate_id,
        candidate_type=str(candidate_row["candidate_type"]),
        selected_k=selected_k,
        topic_count=topic_count,
        assignment_count=assignment_count,
        status="completed",
    )


def _persist_calibration(
    *,
    run_id: int,
    scan_results: list[KScanResult],
    candidates: list[CandidateProfile],
) -> list[dict[str, Any]]:
    """以單一 transaction 保存七組掃描與三組前端候選。"""
    persisted_candidates: list[dict[str, Any]] = []
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM derived_layer.topic_candidates WHERE run_id = %s", (run_id,))
            for candidate in candidates:
                item = candidate.result
                cur.execute(
                    """
                    INSERT INTO derived_layer.topic_candidates (
                        run_id, candidate_type, candidate_k,
                        coherence, diversity, balance, score, parameters_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING candidate_id
                    """,
                    (
                        run_id,
                        candidate.candidate_type,
                        item.k,
                        item.coherence,
                        item.diversity,
                        item.balance,
                        item.score,
                        Jsonb(
                            {
                                "small_topic_ratio": item.small_topic_ratio,
                                "topic_count": item.topic_count,
                                "elapsed_seconds": item.elapsed_seconds,
                            }
                        ),
                    ),
                )
                persisted_candidates.append(
                    {
                        "candidate_id": int(cur.fetchone()[0]),
                        **candidate.to_dict(),
                    }
                )
            cur.execute(
                """
                UPDATE derived_layer.topic_runs
                SET status = 'needs_review',
                    metrics_json = %s,
                    error_message = NULL
                WHERE run_id = %s
                """,
                (Jsonb({"k_scan": [item.to_dict() for item in scan_results]}), run_id),
            )
    return persisted_candidates


def _load_run_and_candidate(*, run_id: int, candidate_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """取得待定案 run 與其候選，拒絕跨 run candidate。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM derived_layer.topic_runs WHERE run_id = %s", (run_id,))
            run_row = cur.fetchone()
            if run_row is None:
                raise ValueError(f"topic run not found: {run_id}")
            if run_row["status"] not in {"needs_review", "failed"}:
                raise ValueError(f"topic run cannot be finalized from status={run_row['status']}")
            cur.execute(
                "SELECT * FROM derived_layer.topic_candidates WHERE candidate_id = %s AND run_id = %s",
                (candidate_id, run_id),
            )
            candidate_row = cur.fetchone()
            if candidate_row is None:
                raise ValueError("candidate does not belong to the requested run")
    return dict(run_row), dict(candidate_row)


def _persist_final_topics(
    *,
    run_id: int,
    candidate_id: int,
    selected_by: str,
    corpus: ClusteringCorpus,
    reduced_matrix: ReducedEmbeddingMatrix,
    result: Any,
    selected_score: float,
    model_path: Path,
    model_hash: str,
) -> tuple[int, int]:
    """保存 workspace 永久 topics、原始 assignments 與可重用 artifact。"""
    topics = [int(value) for value in result.topics]
    topic_ids = sorted(topic_id for topic_id in set(topics) if topic_id != -1)
    top_terms = {
        topic_id: [term for term, _ in (result.topic_model.get_topic(topic_id) or [])[:10]]
        for topic_id in topic_ids
    }
    coherence_scores = topic_cv_coherence_per_topic(
        corpus.documents,
        topics=topics,
        top_terms=top_terms,
    )
    vectors = np.asarray(reduced_matrix.vectors, dtype=float)
    # BERTopic 可能依頻率重排 topic ID，不能直接拿它索引 KMeans 原始 centroid。
    centers = {
        topic_id: vectors[[index for index, assigned in enumerate(topics) if assigned == topic_id]].mean(axis=0)
        for topic_id in topic_ids
    }
    distances = [
        float(np.linalg.norm(vectors[index] - centers[topic_id])) if topic_id != -1 else math.inf
        for index, topic_id in enumerate(topics)
    ]

    db_topic_ids: dict[int, int] = {}
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT workspace_id, source_field FROM derived_layer.topic_runs WHERE run_id = %s",
                (run_id,),
            )
            run_scope = cur.fetchone()
            if run_scope is None or run_scope[0] is None:
                raise ValueError("workspace run not found while persisting topics")
            workspace_id = int(run_scope[0])
            source_field = str(run_scope[1])
            cur.execute(
                """
                SELECT count(*)
                FROM derived_layer.topics
                WHERE workspace_id = %s AND source_field = %s
                """,
                (workspace_id, source_field),
            )
            if int(cur.fetchone()[0]) > 0:
                raise ValueError("workspace source already has topics; use incremental or merge")
            cur.execute(
                """
                UPDATE derived_layer.topic_candidates
                SET is_selected = false, selected_by = NULL, selected_at = NULL
                WHERE run_id = %s
                """,
                (run_id,),
            )
            cur.execute(
                """
                UPDATE derived_layer.topic_candidates
                SET is_selected = true, selected_by = %s, selected_at = now()
                WHERE run_id = %s AND candidate_id = %s
                """,
                (selected_by, run_id, candidate_id),
            )
            if cur.rowcount != 1:
                raise ValueError("selected candidate was not updated")

            for position, topic_id in enumerate(topic_ids, start=1):
                indexes = [index for index, assigned in enumerate(topics) if assigned == topic_id]
                representative_indexes = sorted(
                    indexes,
                    key=lambda index: distances[index],
                )[:REPRESENTATIVE_DOC_LIMIT_FOR_LLM]
                keywords = [
                    {"term": term, "weight": float(weight)}
                    for term, weight in (result.topic_model.get_topic(topic_id) or [])[:10]
                ]
                cur.execute(
                    """
                    INSERT INTO derived_layer.topics (
                        workspace_id, source_field, created_run_id, topic_code,
                        model_topic_ids, topic_kind, doc_count, coherence,
                        diversity, balance, keywords_json,
                        representative_patent_ids_json, label, label_source,
                        display_order, status
                    ) VALUES (
                        %s, %s, %s, %s, %s, 'model', %s, %s,
                        %s, %s, %s, %s, %s, 'fallback', %s, 'active'
                    )
                    RETURNING topic_id
                    """,
                    (
                        workspace_id,
                        source_field,
                        run_id,
                        f"T{position:03d}",
                        [topic_id],
                        len(indexes),
                        coherence_scores.get(topic_id),
                        float(result.metrics.get("diversity", 0.0)),
                        float(result.metrics.get("balance", 0.0)),
                        Jsonb(keywords),
                        Jsonb([corpus.patent_ids[index] for index in representative_indexes]),
                        " / ".join(term["term"] for term in keywords[:3]) or f"Topic {position}",
                        position,
                    ),
                )
                db_topic_ids[topic_id] = int(cur.fetchone()[0])

            # 系統桶是正式 UI 選項，但不占模型 topic_count，也沒有 model topic ID。
            for offset, (topic_code, topic_kind, label) in enumerate(
                (
                    ("UNCLASSIFIED", "unclassified", "未分類"),
                    ("OTHER", "other", "其他"),
                ),
                start=len(topic_ids) + 1,
            ):
                cur.execute(
                    """
                    INSERT INTO derived_layer.topics (
                        workspace_id, source_field, created_run_id, topic_code,
                        topic_kind, label, label_source, display_order, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'fallback', %s, 'active')
                    """,
                    (workspace_id, source_field, run_id, topic_code, topic_kind, label, offset),
                )

            assignment_rows = [
                (
                    workspace_id,
                    source_field,
                    corpus.patent_ids[index],
                    db_topic_ids[topic_id],
                    run_id,
                    distances[index],
                )
                for index, topic_id in enumerate(topics)
                if topic_id != -1
            ]
            cur.executemany(
                """
                INSERT INTO derived_layer.topic_assignments (
                    workspace_id, source_field, patent_id, topic_id,
                    assigned_run_id, distance_to_centroid
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                assignment_rows,
            )
            cur.execute(
                """
                UPDATE derived_layer.topic_runs
                SET status = 'completed',
                    topic_count = %s,
                    metrics_json = metrics_json || %s,
                    model_artifact_path = %s,
                    model_artifact_hash = %s,
                    error_message = NULL,
                    completed_at = now(),
                    updated_at = now()
                WHERE run_id = %s
                """,
                (
                    len(topic_ids),
                    Jsonb({"selected_result": {**result.metrics, "score": selected_score}}),
                    str(model_path),
                    model_hash,
                    run_id,
                ),
            )
    return len(topic_ids), len(assignment_rows)


def _mark_run_running(run_id: int) -> None:
    """在耗時計算前把 run 標成 running 並清除前次錯誤。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE derived_layer.topic_runs SET status='running', error_message=NULL WHERE run_id=%s",
                (run_id,),
            )


def _mark_run_failed(run_id: int, error: Exception) -> None:
    """任何計算或寫入錯誤都保留 run 並記錄失敗原因。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE derived_layer.topic_runs
                SET status='failed', error_message=%s, completed_at=now()
                WHERE run_id=%s
                """,
                (str(error)[:4000], run_id),
            )


def _normalize_values(values: list[float], *, higher_is_better: bool) -> list[float]:
    """把單一指標正規化到 0..1，供七組 k 的相對排序使用。"""
    if not values:
        return []
    minimum = min(values)
    maximum = max(values)
    if math.isclose(minimum, maximum):
        return [1.0 for _ in values]
    normalized = [(value - minimum) / (maximum - minimum) for value in values]
    return normalized if higher_is_better else [1.0 - value for value in normalized]


def parse_args() -> argparse.Namespace:
    """解析第一層校準與候選定案兩種命令。"""
    parser = argparse.ArgumentParser(description="Run top-level BERTopic clustering from DB embeddings.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    calibrate = subparsers.add_parser("calibrate", help="scan k=10..40 and persist three candidates")
    calibrate.add_argument("--scope", choices=("global", "workspace"), default="global")
    calibrate.add_argument("--workspace-id", type=int)
    calibrate.add_argument("--source-field", default=SOURCE_FIELD_WIPS_INDEPENDENT_CLAIMS)
    calibrate.add_argument("--batch-size", type=int, default=8)
    calibrate.add_argument("--kmeans-batch-size", type=int, default=128)

    finalize = subparsers.add_parser("finalize", help="accept one candidate and persist top-level topics")
    finalize.add_argument("--run-id", type=int, required=True)
    finalize.add_argument("--candidate-id", type=int, required=True)
    finalize.add_argument("--selected-by", required=True)
    finalize.add_argument("--batch-size", type=int, default=8)
    finalize.add_argument("--kmeans-batch-size", type=int, default=128)

    candidate_payload = subparsers.add_parser(
        "candidate-payload",
        help="export candidate review data for Claude CLI",
    )
    candidate_payload.add_argument("--run-id", type=int, required=True)

    apply_candidate_explanations = subparsers.add_parser(
        "apply-candidate-explanations",
        help="persist Claude CLI generated candidate explanations",
    )
    apply_candidate_explanations.add_argument("--run-id", type=int, required=True)
    apply_candidate_explanations.add_argument("--input-json", type=Path, required=True)

    topic_payload = subparsers.add_parser(
        "topic-labeling-payload",
        help="export topic keyword and representative patent data for Claude CLI",
    )
    topic_payload.add_argument("--workspace-id", type=int, required=True)
    topic_payload.add_argument("--source-field", default=SOURCE_FIELD_WIPS_INDEPENDENT_CLAIMS)
    topic_payload.add_argument("--topic-id", dest="topic_ids", type=int, action="append")

    apply_labels = subparsers.add_parser(
        "apply-topic-labels",
        help="persist Claude CLI generated topic labels and summaries",
    )
    apply_labels.add_argument("--workspace-id", type=int, required=True)
    apply_labels.add_argument("--source-field", default=SOURCE_FIELD_WIPS_INDEPENDENT_CLAIMS)
    apply_labels.add_argument("--input-json", type=Path, required=True)
    apply_labels.add_argument("--updated-by", default="claude-cli")

    backfill = subparsers.add_parser(
        "backfill-representatives",
        help="backfill representative patents of old topics up to the 15-doc limit",
    )
    backfill.add_argument("--workspace-id", type=int, required=True)
    backfill.add_argument("--source-field", default=SOURCE_FIELD_WIPS_INDEPENDENT_CLAIMS)
    return parser.parse_args()


def main() -> None:
    """執行 CLI 並輸出可供後端或人工驗收的 JSON。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # gensim 的 c_v 會逐千筆輸出滑動視窗進度；正式 log 只保留管線階段與指標。
    logging.getLogger("gensim").setLevel(logging.WARNING)
    args = parse_args()
    if args.command == "calibrate":
        if args.scope == "workspace" and args.workspace_id is None:
            raise ValueError("workspace scope requires --workspace-id")
        if args.scope == "global" and args.workspace_id is not None:
            raise ValueError("global scope must not include --workspace-id")
        summary = calibrate_top_level(
            workspace_id=args.workspace_id,
            source_field=args.source_field,
            batch_size=args.batch_size,
            kmeans_batch_size=args.kmeans_batch_size,
        )
    elif args.command == "finalize":
        summary = finalize_top_level(
            run_id=args.run_id,
            candidate_id=args.candidate_id,
            selected_by=args.selected_by,
            batch_size=args.batch_size,
            kmeans_batch_size=args.kmeans_batch_size,
        )
    elif args.command == "candidate-payload":
        from .workspace_service import candidate_review_payload

        payload = candidate_review_payload(args.run_id)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    elif args.command == "apply-candidate-explanations":
        from .workspace_service import apply_candidate_explanations

        explanations = json.loads(args.input_json.read_text(encoding="utf-8"))
        if isinstance(explanations, dict):
            explanations = explanations.get("explanations", [])
        result = apply_candidate_explanations(
            run_id=args.run_id,
            explanations=explanations,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    elif args.command == "topic-labeling-payload":
        from .workspace_service import topic_labeling_payload

        payload = topic_labeling_payload(
            workspace_id=args.workspace_id,
            source_field=args.source_field,
            topic_ids=args.topic_ids,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    elif args.command == "apply-topic-labels":
        from .workspace_service import apply_topic_labels

        labels = json.loads(args.input_json.read_text(encoding="utf-8"))
        if isinstance(labels, dict):
            labels = labels.get("labels", [])
        result = apply_topic_labels(
            workspace_id=args.workspace_id,
            source_field=args.source_field,
            labels=labels,
            updated_by=args.updated_by,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    elif args.command == "backfill-representatives":
        from .workspace_service import backfill_representative_patents

        result = backfill_representative_patents(
            workspace_id=args.workspace_id,
            source_field=args.source_field,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    else:
        raise ValueError(f"unsupported command: {args.command}")
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
