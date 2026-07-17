from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import time
from typing import Any

# 這些環境值必須在 numpy/sklearn/joblib 匯入前設定，才能避免 Windows 核心偵測噪音。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

import numpy as np
import pandas as pd
from transformers import AutoTokenizer

from .model import (
    EmbeddingMatrix,
    ModelConfig,
    PATENT_SBERTA_MAX_SEQ_LENGTH,
    PATENT_SBERTA_MODEL,
    build_embedding_matrix,
    compare_dimension_schemes,
    default_dimension_schemes,
    embed_processed_texts,
    load_sentence_transformer,
    resolve_patent_number,
    validate_patent_sberta,
)
from .preprocessing import TextPreprocessConfig, add_claim_aware_chunks, mark_exact_duplicates, process_patent_text


LOGGER = logging.getLogger(__name__)
DEFAULT_LOCAL_MODEL_PATH = Path("backend/models/PatentSBERTa")
DEFAULT_OUTPUT_PATH = Path("output/clustering_dimension_test/dimension_comparison.json")
DEFAULT_EMBEDDING_CACHE_PATH = Path("output/clustering_dimension_test/patent_embeddings.npz")
CACHE_FORMAT_VERSION = "patent_embedding_npz_v1"
PREPROCESSING_VERSION = "wips_independent_claims_claim_aware_v1"


