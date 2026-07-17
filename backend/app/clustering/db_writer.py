"""將既有分類模型產生的 patent-level embedding 寫入 PostgreSQL。

本檔只負責資料庫讀寫與管線串接；文本前處理仍在 ``preprocessing.py``，
PatentSBERTa embedding 演算法仍在 ``model.py``，避免產生第二套模型邏輯。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from transformers import AutoTokenizer

from backend.app.db.connection import get_connection_kwargs

from .model import (
    PATENT_SBERTA_MAX_SEQ_LENGTH,
    PATENT_SBERTA_MODEL,
    PATENT_SBERTA_VECTOR_DIM,
    DocumentEmbedding,
    ModelConfig,
    PatentNumberIdentity,
    embed_processed_texts,
    load_sentence_transformer,
    resolve_patent_number,
    validate_patent_sberta,
)
from .preprocessing import (
    TEXT_CLEANING_VERSION,
    ProcessedText,
    TextPreprocessConfig,
    add_claim_aware_chunks,
    mark_exact_duplicates,
    process_patent_text,
)
from .sources import (
    SOURCE_FIELD_TECHNICAL,
    ClusteringSourceSpec,
    get_source_spec,
)


LOGGER = logging.getLogger(__name__)
DEFAULT_LOCAL_MODEL_PATH = Path("backend/models/PatentSBERTa")
SOURCE_FIELD_WIPS_INDEPENDENT_CLAIMS = SOURCE_FIELD_TECHNICAL


@dataclass(frozen=True)
class PatentEmbeddingSource:
    """保存一筆核心專利的追蹤號碼與待嵌入獨立項。"""

    patent_id: int
    source_text: str
    identity: PatentNumberIdentity


@dataclass(frozen=True)
class EmbeddingWriteConfig:
    """集中管理正式 embedding 寫入所需的可追溯參數。"""

    source_field: str = SOURCE_FIELD_WIPS_INDEPENDENT_CLAIMS
    model_path: Path = DEFAULT_LOCAL_MODEL_PATH
    device: str = "auto"
    batch_size: int = 8
    normalize_embeddings: bool = True
    show_progress_bar: bool = True
    limit: int | None = None


@dataclass(frozen=True)
class EmbeddingWriteSummary:
    """回報本次讀取、跳過、寫入及資料表最終筆數。"""

    source_rows: int
    usable_rows: int
    skipped_rows: int
    total_chunks: int
    max_chunk_tokens: int
    would_truncate_after_chunking: int
    reused_rows: int
    upserted_rows: int
    table_rows_for_source: int
    device: str
    vector_dim: int
    embedding_model: str
    model_version: str
    source_field: str

    def to_dict(self) -> dict[str, Any]:
        """轉成 CLI 與 log 可直接輸出的字典。"""
        return asdict(self)


def fetch_embedding_sources(
    conn: psycopg.Connection[Any],
    *,
    source_field: str,
    limit: int | None = None,
) -> list[PatentEmbeddingSource]:
    """從核心專利表讀取指定來源欄位及四種專利號。"""
    spec = get_source_spec(source_field)
    if limit is not None and limit <= 0:
        raise ValueError("limit must be > 0")

    query = _embedding_source_query(spec=spec, limit=limit)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, (limit,) if limit is not None else None)
        rows = cur.fetchall()

    sources: list[PatentEmbeddingSource] = []
    for row in rows:
        identity = resolve_patent_number(row)
        sources.append(
            PatentEmbeddingSource(
                patent_id=int(row["patent_id"]),
                source_text=str(row["source_text"]),
                identity=identity,
            )
        )
    return sources


def _embedding_source_query(*, spec: ClusteringSourceSpec, limit: int | None) -> Any:
    """使用 psycopg Identifier 安全建立雙來源查詢，不接受任意表名或欄名。"""
    from psycopg import sql

    schema_name, table_name = spec.source_table.split(".", maxsplit=1)
    if spec.source_table == "core_layer.patents":
        source_join = sql.SQL("")
        source_alias = sql.Identifier("p")
    else:
        source_join = sql.SQL(
            "JOIN {source_table} s ON s.patent_id = p.id"
        ).format(source_table=sql.Identifier(schema_name, table_name))
        source_alias = sql.Identifier("s")

    limit_sql = sql.SQL(" LIMIT %s") if limit is not None else sql.SQL("")
    return sql.SQL(
        """
        SELECT
            p.id AS patent_id,
            p.country_code,
            p."授權公告號",
            p."審查的公告號",
            p."未審查的公開號",
            p."未審查的公開號(轉換後)",
            p."申請號",
            p."申請號(轉換後)",
            {source_alias}.{source_column} AS source_text
        FROM core_layer.patents p
        {source_join}
        WHERE NULLIF(BTRIM({source_alias}.{source_column}), '') IS NOT NULL
        ORDER BY p.id
        {limit_sql}
        """
    ).format(
        source_alias=source_alias,
        source_column=sql.Identifier(spec.source_column),
        source_join=source_join,
        limit_sql=limit_sql,
    )


def preprocess_embedding_sources(
    sources: list[PatentEmbeddingSource],
    *,
    tokenizer: Any,
    source_field: str = SOURCE_FIELD_WIPS_INDEPENDENT_CLAIMS,
) -> list[ProcessedText]:
    """依來源規格清理文本，長文本以 claim 或 tokenizer 安全邊界切 chunks。"""
    spec = get_source_spec(source_field)
    config = TextPreprocessConfig(chunking=True)
    processed = [
        process_patent_text(source.source_text, row_number=source.patent_id, config=config)
        for source in sources
    ]
    mark_exact_duplicates(processed)
    if spec.claim_aware_chunking:
        add_claim_aware_chunks(
            processed,
            tokenizer=tokenizer,
            max_seq_length=PATENT_SBERTA_MAX_SEQ_LENGTH,
        )
    else:
        # 效果摘要沒有多個 claim 邊界；沿用 tokenizer 安全切法，禁止靜默截斷。
        add_claim_aware_chunks(
            processed,
            tokenizer=tokenizer,
            max_seq_length=PATENT_SBERTA_MAX_SEQ_LENGTH,
        )
    truncated = [
        item.row_number
        for item in processed
        if item.status == "usable" and item.would_truncate_after_chunking
    ]
    if truncated:
        raise ValueError(f"claim-aware chunks still exceed model limit: {truncated[:10]}")
    return processed


def build_embedding_records(
    *,
    sources: list[PatentEmbeddingSource],
    processed: list[ProcessedText],
    embeddings: list[DocumentEmbedding],
) -> list[dict[str, Any]]:
    """對齊來源、前處理 metadata 與向量，組成資料庫寫入紀錄。"""
    if not (len(sources) == len(processed) == len(embeddings)):
        raise ValueError("sources, processed, and embeddings must have the same length")

    records: list[dict[str, Any]] = []
    for source, item, embedding in zip(sources, processed, embeddings, strict=True):
        if source.patent_id != item.row_number or item.row_number != embedding.row_number:
            raise ValueError("embedding rows are not aligned with core patent ids")
        if item.status != "usable":
            continue
        if embedding.vector is None or embedding.vector_dim != PATENT_SBERTA_VECTOR_DIM:
            raise ValueError(f"patent {source.patent_id} has an invalid embedding vector")

        records.append(
            {
                "source": source,
                "processed": item,
                "embedding": embedding,
                "vector_text": vector_to_pgvector(embedding.vector),
                "vector_hash": hash_embedding_vector(embedding.vector),
            }
        )
    return records


def find_pending_embedding_indices(
    conn: psycopg.Connection[Any],
    *,
    sources: list[PatentEmbeddingSource],
    processed: list[ProcessedText],
    source_field: str,
    model_version: str,
) -> tuple[list[int], int]:
    """找出 DB 尚無相同模型與文本 hash 的資料列，已存在者直接重用。"""
    if len(sources) != len(processed):
        raise ValueError("sources and processed must have the same length")

    embedding_table = get_source_spec(source_field).embedding_table
    from psycopg import sql

    schema_name, table_name = embedding_table.split(".", maxsplit=1)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
            SELECT
                patent_id,
                preprocessing_version,
                text_hash
            FROM {embedding_table}
            WHERE embedding_model = %s
              AND model_version = %s
            """
            ).format(embedding_table=sql.Identifier(schema_name, table_name)),
            (PATENT_SBERTA_MODEL, model_version),
        )
        existing_keys = {(int(row[0]), row[1], row[2]) for row in cur.fetchall()}

    pending_indices: list[int] = []
    reused_rows = 0
    for index, (source, item) in enumerate(zip(sources, processed, strict=True)):
        if item.status != "usable":
            continue
        key = (
            source.patent_id,
            TEXT_CLEANING_VERSION,
            item.model_text_hash,
        )
        if key in existing_keys:
            reused_rows += 1
        else:
            pending_indices.append(index)
    return pending_indices, reused_rows


