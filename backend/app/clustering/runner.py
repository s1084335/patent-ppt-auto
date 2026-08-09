"""第一層 BERTopic 正式管線：DB corpus、k 掃描、候選與定案落庫。

0021 落點（併表後唯一事實來源，與 PostgresTopicStateRepository 讀取語意一致）：
- run 本體：derived_layer.topic_runs（run_id/workflow_run_id/previous_run_id/
  source_field/topic_state_json/artifact_key）。已無 workspace_id/status/run_mode
  等欄，run 歸屬與狀態一律經 workflow_run_id JOIN app_layer.workflow_runs 取得。
- 候選方案：topic_runs.topic_state_json->'candidates'（0021 檔頭明示 candidates 併入
  topic_state_json）。不寫 legacy_0021.topic_candidates：該表 run_id FK 指向 legacy_0021.topic_runs
  這個凍結 archive，新 run 不在其中，寫入必先在 archive 補影子列＝復活已退役的表。
  candidate_id 於 run 內由 1 起編號。
- 正式主題：topic_runs.topic_state_json->'topics'；assignments：derived_layer.topic_assignments
  （(run_id,patent_id) 一列，topic_key=topic_code）。
- run 進度與錯誤：寫 app_layer.workflow_runs.status/worker_state_json，不再寫 topic_runs。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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
from backend.app.transforms.patent_numbers import display_number_sql

from .model import (
    EmbeddingMatrix,
    ModelConfig,
    REPRESENTATIVE_DOC_LIMIT_FOR_LLM,
    ReducedEmbeddingMatrix,
    fit_bertopic,
    fit_incremental_pca,
    reduce_with_incremental_pca,
    topic_cv_coherence_per_topic,
)
from .artifacts import WorkspaceTopicArtifact, artifact_key, artifact_path, save_artifact
from .engine import format_topic_code
from .preprocessing import clean_patent_text, sha256_text
from .sources import SOURCE_FIELD_TECHNICAL, get_source_spec


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_FIELD_WIPS_INDEPENDENT_CLAIMS = SOURCE_FIELD_TECHNICAL
PCA_COMPONENTS = 100
# 分群文件數下限（2026-07-27 使用者定：50→30）。
# 實機動因：滑雪機 60 筆專利，但各通道**可用文件數**不足 50——技術（獨立項）40、
# 功效（效果摘要）49，兩通道都被舊門檻擋下。可用數＜專利數，因為不是每筆都有該欄位。
# ⚠ 低於本門檻改由 AI 提主題草稿、使用者定案（罕用備案），不走 BERTopic。
MIN_CLUSTERING_DOCUMENTS = 30
CANDIDATE_REFERENCE_PARAMETER_KEY = "_representative_document_references"

# ⚠ 分析用 workspace 取成員的 SQL 收口（單一 %s＝workspace_id）：
# 展開 workspaces.patent_ids_json 為 patent_id，並**扣除不相干排除清單**
# （0035 derived_layer.workspace_excluded_patents），與 clustering/exclusions.py
# analysis_member_patent_ids 為同一收口語意（Python 讀取點走該函式，SQL 讀取點走此常數）：
# - 一般 workspace：扣除排除清單（NOT EXISTS）。
# - **全庫 workspace 不扣除**（規格第 62-64 行）：排除是 workspace 級，同一 patent_id 在特定
#   ws 被排除、在全庫仍照常。故 `w.is_global` 為真時整段 OR 恆真，不扣任何專利。
# 供 load_clustering_corpus 內嵌，亦供收口正確性測試直接執行（單一事實來源、不各自複製 SQL）。
ANALYSIS_MEMBER_SUBQUERY = """
    SELECT member.patent_id
    FROM app_layer.workspaces w
    CROSS JOIN LATERAL (
        SELECT (jsonb_array_elements_text(w.patent_ids_json))::bigint AS patent_id
    ) member
    WHERE w.workspace_id = %s
      AND (
          w.is_global
          OR NOT EXISTS (
              SELECT 1 FROM derived_layer.workspace_excluded_patents ex
              WHERE ex.workspace_id = w.workspace_id
                AND ex.patent_id = member.patent_id
                AND ex.status = 'excluded'
          )
      )