def main() -> None:
    """Run the fixed-k BERTopic dimension comparison from a WIPS Excel file."""
    args = parse_args()
    configure_runtime()
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.embedding_cache.parent.mkdir(parents=True, exist_ok=True)

    started_at = time.perf_counter()
    model_path = validate_local_model_path(args.model_path)
    input_hash = file_sha256(args.input_xlsx)
    model_hash = file_sha256(find_model_weight_file(model_path))
    cache_key = build_cache_key(
        input_hash=input_hash,
        model_hash=model_hash,
        normalize_embeddings=True,
    )

    LOGGER.info("Reading WIPS Excel: %s", args.input_xlsx)
    df = pd.read_excel(args.input_xlsx)
    claim_column = find_wips_independent_claim_column(df.columns)
    processed = [
        process_patent_text(value, row_number=index + 1, config=TextPreprocessConfig(chunking=True))
        for index, value in enumerate(df[claim_column].tolist())
    ]
    mark_exact_duplicates(processed)
    documents = [item.cleaned_text for item in processed if item.status == "usable"]
    patent_identities = [resolve_row_identity(row) for _, row in df.iterrows()]
    preprocessing_seconds = time.perf_counter() - started_at
    LOGGER.info("Preprocessed %d usable documents from %d rows", len(documents), len(df))

    config = ModelConfig(
        embedding_model=PATENT_SBERTA_MODEL,
        n_clusters=args.fixed_k,
        batch_size=args.batch_size,
        kmeans_batch_size=args.kmeans_batch_size,
        device=args.device,
        show_progress_bar=True,
    )
    validate_run_parameters(config=config, document_count=len(documents))

    matrix: EmbeddingMatrix
    cache_metadata = load_cache_metadata(args.embedding_cache)
    cache_hit = (
        not args.rebuild_embedding_cache
        and cache_metadata is not None
        and cache_metadata.get("cache_key") == cache_key
        and args.embedding_cache.exists()
    )
    model_runtime: dict[str, Any]
    chunk_stats: dict[str, Any]
    model_load_seconds = 0.0
    embedding_seconds = 0.0

    if cache_hit:
        LOGGER.info("Reusing embedding cache: %s", args.embedding_cache)
        matrix = load_embedding_cache(args.embedding_cache)
        model_runtime = dict(cache_metadata["model_runtime"])
        chunk_stats = dict(cache_metadata["chunk_stats"])
    else:
        LOGGER.info("Building claim-aware chunks with local tokenizer")
        tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        add_claim_aware_chunks(
            processed,
            tokenizer=tokenizer,
            max_seq_length=PATENT_SBERTA_MAX_SEQ_LENGTH,
        )
        ensure_no_chunk_truncation(processed)
        chunk_stats = summarize_chunks(processed)
        LOGGER.info(
            "Chunking produced %d chunks; max tokens=%d; multi-chunk documents=%d",
            chunk_stats["total_chunks"],
            chunk_stats["max_chunk_tokens"],
            chunk_stats["multi_chunk_docs"],
        )

        model_load_started = time.perf_counter()
        LOGGER.info("Loading PatentSBERTa from %s on device=%s", model_path, args.device)
        model = load_sentence_transformer(
            model_path,
            device=args.device,
            local_files_only=True,
        )
        model_runtime = validate_patent_sberta(model)
        model_load_seconds = time.perf_counter() - model_load_started
        LOGGER.info(
            "Loaded PatentSBERTa on %s: max_seq_length=%d, vector_dim=%d",
            model_runtime["device"],
            model_runtime["max_seq_length"],
            model_runtime["vector_dim"],
        )

        embedding_started = time.perf_counter()
        embeddings = embed_processed_texts(
            processed,
            model=model,
            config=config,
            patent_identities=patent_identities,
        )
        matrix = build_embedding_matrix(embeddings)
        embedding_seconds = time.perf_counter() - embedding_started
        LOGGER.info(
            "Embedded %d patents into %d dimensions in %.2f seconds",
            len(matrix.vectors),
            len(matrix.vectors[0]),
            embedding_seconds,
        )

        cache_metadata = {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "cache_key": cache_key,
            "input_sha256": input_hash,
            "model_sha256": model_hash,
            "embedding_model": PATENT_SBERTA_MODEL,
            "model_path": str(model_path),
            "model_runtime": model_runtime,
            "preprocessing_version": PREPROCESSING_VERSION,
            "source_column": claim_column,
            "chunk_stats": chunk_stats,
            "embedding_seconds": embedding_seconds,
        }
        save_embedding_cache(args.embedding_cache, matrix=matrix, metadata=cache_metadata)
        LOGGER.info("Saved reusable embedding cache: %s", args.embedding_cache)

        del model
        release_cuda_memory()

    validate_document_alignment(processed=processed, matrix=matrix)
    LOGGER.info("Running BERTopic with fixed k=%d for 768D, PCA100D, and PCA50D", args.fixed_k)
    clustering_started = time.perf_counter()
    results = compare_dimension_schemes(
        documents,
        matrix,
        schemes=default_dimension_schemes(),
        config=config,
    )
    clustering_seconds = time.perf_counter() - clustering_started

    payload = {
        "input_xlsx": str(args.input_xlsx),
        "input_sha256": input_hash,
        "source_column": claim_column,
        "fixed_k": args.fixed_k,
        "doc_count": len(documents),
        "model": {
            "repository": PATENT_SBERTA_MODEL,
            "local_path": str(model_path),
            "weights_sha256": model_hash,
            **model_runtime,
        },
        "embedding_cache": {
            "path": str(args.embedding_cache),
            "cache_key": cache_key,
            "reused": cache_hit,
        },
        "preprocessing": {
            "version": PREPROCESSING_VERSION,
            **chunk_stats,
        },
        "timing_seconds": {
            "preprocessing": preprocessing_seconds,
            "model_load": model_load_seconds,
            "embedding": embedding_seconds,
            "clustering_and_metrics": clustering_seconds,
            "total": time.perf_counter() - started_at,
        },
        "summary": [
            {
                "scheme": result.scheme_name,
                "score": result.score,
                **result.metrics,
                "elapsed_seconds": result.elapsed_seconds,
            }
            for result in sorted(results, key=lambda item: item.score or 0.0, reverse=True)
        ],
        "details": [result.to_dict(include_model=False) for result in results],
    }
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.info("Completed comparison in %.2f seconds: %s", payload["timing_seconds"]["total"], args.output_json)
    print(json.dumps({"output_json": str(args.output_json), "summary": payload["summary"]}, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    """Parse the explicit inputs needed for a reproducible dimension comparison."""
    parser = argparse.ArgumentParser(description="Compare BERTopic clustering spaces: 768D, PCA100D, PCA50D.")
    parser.add_argument("input_xlsx", type=Path)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--embedding-cache", type=Path, default=DEFAULT_EMBEDDING_CACHE_PATH)
    parser.add_argument("--model-path", type=Path, default=DEFAULT_LOCAL_MODEL_PATH)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--fixed-k", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--kmeans-batch-size", type=int, default=128)
    parser.add_argument("--rebuild-embedding-cache", action="store_true")
    return parser.parse_args()


def configure_runtime() -> None:
    """Configure concise logs and avoid noisy Windows joblib CPU probing."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("gensim").setLevel(logging.WARNING)


def validate_local_model_path(model_path: Path) -> Path:
    """Require a complete local model artifact instead of runtime Hub fallback."""
    resolved = model_path.resolve()
    required_files = ("config.json", "modules.json", "tokenizer.json")
    missing = [name for name in required_files if not (resolved / name).is_file()]
    if missing:
        raise FileNotFoundError(f"incomplete local PatentSBERTa model at {resolved}; missing: {missing}")
    find_model_weight_file(resolved)
    return resolved


def find_model_weight_file(model_path: Path) -> Path:
    """Locate the primary local model weights used for artifact identity."""
    for filename in ("model.safetensors", "pytorch_model.bin"):
        candidate = model_path / filename
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no model weights found under {model_path}")


def file_sha256(path: Path) -> str:
    """Hash an input or model artifact without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_cache_key(*, input_hash: str, model_hash: str, normalize_embeddings: bool) -> str:
    """Build a deterministic identity for reusable patent-level embeddings."""
    payload = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "input_sha256": input_hash,
        "model_sha256": model_hash,
        "preprocessing_version": PREPROCESSING_VERSION,
        "embedding_model": PATENT_SBERTA_MODEL,
        "max_seq_length": PATENT_SBERTA_MAX_SEQ_LENGTH,
        "normalize_embeddings": normalize_embeddings,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def save_embedding_cache(path: Path, *, matrix: EmbeddingMatrix, metadata: dict[str, Any]) -> None:
    """Persist vectors and row identity for repeated dimension experiments."""
    np.savez_compressed(
        path,
        vectors=np.asarray(matrix.vectors, dtype=np.float32),
        row_numbers=np.asarray(matrix.row_numbers, dtype=np.int64),
        patent_numbers=np.asarray([value or "" for value in matrix.patent_numbers], dtype=np.str_),
    )
    cache_metadata_path(path).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def load_embedding_cache(path: Path) -> EmbeddingMatrix:
    """Load a non-pickle NPZ cache while preserving patent row alignment."""
    with np.load(path, allow_pickle=False) as cached:
        vectors = cached["vectors"].astype(np.float32, copy=False)
        row_numbers = cached["row_numbers"].astype(np.int64, copy=False)
        patent_numbers = cached["patent_numbers"].astype(np.str_, copy=False)
    return EmbeddingMatrix(
        row_numbers=[int(value) for value in row_numbers],
        patent_numbers=[str(value) or None for value in patent_numbers],
        vectors=vectors.tolist(),
    )


def load_cache_metadata(path: Path) -> dict[str, Any] | None:
    """Read cache metadata when both its JSON syntax and object shape are valid."""
    metadata_path = cache_metadata_path(path)
    if not metadata_path.is_file():
        return None
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"embedding cache metadata must be an object: {metadata_path}")
    return payload