def persist_embedding_records(
    conn: psycopg.Connection[Any],
    *,
    records: list[dict[str, Any]],
    source_field: str,
    model_version: str,
    model_runtime: dict[str, Any],
    model_config: ModelConfig,
) -> int:
    """以可重跑的 upsert 寫入向量及合併後的追溯 metadata。"""
    from psycopg import sql

    embedding_table = get_source_spec(source_field).embedding_table
    schema_name, table_name = embedding_table.split(".", maxsplit=1)
    query = sql.SQL(
        """
        INSERT INTO {embedding_table} (
            patent_id, preprocessing_version, text_hash,
            embedding_model, model_version, embedding_vector,
            chunk_count, metadata_json
        ) VALUES (
            %s, %s, %s, %s, %s, %s::vector, %s, %s
        )
        ON CONFLICT (
            patent_id, text_hash, embedding_model, model_version, preprocessing_version
        ) DO UPDATE SET
            embedding_vector = EXCLUDED.embedding_vector,
            chunk_count = EXCLUDED.chunk_count,
            metadata_json = EXCLUDED.metadata_json
        """
    ).format(embedding_table=sql.Identifier(schema_name, table_name))
    model_metadata = {
        "runtime": model_runtime,
        "config": model_config.to_dict(),
    }
    with conn.cursor() as cur:
        for record in records:
            source: PatentEmbeddingSource = record["source"]
            item: ProcessedText = record["processed"]
            embedding: DocumentEmbedding = record["embedding"]
            identity = source.identity
            metadata = item.to_dict(include_text=False)
            metadata.update(
                {
                    "aggregation_method": embedding.aggregation_method,
                    "vector_hash": record["vector_hash"],
                    "chunk_token_counts": item.chunk_token_counts or [],
                    "chunk_weights": embedding.chunk_weights,
                    "tokenizer_name": PATENT_SBERTA_MODEL,
                    "max_seq_length": PATENT_SBERTA_MAX_SEQ_LENGTH,
                    "vector_dim": embedding.vector_dim,
                    "model": model_metadata,
                }
            )
            cur.execute(
                query,
                (
                    source.patent_id,
                    TEXT_CLEANING_VERSION,
                    item.model_text_hash,
                    PATENT_SBERTA_MODEL,
                    model_version,
                    record["vector_text"],
                    embedding.chunk_count,
                    Jsonb(metadata),
                ),
            )
    return len(records)