"""

# CLI 直接執行時載入專案 .env；容器正式部署仍可用環境變數覆蓋。
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _ensure_workflow_run(
    conn: Any, *, workspace_id: int | None, source_field: str, run_type: str
) -> int:
    """沒有既有 job 時補建一筆 app_layer.workflow_runs，回傳其 run_id。

    worker 走佇列時 job 本身就是 workflow_run（job_id＝run_id），由呼叫端帶入；
    CLI／舊版 API 直呼分群時沒有 job，需自行補建才能滿足 topic_runs.workflow_run_id NOT NULL。
    """
    row = conn.execute(
        "INSERT INTO app_layer.workflow_runs (workspace_id, run_type, status, request_json) "
        "VALUES (%s, %s, 'running', %s) RETURNING run_id",
        (workspace_id, run_type, Jsonb({"source_field": source_field})),
    ).fetchone()
    return int(row[0])


def _next_topic_run_id(conn: Any) -> int:
    """topic_runs.run_id 非 identity 欄，需自行取號（0021 保留舊 run_id 值域）。"""
    row = conn.execute("SELECT COALESCE(max(run_id), 0) + 1 FROM derived_layer.topic_runs").fetchone()
    return int(row[0])


def create_topic_run(
    *,
    workflow_run_id: int,
    source_field: str,
    state: dict[str, Any] | None = None,
    previous_run_id: int | None = None,
    connection: Any = None,
) -> int:
    """建立 derived_layer.topic_runs 一列（0021：必帶 workflow_run_id），回傳 run_id。"""

    def _create(conn: Any) -> int:
        run_id = _next_topic_run_id(conn)
        conn.execute(
            """
            INSERT INTO derived_layer.topic_runs (
                run_id, workflow_run_id, previous_run_id, source_field, topic_state_json
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (run_id, workflow_run_id, previous_run_id, source_field, Jsonb(state or {})),
        )
        return run_id

    if connection is not None:
        return _create(connection)
    with psycopg.connect(**get_connection_kwargs()) as conn:
        return _create(conn)


def load_run_scope(run_id: int, *, connection: Any = None) -> dict[str, Any]:
    """取 run 歸屬與狀態：workspace_id/status 來自 workflow_runs JOIN（0021 已無這兩欄）。"""

    def _load(conn: Any) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT tr.run_id, tr.workflow_run_id, tr.previous_run_id, tr.source_field,
                   tr.topic_state_json, tr.artifact_key,
                   wr.workspace_id, wr.status
            FROM derived_layer.topic_runs tr
            JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
            WHERE tr.run_id = %s
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"topic run not found: {run_id}")
        return dict(row)

    if connection is not None:
        return _load(connection)
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        return _load(conn)


def _merge_topic_state(conn: Any, run_id: int, patch: dict[str, Any]) -> None:
    """就地合併 topic_state_json（jsonb ||），不覆蓋既有其他鍵。"""
    conn.execute(
        "UPDATE derived_layer.topic_runs SET topic_state_json = topic_state_json || %s WHERE run_id = %s",
        (Jsonb(patch), run_id),
    )


def _set_workflow_status(conn: Any, run_id: int, status: str, *, error: str | None = None) -> None:
    """更新該 topic run 對應 workflow_run 的狀態（0021：status 只在 workflow_runs）。"""
    conn.execute(
        """
        UPDATE app_layer.workflow_runs wr
        SET status = %s,
            worker_state_json = CASE WHEN %s::text IS NULL
                THEN wr.worker_state_json - 'error_message'
                ELSE wr.worker_state_json || jsonb_build_object('error_message', %s::text) END
        FROM derived_layer.topic_runs tr
        WHERE tr.workflow_run_id = wr.run_id AND tr.run_id = %s
        """,
        (status, error, error, run_id),
    )