def cache_metadata_path(path: Path) -> Path:
    """Return the sidecar path that explains how an embedding cache was made."""
    return path.with_suffix(f"{path.suffix}.json")


def validate_run_parameters(*, config: ModelConfig, document_count: int) -> None:
    """Reject fixed-k and batch settings that cannot produce a valid test."""
    if document_count < 2:
        raise ValueError("at least two usable documents are required")
    if config.n_clusters < 2 or config.n_clusters >= document_count:
        raise ValueError("fixed-k must be >= 2 and smaller than usable document count")
    if config.batch_size <= 0 or config.kmeans_batch_size <= 0:
        raise ValueError("batch sizes must be > 0")


def ensure_no_chunk_truncation(processed: list[Any]) -> None:
    """Stop before embedding if any usable chunk still exceeds 512 tokens."""
    truncated_rows = [item.row_number for item in processed if item.status == "usable" and item.was_truncated]
    if truncated_rows:
        raise ValueError(f"claim-aware chunking still truncates rows: {truncated_rows[:10]}")


def summarize_chunks(processed: list[Any]) -> dict[str, Any]:
    """Summarize chunk completeness for the final experiment artifact."""
    usable = [item for item in processed if item.status == "usable"]
    token_counts = [count for item in usable for count in (item.chunk_token_counts or [])]
    return {
        "usable_docs": len(usable),
        "skipped_docs": len(processed) - len(usable),
        "total_chunks": len(token_counts),
        "max_chunk_tokens": max(token_counts, default=0),
        "multi_chunk_docs": sum(item.chunk_count > 1 for item in usable),
        "split_within_claim_docs": sum(item.split_within_claim_count > 0 for item in usable),
        "would_truncate_after_chunking": sum(bool(item.was_truncated) for item in usable),
    }


def validate_document_alignment(*, processed: list[Any], matrix: EmbeddingMatrix) -> None:
    """Ensure every BERTopic document remains tied to its source patent row."""
    expected_rows = [item.row_number for item in processed if item.status == "usable"]
    if matrix.row_numbers != expected_rows:
        raise ValueError("embedding cache rows do not align with current usable documents")
    if len(matrix.patent_numbers) != len(expected_rows) or len(matrix.vectors) != len(expected_rows):
        raise ValueError("patent numbers, rows, and embedding vectors must have the same length")
    vector_dims = {len(vector) for vector in matrix.vectors}
    if vector_dims != {768}:
        raise ValueError(f"expected only 768-dimensional PatentSBERTa vectors, got {sorted(vector_dims)}")


def release_cuda_memory() -> None:
    """Release embedding model VRAM before CPU clustering and metric evaluation."""
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def find_wips_independent_claim_column(columns: Any) -> str:
    """Find the WIPS independent-claims column without selecting its count field."""
    for column in columns:
        text = str(column)
        if "[KR,JP,US,CN,EP,IN]" in text and "数量" not in text and "數量" not in text:
            return text
    raise ValueError("cannot find WIPS independent claims column")


def resolve_row_identity(row: Any) -> Any:
    """Resolve one row's patent-number identity using grant-to-application priority."""
    fields = {
        "country_code": clean_cell(row.get("国家代码")),
        "授權公告號": clean_cell(row.get("授权公告号")),
        "審查的公告號": clean_cell(row.get("审查的公告号")),
        "未審查的公開號": clean_cell(row.get("未审查的公开号")),
        "申請號": clean_cell(row.get("申请号")),
    }
    return resolve_patent_number(fields)


def clean_cell(value: Any) -> str | None:
    """Normalize empty spreadsheet values before patent-number resolution."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


if __name__ == "__main__":
    main()