def write_patent_embeddings(config: EmbeddingWriteConfig | None = None) -> EmbeddingWriteSummary:
    """執行 DB 讀取、chunking、PatentSBERTa 與 commit 的完整正式流程。"""
    config = config or EmbeddingWriteConfig()
    model_path = config.model_path.resolve()
    if not model_path.is_dir():
        raise FileNotFoundError(f"local PatentSBERTa model not found: {model_path}")
    model_version = resolve_model_version(model_path)

    with psycopg.connect(**get_connection_kwargs()) as conn:
        sources = fetch_embedding_sources(
            conn,
            source_field=config.source_field,
            limit=config.limit,
        )
        if not sources:
            raise ValueError(f"no source rows found for {config.source_field}")

        LOGGER.info("讀取 %d 筆 %s", len(sources), config.source_field)
        tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        processed = preprocess_embedding_sources(
            sources,
            tokenizer=tokenizer,
            source_field=config.source_field,
        )
        model_config = ModelConfig(
            embedding_model=PATENT_SBERTA_MODEL,
            batch_size=config.batch_size,
            normalize_embeddings=config.normalize_embeddings,
            show_progress_bar=config.show_progress_bar,
            device=config.device,
        )
        pending_indices, reused_rows = find_pending_embedding_indices(
            conn,
            sources=sources,
            processed=processed,
            source_field=config.source_field,
            model_version=model_version,
        )
        if pending_indices:
            # 只把 DB 尚未保存的文本送進 GPU；既有 hash 對應向量直接重用。
            pending_sources = [sources[index] for index in pending_indices]
            pending_processed = [processed[index] for index in pending_indices]
            model = load_sentence_transformer(model_path, device=config.device, local_files_only=True)
            model_runtime = validate_patent_sberta(model)
            embeddings = embed_processed_texts(
                pending_processed,
                model=model,
                config=model_config,
                patent_identities=[source.identity for source in pending_sources],
            )
            records = build_embedding_records(
                sources=pending_sources,
                processed=pending_processed,
                embeddings=embeddings,
            )
            upserted_rows = persist_embedding_records(
                conn,
                records=records,
                source_field=config.source_field,
                model_version=model_version,
                model_runtime=model_runtime,
                model_config=model_config,
            )
        else:
            # 全數命中快取時不載入數百 MB 權重，也不佔用 GPU。
            model_runtime = {
                "device": "not_loaded_all_reused",
                "vector_dim": PATENT_SBERTA_VECTOR_DIM,
                "max_seq_length": PATENT_SBERTA_MAX_SEQ_LENGTH,
            }
            upserted_rows = 0
        # psycopg connection context 只有整段無例外才會 commit，避免半批向量落庫。
        from psycopg import sql

        embedding_table = get_source_spec(config.source_field).embedding_table
        schema_name, table_name = embedding_table.split(".", maxsplit=1)
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema_name, table_name))
            )
            table_rows_for_source = int(cur.fetchone()[0])

    usable = [item for item in processed if item.status == "usable"]
    chunk_token_counts = [count for item in usable for count in (item.chunk_token_counts or [])]
    return EmbeddingWriteSummary(
        source_rows=len(sources),
        usable_rows=len(usable),
        skipped_rows=len(processed) - len(usable),
        total_chunks=len(chunk_token_counts),
        max_chunk_tokens=max(chunk_token_counts, default=0),
        would_truncate_after_chunking=sum(bool(item.would_truncate_after_chunking) for item in usable),
        reused_rows=reused_rows,
        upserted_rows=upserted_rows,
        table_rows_for_source=table_rows_for_source,
        device=str(model_runtime["device"]),
        vector_dim=int(model_runtime["vector_dim"]),
        embedding_model=PATENT_SBERTA_MODEL,
        model_version=model_version,
        source_field=config.source_field,
    )