def persist_final_topics(
    *,
    run_id: int,
    topics: list[dict[str, Any]],
    assignments: list[tuple[int, str, float | None]],
    metrics: dict[str, Any] | None = None,
    artifact_key: str | None = None,
) -> tuple[int, int]:
    """把正式主題寫進 topic_state_json、assignments 寫進 derived_layer.topic_assignments。

    落點與 PostgresTopicStateRepository 讀取語意一致：topics 掛 topic_state_json->'topics'，
    每筆 (run_id, patent_id) 一列 assignment、topic_key 存 topic_code。
    assignments 以 executemany 批次寫入，不逐筆往返。
    """
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            patch: dict[str, Any] = {"topics": topics}
            if metrics is not None:
                patch["metrics"] = metrics
            cur.execute(
                """
                UPDATE derived_layer.topic_runs
                SET topic_state_json = topic_state_json || %s,
                    artifact_key = COALESCE(%s, artifact_key)
                WHERE run_id = %s
                """,
                (Jsonb(patch), artifact_key, run_id),
            )
            if cur.rowcount != 1:
                raise ValueError(f"topic run not found: {run_id}")
            cur.execute("DELETE FROM derived_layer.topic_assignments WHERE run_id = %s", (run_id,))
            if assignments:
                cur.executemany(
                    """
                    INSERT INTO derived_layer.topic_assignments (
                        run_id, patent_id, topic_key, distance_to_centroid
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    [(run_id, patent_id, topic_key, distance)
                     for patent_id, topic_key, distance in assignments],
                )
    return len(topics), len(assignments)


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
    references: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self, *, include_references: bool = False) -> dict[str, Any]:
        """轉成 DB metrics JSON 與 CLI 輸出的字典，預設不輸出內部代表文件參照。"""
        payload = asdict(self)
        if not include_references:
            payload.pop("references", None)
        return payload


@dataclass(frozen=True)
class CandidateProfile:
    """保存要送到前端的保守、平衡或細分候選。"""

    candidate_type: str
    result: KScanResult

    def to_dict(self) -> dict[str, Any]:
        """輸出候選類型與完整 k 指標，但不把內部代表文件參照重複塞進 summary。"""
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
    """回報使用者選定候選後第一層 topic 與 assignment 筆數。

    ⚠ workspace_id／source_field 是後續自動接續 job 的必要資訊：
    handlers 的 `_enqueue_irrelevant_filter`／`_enqueue_topic_label` 都從 summary
    取這兩欄。2026-07-27 前缺 workspace_id，導致 irrelevant_filter 每次靜默 return
    （DB 歷來 0 筆），2026-07-24 定案的「分群完成自動接續」實際從未運作。
    """

    run_id: int
    candidate_id: int
    candidate_type: str
    selected_k: int
    topic_count: int
    assignment_count: int
    status: str
    workspace_id: int | None = None
    source_field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """轉成 CLI 可直接輸出的 JSON payload。"""
        return asdict(self)


def _insufficient_documents_message(document_count: int, source_field: str | None) -> str:
    """文件數不足時的可讀訊息（2026-07-27 使用者實機看不懂原訊息而改）。

    原訊息只有「clustering requires at least N documents」——使用者無從得知
    是「專利本來就少」還是「專利夠但該欄位多半是空的」。實機正是後者：
    60 筆專利，技術通道只有 40 筆有「獨立項」、功效只有 49 筆有「效果摘要」。
    故訊息要指明：**哪個通道、實際幾筆、缺哪個欄位、怎麼辦**。
    """
    if source_field:
        try:
            spec = get_source_spec(source_field)
            return (
                f"{spec.label_zh}通道可分群文件數不足："
                f"僅 {document_count} 筆有「{spec.source_column}」內容，"
                f"未達分群下限 {MIN_CLUSTERING_DOCUMENTS} 筆。"
                "請補充該欄位有內容的專利，或確認匯入來源是否缺此欄。"
            )
        except Exception:  # noqa: BLE001 - 未知通道時退回通用訊息，不讓錯誤訊息自己炸
            pass
    return (
        f"可分群文件數不足：僅 {document_count} 筆，"
        f"未達分群下限 {MIN_CLUSTERING_DOCUMENTS} 筆。"
    )


def top_level_k_values(
    document_count: int, *, source_field: str | None = None
) -> tuple[int, ...]:
    """依 workspace 可用文件數回傳階梯式 k；小資料不測不合理的大主題數。

    source_field 只用於文件數不足時的錯誤訊息（指明哪個通道、缺哪個欄位），
    不影響 k 的計算——k 只看數量。

    2026-07-27 使用者定案：門檻 50→30，並為 30–49 這段另給更小的 k。
    ⚠ 單純降門檻不夠——30–49 若沿用「<100 → k=(5,10)」，40 篇分 10 群每群才 4 篇，
    主題零碎到沒有分析價值，那正是原本設 50 門檻要避免的情況。
    故 30–49 掃 k=(3,5,8)：3 群約 10–16 篇、5 群約 6–10 篇、8 群約 4–6 篇，
    讓使用者在粗／中／細之間依實際內容挑。
    """
    if document_count < MIN_CLUSTERING_DOCUMENTS:
        raise ValueError(_insufficient_documents_message(document_count, source_field))
    if document_count < 50:
        return (3, 5, 8)
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

    # 0021：app_layer.workspace_patents 已併入 workspaces.patent_ids_json，
    # workspace 範圍改用該 JSON 陣列展開後 JOIN，不再有成員關聯表。
    # 分群是分析用取成員，須扣除不相干排除清單——SQL 收口於 ANALYSIS_MEMBER_SUBQUERY
    # （見該常數說明；與 clustering/exclusions.analysis_member_patent_ids 同語意）。
    workspace_join = ""
    parameters: tuple[Any, ...] = ()
    if workspace_id is not None:
        workspace_join = f"""
            JOIN ({ANALYSIS_MEMBER_SUBQUERY}) wp ON wp.patent_id = p.id
        """
        parameters = (workspace_id,)

    # 顯示號鏈唯一定義處＝transforms.patent_numbers（2026-08-04 治本收斂）。
    # 本查詢走 psycopg sql.SQL 的 {} 佔位，故以字串串接嵌入靜態鏈，不用 f-string。
    query = sql.SQL(
        """
        SELECT
            p.id AS patent_id,
            p.{source_column} AS source_text,
            """
        + display_number_sql("p")
        + """ AS patent_number,
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
    k_values = k_values or top_level_k_values(
        len(corpus.documents), source_field=source_field)
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
            references=select_calibration_references(
                corpus=corpus,
                topics=[int(topic) for topic in result.topics],
                representative_doc_indices=result.representative_doc_indices,
            ),
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


def select_calibration_references(
    *,
    corpus: ClusteringCorpus,
    topics: list[int],
    representative_doc_indices: dict[int, list[int]],
    per_topic_limit: int = REPRESENTATIVE_DOC_LIMIT_FOR_LLM,
) -> list[dict[str, Any]]:
    """把 BERTopic 每個 topic 的 c-TF-IDF 前 N 筆轉成可追溯專利參照。"""
    document_count = len(corpus.documents)
    if not (
        document_count
        == len(corpus.patent_ids)
        == len(corpus.matrix.patent_numbers)
        == len(topics)
    ):
        raise ValueError("candidate reference inputs must have the same length")
    if per_topic_limit < 1:
        raise ValueError("candidate per-topic reference limit must be positive")

    assigned_topic_ids = sorted({topic_id for topic_id in topics if topic_id != -1})
    if set(representative_doc_indices) != set(assigned_topic_ids):
        raise ValueError("representative document topics do not match candidate assignments")

    references: list[dict[str, Any]] = []
    used_indexes: set[int] = set()
    for topic_id in assigned_topic_ids:
        for rank, index in enumerate(
            representative_doc_indices[topic_id][:per_topic_limit], start=1
        ):
            if index < 0 or index >= document_count:
                raise ValueError(f"representative document index out of range: {index}")
            if topics[index] != topic_id:
                raise ValueError("representative document does not belong to its topic")
            if index in used_indexes:
                raise ValueError("representative document index appears in multiple topics")
            used_indexes.add(index)
            references.append(
                {
                    "patent_id": int(corpus.patent_ids[index]),
                    "patent_number": corpus.matrix.patent_numbers[index],
                    "model_topic_id": int(topic_id),
                    "rank": rank,
                    "text_hash": sha256_text(corpus.documents[index]),
                }
            )
    return references


def calculate_assignment_centroid_distances(
    *,
    vectors: list[list[float]],
    topics: list[int],
) -> list[float]:
    """計算 assignment 診斷用 centroid 距離，不參與代表文檔選取。"""
    matrix = np.asarray(vectors, dtype=float)
    if len(matrix) != len(topics):
        raise ValueError("reduced vectors and topic assignments must have the same length")

    topic_ids = sorted(topic_id for topic_id in set(topics) if topic_id != -1)
    centers = {
        topic_id: matrix[
            [index for index, assigned in enumerate(topics) if assigned == topic_id]
        ].mean(axis=0)
        for topic_id in topic_ids
    }
    return [
        float(np.linalg.norm(matrix[index] - centers[topic_id]))
        if topic_id != -1
        else math.inf
        for index, topic_id in enumerate(topics)
    ]


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
    workflow_run_id: int | None = None,
) -> CalibrationSummary:
    """建立正式 run，掃描七組 k 並將三組候選寫入 DB 等待使用者選擇。

    workflow_run_id：worker 走佇列時帶入該 job 的 run_id（job_id＝workflow_runs.run_id）；
    CLI／舊版 API 直呼時留空，由 _ensure_workflow_run 補建一筆 workflow_run。
    """
    scope = "workspace" if workspace_id is not None else "global"
    with psycopg.connect(**get_connection_kwargs()) as conn:
        corpus = load_clustering_corpus(conn, workspace_id=workspace_id, source_field=source_field)
        k_values = top_level_k_values(
            len(corpus.documents), source_field=source_field)
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
        # 0021：run 歸屬走 workflow_run_id；run_mode/status/input_doc_count 併入 topic_state_json
        effective_workflow_run_id = workflow_run_id
        if effective_workflow_run_id is None:
            effective_workflow_run_id = _ensure_workflow_run(
                conn, workspace_id=workspace_id, source_field=source_field,
                run_type="clustering_calibrate",
            )
        run_id = create_topic_run(
            workflow_run_id=effective_workflow_run_id,
            source_field=source_field,
            state={
                "run_mode": "full",
                "input_doc_count": len(corpus.documents),
                "parameters": parameters,
            },
            connection=conn,
        )

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
        model_key = artifact_key(
            workspace_id=int(workspace_id),
            source_field=source_field,
            run_id=run_id,
        )
        model_path = artifact_path(
            workspace_id=int(workspace_id),
            source_field=source_field,
            run_id=run_id,
        )
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
            model_artifact_key=model_key,
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
        # 供 handlers 自動接續 ai:irrelevant_filter／ai:topic_label 使用
        workspace_id=int(workspace_id),
        source_field=source_field,
    )


def _persist_calibration(
    *,
    run_id: int,
    scan_results: list[KScanResult],
    candidates: list[CandidateProfile],
) -> list[dict[str, Any]]:
    """以單一 transaction 保存七組掃描與三組前端候選（0021：候選落 topic_state_json）。

    candidate_id 於 run 內由 1 起編號，供 finalize 指定；一次 UPDATE 寫完整份候選，
    不逐筆 INSERT 往返。
    """
    persisted_candidates = [
        {
            "candidate_id": index,
            "run_id": run_id,
            "is_selected": False,
            "selected_by": None,
            "selected_at": None,
            "llm_explanation": None,
            "parameters": {
                "small_topic_ratio": candidate.result.small_topic_ratio,
                "topic_count": candidate.result.topic_count,
                "elapsed_seconds": candidate.result.elapsed_seconds,
                CANDIDATE_REFERENCE_PARAMETER_KEY: candidate.result.references,
            },
            **candidate.to_dict(),
            "candidate_k": candidate.result.k,
        }
        for index, candidate in enumerate(candidates, start=1)
    ]
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            # 0021：候選、狀態與 k_scan 全部併入 topic_state_json；workflow_runs 只記 job 狀態
            _merge_topic_state(
                cur, run_id,
                {
                    "candidates": persisted_candidates,
                    "status": "needs_review",
                    "metrics": {"k_scan": [item.to_dict() for item in scan_results]},
                },
            )
            _set_workflow_status(cur, run_id, "succeeded")
    return persisted_candidates