def model_config_aggregation_method() -> str:
    """回傳目前正式管線唯一允許的 patent-level 聚合方法。"""
    return ModelConfig().aggregation_method


def resolve_model_version(model_path: Path) -> str:
    """以本機 PatentSBERTa 權重 SHA-256 建立可重現的模型版本。"""
    for filename in ("model.safetensors", "pytorch_model.bin"):
        weight_path = model_path / filename
        if weight_path.is_file():
            digest = hashlib.sha256()
            with weight_path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            return f"sha256:{digest.hexdigest()}"
    raise FileNotFoundError(f"no PatentSBERTa model weights found under {model_path}")


def vector_to_pgvector(vector: list[float]) -> str:
    """把 768 維浮點向量轉成 pgvector 可接受的文字格式。"""
    if len(vector) != PATENT_SBERTA_VECTOR_DIM:
        raise ValueError(f"expected {PATENT_SBERTA_VECTOR_DIM} dimensions, got {len(vector)}")
    return "[" + ",".join(format(float(value), ".9g") for value in vector) + "]"


def hash_embedding_vector(vector: list[float]) -> str:
    """以 float32 bytes 計算穩定 hash，供重用與完整性檢查。"""
    values = np.asarray(vector, dtype=np.float32)
    if values.shape != (PATENT_SBERTA_VECTOR_DIM,):
        raise ValueError(f"expected vector shape ({PATENT_SBERTA_VECTOR_DIM},), got {values.shape}")
    return hashlib.sha256(values.tobytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    """解析正式 embedding writer 的命令列參數。"""
    parser = argparse.ArgumentParser(description="Embed DB patent claims and persist VECTOR(768) results.")
    parser.add_argument("--model-path", type=Path, default=DEFAULT_LOCAL_MODEL_PATH)
    parser.add_argument("--source-field", default=SOURCE_FIELD_WIPS_INDEPENDENT_CLAIMS)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> None:
    """執行 writer 並以 JSON 顯示可供人工驗收的結果。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    if args.batch_size <= 0:
        raise ValueError("batch-size must be > 0")
    summary = write_patent_embeddings(
        EmbeddingWriteConfig(
            source_field=args.source_field,
            model_path=args.model_path,
            device=args.device,
            batch_size=args.batch_size,
            limit=args.limit,
        )
    )
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