def _load_run_and_candidate(*, run_id: int, candidate_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """取得待定案 run 與其候選，拒絕跨 run candidate。

    0021：run 的 workspace_id 由 workflow_runs JOIN 取得（load_run_scope）；分群自身的
    needs_review 狀態與候選都存在 topic_state_json。
    """
    run_row = load_run_scope(run_id)
    state = dict(run_row.get("topic_state_json") or {})
    clustering_status = state.get("status")
    if clustering_status not in FINALIZABLE_STATUSES:
        raise ValueError(f"topic run cannot be finalized from status={clustering_status}")
    run_row["input_doc_count"] = state.get("input_doc_count")
    run_row["artifact_version"] = state.get("artifact_version", 1)
    candidate_row = next(
        (c for c in (state.get("candidates") or []) if c.get("candidate_id") == candidate_id),
        None,
    )
    if candidate_row is None:
        raise ValueError("candidate does not belong to the requested run")
    return run_row, dict(candidate_row)


# 可以 finalize 的 clustering_status（唯一來源）。
# 2026-07-28 加入 completed：使用者要能改選其他候選方案（同一 run 想改幾次就改幾次）。
# ⚠ 這組值原本在三處各寫一份（api/clustering.py 的 409 守門、runner._load_run_and_candidate、
# 前端 index.html 的 finalized 判斷），是本專案反覆出現的「同一規則多處實作」。
# 後端兩處已收口到此常數；前端另有一份判斷，改動時兩邊都要看。
FINALIZABLE_STATUSES = frozenset({"needs_review", "failed", "completed"})


def can_refinalize(scope: dict[str, Any]) -> bool:
    """該 run 能否再次 finalize（＝使用者改選其他候選方案）。

    2026-07-28 使用者定案：**同一個 run 內想改幾次就改幾次**。原本 `_persist_final_topics`
    看到已有 topics 就 raise，使得第一次選定後無法反悔——而畫面文案卻寫「要改用其他分法，
    請按上方『分類』重跑一次」，那條路實際走 incremental（只處理新專利、無新資料時等於空跑，
    artifact 遺失時更直接 FileNotFoundError），承諾了做不到的操作。

    候選資料本來就完整保留在 `topic_state_json.candidates`（calibrate 一次算完 k=3／5／8），
    finalize 也本來就靠 candidate_id 指定，故重選在資料上完全成立。

    目前一律放行；保留本函式是為了讓「能不能重選」有單一判斷點——
    日後若要加條件（例如已進報表的 run 不給改），只改這裡。
    """
    return True


def clear_topic_scoped_state(run_id: int) -> None:
    """切換候選前，清掉「掛在舊主題編號上」的下游狀態。

    為什麼要清：k=3 的 T001 與 k=5 的 T001 **不是同一個主題**，沿用舊資料會張冠李戴。

    清這些（主題級）：
    - 合併／拆分產生的下游 topic_run（`previous_run_id` 指向本 run）——它們的
      merged_topic_code 指涉舊編號，切換後失去意義。
    - topic_state_json 內 AI 命名／人工改名的殘留欄位由 `_write_topic_state` 整份取代
      `topics` 陣列時一併汰換，不需另外處理。

    **不清這些（專利級）**：`workspace_excluded_patents` 的人工裁決是「這篇專利不相干」，
    與主題怎麼切分無關，切換候選不得動它——使用者的裁決不該因為改了主題數就消失。

    assignments 不在此處理：`_write_topic_state` 寫入前已 `DELETE WHERE run_id`。
    """
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            # 下游 run（合併／拆分）連同其 assignments 一併移除；assignments 有 FK 先刪。
            cur.execute(
                "SELECT run_id FROM derived_layer.topic_runs WHERE previous_run_id = %s",
                (run_id,),
            )
            downstream = [row[0] for row in cur.fetchall()]
            if downstream:
                cur.execute(
                    "DELETE FROM derived_layer.topic_assignments WHERE run_id = ANY(%s)",
                    (downstream,),
                )
                cur.execute(
                    "DELETE FROM derived_layer.topic_runs WHERE run_id = ANY(%s)",
                    (downstream,),
                )


def _persist_final_topics(
    *,
    run_id: int,
    candidate_id: int,
    selected_by: str,
    corpus: ClusteringCorpus,
    reduced_matrix: ReducedEmbeddingMatrix,
    result: Any,
    selected_score: float,
    model_artifact_key: str,
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
    # 代表文檔改由 c-TF-IDF 選取；centroid 距離只保留給 assignment 診斷與後續排序。
    distances = calculate_assignment_centroid_distances(
        vectors=reduced_matrix.vectors,
        topics=topics,
    )
    # 0021：run 歸屬經 workflow_runs JOIN；已定案主題改由 topic_state_json 判斷
    scope = load_run_scope(run_id)
    if scope["workspace_id"] is None:
        raise ValueError("workspace run not found while persisting topics")
    source_field = str(scope["source_field"])
    # 2026-07-28：允許同一個 run 重選候選（原本「已有 topics 就 raise」讓選擇無法反悔）。
    # 舊 topics 與 assignments 由 _write_topic_state 自動汰換（topic_state_json 的 topics
    # 整個取代、assignments 先 DELETE WHERE run_id），此處只需清掉「掛在舊主題編號上」的
    # 下游狀態——見 clear_topic_scoped_state。
    if not can_refinalize(scope):
        raise ValueError("workspace source already has topics; use incremental or merge")
    clear_topic_scoped_state(run_id)

    # 正式主題組成 topic_state_json->'topics'；topic_code 即後續 assignments 的 topic_key
    topic_dicts: list[dict[str, Any]] = []
    code_by_model_id: dict[int, str] = {}
    for position, topic_id in enumerate(topic_ids, start=1):
        indexes = [index for index, assigned in enumerate(topics) if assigned == topic_id]
        representative_indexes = result.representative_doc_indices.get(topic_id)
        if not representative_indexes:
            raise ValueError(f"topic {topic_id} has no c-TF-IDF representative documents")
        representative_indexes = representative_indexes[:REPRESENTATIVE_DOC_LIMIT_FOR_LLM]
        keywords = [
            {"term": term, "weight": float(weight)}
            for term, weight in (result.topic_model.get_topic(topic_id) or [])[:10]
        ]
        topic_code = format_topic_code(position)
        code_by_model_id[topic_id] = topic_code
        topic_dicts.append({
            "topic_id": position,
            "topic_code": topic_code,
            "source_field": source_field,
            "created_run_id": run_id,
            "model_topic_ids": [topic_id],
            "topic_kind": "model",
            "doc_count": len(indexes),
            "coherence": coherence_scores.get(topic_id),
            "diversity": float(result.metrics.get("diversity", 0.0)),
            "balance": float(result.metrics.get("balance", 0.0)),
            "keywords": keywords,
            "representative_patent_ids": [corpus.patent_ids[i] for i in representative_indexes],
            "label": " / ".join(term["term"] for term in keywords[:3]) or f"Topic {position}",
            "label_source": "fallback",
            "display_order": position,
            "status": "active",
        })
    # 2026-07-27：移除「未分類」「其他」兩個系統桶（使用者定案）。
    # 初始與增量都用 MiniBatchKMeans（model.py 的 hdbscan_model 參數實際塞 KMeans），
    # 每個點必被指派到最近中心，不存在 HDBSCAN 的 -1 outlier——兩桶 doc_count 恆為 0，
    # 對使用者是純雜訊。剔除語意改由 workspace_excluded_patents 的「不相干」桶承接（0036）。

    assignment_rows = [
        (corpus.patent_ids[index], code_by_model_id[topic_id], distances[index])
        for index, topic_id in enumerate(topics)
        if topic_id != -1
    ]
    persist_final_topics(
        run_id=run_id,
        topics=topic_dicts,
        assignments=assignment_rows,
        metrics={"selected_result": {**result.metrics, "score": selected_score},
                 "model_artifact_hash": model_hash},
        artifact_key=model_artifact_key,
    )
    # 候選選定旗標同樣記在 topic_state_json（與候選寫入端同源）
    selected_at = datetime.now(timezone.utc).isoformat()
    updated_candidates = [
        {**candidate,
         "is_selected": candidate.get("candidate_id") == candidate_id,
         "selected_by": selected_by if candidate.get("candidate_id") == candidate_id else None,
         "selected_at": selected_at if candidate.get("candidate_id") == candidate_id else None}
        for candidate in (dict(scope.get("topic_state_json") or {}).get("candidates") or [])
    ]
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            # model_artifact_hash／artifact_version 必須落 state 頂層：incremental 端
            # 經 _latest_completed_run 攤平後直接取這兩個頂層鍵（並以 artifact_version
            # 排序取最新 run），只寫進 metrics 會讓後續增量分群 KeyError。
            _merge_topic_state(
                cur, run_id,
                {"candidates": updated_candidates, "status": "completed",
                 "topic_count": len(topic_ids), "error_message": None,
                 "model_artifact_hash": model_hash,
                 "artifact_version": int(
                     dict(scope.get("topic_state_json") or {}).get("artifact_version") or 1)},
            )
            _set_workflow_status(cur, run_id, "succeeded")
    return len(topic_ids), len(assignment_rows)


def _mark_run_running(run_id: int) -> None:
    """在耗時計算前把 run 標成 running 並清除前次錯誤（0021：狀態寫 workflow_runs）。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            _merge_topic_state(cur, run_id, {"status": "running", "error_message": None})
            _set_workflow_status(cur, run_id, "running")


def _mark_run_failed(run_id: int, error: Exception) -> None:
    """任何計算或寫入錯誤都保留 run 並記錄失敗原因（0021：狀態寫 workflow_runs）。"""
    message = str(error)[:4000]
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            _merge_topic_state(cur, run_id, {"status": "failed", "error_message": message})
            _set_workflow_status(cur, run_id, "failed", error=message)


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
