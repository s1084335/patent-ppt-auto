"""專利主題分群的模型層。

流程是「專利號解析 -> PatentSBERTa chunk embedding -> token 加權聚合 ->
可選的 IncrementalPCA -> BERTopic + MiniBatchKMeans -> 品質指標」。
Excel、資料庫與 CLI 讀寫留在外層，讓模型可以重用於 global 與 workspace 分群。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import re
import time
from typing import Any, Iterable, Protocol

from .preprocessing import DEFAULT_AGGREGATION_METHOD, ProcessedText
from backend.app.transforms.patent_numbers import (
    APPLICATION_NUMBER_TRANSFORMED,
    UNEXAMINED_PUBLICATION_NUMBER_TRANSFORMED,
    transform_patent_number,
)


PATENT_SBERTA_MODEL = "AI-Growth-Lab/PatentSBERTa"
# 釘住 commit，不用浮動的 main：雲端 worker 首次啟動會自行下載權重（見
# backend/app/deploy.py::ensure_patent_sberta），若上游改版就會與本機既有 embedding
# 不同源，向量無法比較。此值＝2026-07-23 查得的 HuggingFace 最新 commit，與本機
# 快取一致；模型自 2023-02-16 起未更動。要升版時一併重算既有 embeddings。
PATENT_SBERTA_REVISION = "3ff1d553c861d8f5bfd902333d97fc95eb6b4c8f"
PATENT_SBERTA_VECTOR_DIM = 768
PATENT_SBERTA_MAX_SEQ_LENGTH = 512
REPRESENTATIVE_DOC_LIMIT_FOR_LLM = 10
_COHERENCE_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_+/\-]*")
PATENT_NUMBER_PRIORITY = (
    ("grant_publication_number", "授權公告號"),
    ("examined_publication_number", "審查的公告號"),
    ("unexamined_publication_number", "未審查的公開號"),
    ("application_number", "申請號"),
)


# 模型設定與跨階段資料物件。
class TextEmbeddingModel(Protocol):
    """定義 SentenceTransformer 類模型在本管線所需的最小介面。"""

    def encode(self, sentences: list[str], **kwargs: Any) -> Any:
        """把一批文本轉成數值向量。"""
        ...


@dataclass(frozen=True)
class ModelConfig:
    """可隨 topic model profile 保存並重演的模型參數。"""

    embedding_model: str = PATENT_SBERTA_MODEL
    topic_model: str = "BERTopic"
    cluster_model: str = "MiniBatchKMeans"
    reducer: str = "IncrementalPCA"
    aggregation_method: str = DEFAULT_AGGREGATION_METHOD
    # 407 筆實測後定案：正式 clustering space 固定使用 IncrementalPCA 100 維。
    n_components: int = 100
    n_clusters: int = 10
    batch_size: int = 8
    kmeans_batch_size: int = 128
    random_state: int = 42
    normalize_embeddings: bool = True
    show_progress_bar: bool = False
    device: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        """轉成可寫入 profile 與 run metadata 的字典。"""
        return asdict(self)


@dataclass(frozen=True)
class PatentNumberIdentity:
    """由 WIPS 四種專利號解析出的業務追蹤識別。"""

    patent_number: str
    patent_number_type: str
    country_code: str | None = None
    grant_publication_number: str | None = None
    examined_publication_number: str | None = None
    unexamined_publication_number: str | None = None
    application_number: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """轉成可供資料庫寫入與稽核輸出的字典。"""
        return asdict(self)


@dataclass
class DocumentEmbedding:
    """一篇專利的 embedding，以及產生它時使用的 chunk 稽核資料。"""

    row_number: int
    status: str
    vector: list[float] | None
    vector_dim: int
    chunk_count: int
    chunk_weights: list[float]
    aggregation_method: str
    patent_number: str | None = None
    patent_number_type: str | None = None
    skipped_reason: str | None = None

    def to_dict(self, include_vector: bool = False) -> dict[str, Any]:
        """輸出稽核資料；向量較大，只有明確要求時才一併輸出。"""
        payload = asdict(self)
        if not include_vector:
            payload.pop("vector", None)
        return payload


@dataclass(frozen=True)
class EmbeddingMatrix:
    """保留原始列號與專利號對應關係的 patent-level embedding 矩陣。"""

    row_numbers: list[int]
    patent_numbers: list[str | None]
    vectors: list[list[float]]

    def to_dict(self) -> dict[str, Any]:
        """將矩陣與列對應資料完整轉成字典。"""
        return asdict(self)


@dataclass(frozen=True)
class ReducedEmbeddingMatrix:
    """與原始專利列順序對齊的 IncrementalPCA 降維結果。"""

    row_numbers: list[int]
    patent_numbers: list[str | None]
    vectors: list[list[float]]
    reducer: str
    n_components: int

    def to_dict(self) -> dict[str, Any]:
        """輸出降維向量與 reducer metadata。"""
        return asdict(self)


@dataclass(frozen=True)
class TopicAssignment:
    """一篇專利由 BERTopic 指派到某個 topic 的結果。"""

    row_number: int
    patent_number: str | None
    topic_id: int

    def to_dict(self) -> dict[str, Any]:
        """轉成可供資料庫寫入的 topic assignment 字典。"""
        return asdict(self)


@dataclass(frozen=True)
class TopicModelRunResult:
    """單一維度方案的 BERTopic 結果、指派、代表文件與品質指標。"""

    scheme_name: str
    topic_model: Any
    topics: list[int]
    assignments: list[TopicAssignment]
    topic_info: list[dict[str, Any]]
    representative_docs: dict[int, list[str]]
    representative_doc_indices: dict[int, list[int]]
    metrics: dict[str, float]
    score: float | None = None
    elapsed_seconds: float | None = None

    def to_dict(self, include_model: bool = False) -> dict[str, Any]:
        """輸出 run 結果；BERTopic 模型物件只有明確要求時才包含。"""
        payload = asdict(self)
        if include_model:
            payload["topic_model"] = self.topic_model
        else:
            payload.pop("topic_model", None)
        return payload


@dataclass(frozen=True)
class DimensionScheme:
    """一組用來比較是否需要降維的 clustering space 設定。"""

    scheme_name: str
    n_components: int | None

    def to_dict(self) -> dict[str, Any]:
        """將維度測試方案轉成字典。"""
        return asdict(self)


# 專利號解析與 PatentSBERTa artifact 載入。
def resolve_patent_number(number_fields: dict[str, Any]) -> PatentNumberIdentity:
    """依四種號碼優先序解析，未審查公開號與申請號使用轉換後值。"""
    # 四欄各自保存，不把不同種類的專利號混進同一個來源欄位。
    normalized = {
        key: _clean_number_value(number_fields.get(column_name))
        for key, column_name in PATENT_NUMBER_PRIORITY
    }
    country_code = _clean_country_code(number_fields.get("country_code"))
    normalized["unexamined_publication_number"] = _clean_number_value(
        number_fields.get(UNEXAMINED_PUBLICATION_NUMBER_TRANSFORMED)
    ) or transform_patent_number(country_code, normalized["unexamined_publication_number"])
    normalized["application_number"] = _clean_number_value(
        number_fields.get(APPLICATION_NUMBER_TRANSFORMED)
    ) or transform_patent_number(country_code, normalized["application_number"])
    for key, _ in PATENT_NUMBER_PRIORITY:
        raw_patent_number = normalized[key]
        if raw_patent_number:
            return PatentNumberIdentity(
                patent_number=format_patent_number(country_code, raw_patent_number),
                patent_number_type=key,
                country_code=country_code,
                **normalized,
            )
    raise ValueError("at least one patent number is required")


def format_patent_number(country_code: Any, patent_number: Any) -> str:
    """保留來源號碼；台灣案件只把四位西元年前綴轉成三位民國年。"""
    transformed = transform_patent_number(country_code, patent_number)
    if transformed is None:
        raise ValueError("patent_number is required")
    return transformed


def download_sentence_transformer_model(model_name: str, local_dir: str | Path) -> Path:
    """將 SentenceTransformer 模型下載到明確的本機 artifact 目錄。"""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("huggingface_hub is required to download model artifacts") from exc

    target = Path(local_dir)
    snapshot_download(
        repo_id=model_name,
        local_dir=str(target),
        local_dir_use_symlinks=False,
    )
    return target


def resolve_torch_device(requested_device: str = "auto") -> str:
    """解析 auto/cuda/cpu；明確指定 CUDA 時不得靜默退回 CPU。"""
    normalized = requested_device.strip().lower()
    if normalized not in {"auto", "cuda", "cpu"}:
        raise ValueError("device must be one of: auto, cuda, cpu")

    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("torch is required to resolve the embedding device") from exc

    if normalized == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if normalized == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but torch.cuda.is_available() is False")
    return normalized


def load_sentence_transformer(
    model_name_or_path: str | Path = PATENT_SBERTA_MODEL,
    *,
    device: str = "auto",
    local_files_only: bool | None = None,
) -> Any:
    """在指定裝置載入 PatentSBERTa，並可強制只讀本機模型檔。"""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required to load embedding models. "
            "Install it in the runtime environment before real embedding."
        ) from exc
    model_source = str(model_name_or_path)
    if local_files_only is None:
        local_files_only = Path(model_source).exists()
    return SentenceTransformer(
        model_source,
        device=resolve_torch_device(device),
        local_files_only=local_files_only,
    )


def validate_patent_sberta(model: Any) -> dict[str, Any]:
    """正式嵌入前驗證 PatentSBERTa 的 512 token 上限與 768 維輸出。"""
    vector_dim = int(model.get_sentence_embedding_dimension())
    max_seq_length = int(model.max_seq_length)
    if vector_dim != PATENT_SBERTA_VECTOR_DIM:
        raise ValueError(
            f"PatentSBERTa vector dimension must be {PATENT_SBERTA_VECTOR_DIM}, got {vector_dim}"
        )
    if max_seq_length != PATENT_SBERTA_MAX_SEQ_LENGTH:
        raise ValueError(
            f"PatentSBERTa max_seq_length must be {PATENT_SBERTA_MAX_SEQ_LENGTH}, got {max_seq_length}"
        )
    return {
        "device": str(model.device),
        "vector_dim": vector_dim,
        "max_seq_length": max_seq_length,
    }


# Claim chunk embedding 與 patent-level 向量聚合。
def embed_processed_texts(
    processed: list[ProcessedText],
    *,
    model: TextEmbeddingModel,
    config: ModelConfig | None = None,
    patent_identities: list[PatentNumberIdentity | None] | None = None,
) -> list[DocumentEmbedding]:
    """嵌入前處理後的 claim chunks，並依 token 權重聚合成每篇專利向量。"""
    config = config or ModelConfig()
    if config.aggregation_method != DEFAULT_AGGREGATION_METHOD:
        raise ValueError(f"unsupported aggregation_method: {config.aggregation_method}")
    if patent_identities is not None and len(patent_identities) != len(processed):
        raise ValueError("patent_identities must match processed length")

    chunk_jobs: list[tuple[int, str]] = []
    outputs: list[DocumentEmbedding | None] = []

    # 先展開成 chunk jobs，一次交給模型做批次推論；doc_index 保留回原專利的關係。
    for doc_index, item in enumerate(processed):
        identity = patent_identities[doc_index] if patent_identities is not None else None
        if item.status != "usable":
            outputs.append(
                DocumentEmbedding(
                    row_number=item.row_number,
                    status=item.status,
                    vector=None,
                    vector_dim=0,
                    chunk_count=0,
                    chunk_weights=[],
                    aggregation_method=config.aggregation_method,
                    patent_number=identity.patent_number if identity else None,
                    patent_number_type=identity.patent_number_type if identity else None,
                    skipped_reason=item.skip_reason,
                )
            )
            continue

        chunk_texts = item.chunk_texts or [item.cleaned_text]
        if not chunk_texts:
            raise ValueError(f"row {item.row_number} has no chunk text to embed")

        outputs.append(None)
        for chunk_text in chunk_texts:
            chunk_jobs.append((doc_index, chunk_text))

    chunk_vectors = _encode_texts(
        model,
        [chunk_text for _, chunk_text in chunk_jobs],
        config=config,
    )
    vectors_by_doc: dict[int, list[list[float]]] = {}
    for (doc_index, _), vector in zip(chunk_jobs, chunk_vectors, strict=True):
        vectors_by_doc.setdefault(doc_index, []).append(vector)

    # 每篇專利依 chunk token 數做 weighted mean，得到唯一的 patent-level embedding。
    for doc_index, item in enumerate(processed):
        if item.status != "usable":
            continue

        identity = patent_identities[doc_index] if patent_identities is not None else None
        vectors = vectors_by_doc.get(doc_index, [])
        weights = chunk_weights(item, fallback_chunk_count=len(vectors))
        vector = weighted_mean_vectors(vectors, weights)
        outputs[doc_index] = DocumentEmbedding(
            row_number=item.row_number,
            status=item.status,
            vector=vector,
            vector_dim=len(vector),
            chunk_count=len(vectors),
            chunk_weights=weights,
            aggregation_method=config.aggregation_method,
            patent_number=identity.patent_number if identity else None,
            patent_number_type=identity.patent_number_type if identity else None,
            skipped_reason=None,
        )

    return [output for output in outputs if output is not None]


def build_embedding_matrix(embeddings: list[DocumentEmbedding]) -> EmbeddingMatrix:
    """收集可用的專利向量，建立 BERTopic 使用的稠密矩陣。"""
    row_numbers: list[int] = []
    patent_numbers: list[str | None] = []
    vectors: list[list[float]] = []
    for item in embeddings:
        if item.status != "usable" or item.vector is None:
            continue
        row_numbers.append(item.row_number)
        patent_numbers.append(item.patent_number)
        vectors.append(item.vector)
    if not vectors:
        raise ValueError("no usable embeddings to model")
    return EmbeddingMatrix(row_numbers=row_numbers, patent_numbers=patent_numbers, vectors=vectors)


# 降維與 BERTopic + MiniBatchKMeans 分群。
def reduce_with_incremental_pca(
    matrix: EmbeddingMatrix,
    *,
    n_components: int,
    batch_size: int,
) -> ReducedEmbeddingMatrix:
    """先用 IncrementalPCA 降維，再將結果交給 BERTopic。"""
    reduced, _ = fit_incremental_pca(
        matrix,
        n_components=n_components,
        batch_size=batch_size,
    )
    return reduced


def fit_incremental_pca(
    matrix: EmbeddingMatrix,
    *,
    n_components: int,
    batch_size: int,
) -> tuple[ReducedEmbeddingMatrix, Any]:
    """訓練可持久化的 IncrementalPCA，並回傳降維矩陣與 reducer。"""
    try:
        from sklearn.decomposition import IncrementalPCA
    except ImportError as exc:
        raise RuntimeError("scikit-learn is required for IncrementalPCA reduction") from exc

    if n_components <= 0:
        raise ValueError("n_components must be > 0")
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")

    # PCA 維度不能超過文件數或原始向量維度；小資料集時自動收斂到合法值。
    safe_components = min(n_components, len(matrix.vectors), len(matrix.vectors[0]))
    reducer = IncrementalPCA(n_components=safe_components, batch_size=batch_size)
    reduced = reducer.fit_transform(matrix.vectors)
    matrix_result = ReducedEmbeddingMatrix(
        row_numbers=matrix.row_numbers,
        patent_numbers=matrix.patent_numbers,
        vectors=[_vector_to_float_list(vector) for vector in reduced],
        reducer="IncrementalPCA",
        n_components=safe_components,
    )
    return matrix_result, reducer


def partial_fit_incremental_pca(
    reducer: Any,
    matrix: EmbeddingMatrix,
) -> ReducedEmbeddingMatrix:
    """以新批次更新既有 PCA，再轉換同一批向量供 online BERTopic 使用。"""
    import numpy as np

    if not matrix.vectors:
        raise ValueError("incremental PCA requires at least one vector")
    values = np.asarray(matrix.vectors, dtype=float)
    reducer.partial_fit(values)
    reduced = reducer.transform(values)
    return ReducedEmbeddingMatrix(
        row_numbers=matrix.row_numbers,
        patent_numbers=matrix.patent_numbers,
        vectors=[_vector_to_float_list(vector) for vector in reduced],
        reducer="IncrementalPCA",
        n_components=int(reduced.shape[1]),
    )


def build_bertopic_model(config: ModelConfig | None = None) -> Any:
    """建立以 MiniBatchKMeans 為 clustering backend 的 BERTopic。"""
    config = config or ModelConfig()
    try:
        from bertopic import BERTopic
        from bertopic.dimensionality import BaseDimensionalityReduction
        from sklearn.cluster import MiniBatchKMeans
        from bertopic.vectorizers import OnlineCountVectorizer
    except ImportError as exc:
        raise RuntimeError("bertopic and scikit-learn are required for topic modeling") from exc

    cluster_model = MiniBatchKMeans(
        n_clusters=config.n_clusters,
        batch_size=config.kmeans_batch_size,
        n_init=10,
        random_state=config.random_state,
    )
    vectorizer_model = OnlineCountVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
        decay=None,
        delete_min_df=None,
    )
    return BERTopic(
        # Embedding 已由 PatentSBERTa 在外層完成，BERTopic 不得再自行嵌入。
        embedding_model=None,
        # 三種實驗的維度由外層明確提供，這裡停用 BERTopic 預設 UMAP。
        umap_model=BaseDimensionalityReduction(),
        hdbscan_model=cluster_model,
        vectorizer_model=vectorizer_model,
        calculate_probabilities=False,
        verbose=False,
    )


def fit_bertopic(
    documents: list[str],
    matrix: EmbeddingMatrix | ReducedEmbeddingMatrix,
    *,
    scheme_name: str,
    config: ModelConfig | None = None,
) -> TopicModelRunResult:
    """使用外部提供的 embedding 訓練 BERTopic，並回傳 topic 指派結果。"""
    if len(documents) != len(matrix.vectors):
        raise ValueError("documents and embedding matrix must have the same length")
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("numpy is required to pass embeddings into BERTopic") from exc

    started_at = time.perf_counter()
    topic_model = build_bertopic_model(config=config)
    # BERTopic 只接受 numpy/scipy embeddings；這裡不重新計算 PatentSBERTa。
    embedding_array = np.asarray(matrix.vectors, dtype=float)
    topics, _ = topic_model.fit_transform(documents, embeddings=embedding_array)
    assignments = [
        TopicAssignment(
            row_number=row_number,
            patent_number=patent_number,
            topic_id=int(topic_id),
        )
        for row_number, patent_number, topic_id in zip(
            matrix.row_numbers,
            matrix.patent_numbers,
            topics,
            strict=True,
        )
    ]
    topic_ids = [int(topic) for topic in topics]
    metrics = evaluate_topic_model(documents, topics=topic_ids, topic_model=topic_model)
    representative_doc_indices = rank_ctfidf_representative_documents(
        topic_model=topic_model,
        documents=documents,
        topics=topic_ids,
        limit=REPRESENTATIVE_DOC_LIMIT_FOR_LLM,
    )
    return TopicModelRunResult(
        scheme_name=scheme_name,
        topic_model=topic_model,
        topics=topic_ids,
        assignments=assignments,
        topic_info=topic_model.get_topic_info().to_dict(orient="records"),
        representative_docs={
            topic_id: [documents[index] for index in indexes]
            for topic_id, indexes in representative_doc_indices.items()
        },
        representative_doc_indices=representative_doc_indices,
        metrics=metrics,
        elapsed_seconds=time.perf_counter() - started_at,
    )


def rank_ctfidf_representative_documents(
    *,
    topic_model: Any,
    documents: list[str],
    topics: list[int],
    limit: int = REPRESENTATIVE_DOC_LIMIT_FOR_LLM,
) -> dict[int, list[int]]:
    """依文件與 topic c-TF-IDF 向量的 cosine similarity 取每題前 N 筆。

    BERTopic 預設只保留少量 representative docs，且不公開原始列索引；此處沿用
    BERTopic 的 c-TF-IDF 選取原理，但保留 corpus index，讓後續可穩定追溯 patent_id。
    """
    if len(documents) != len(topics):
        raise ValueError("documents and topics must have the same length")
    if limit < 1:
        raise ValueError("representative document limit must be positive")

    try:
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError as exc:
        raise RuntimeError("scikit-learn is required to rank representative documents") from exc

    model_topic_ids = sorted(int(topic_id) for topic_id in topic_model.get_topics())
    topic_row = {topic_id: row for row, topic_id in enumerate(model_topic_ids)}
    assigned_topic_ids = sorted({topic_id for topic_id in topics if topic_id != -1})
    missing = [topic_id for topic_id in assigned_topic_ids if topic_id not in topic_row]
    if missing:
        raise ValueError(f"BERTopic c-TF-IDF rows missing assigned topics: {missing}")

    ranked: dict[int, list[int]] = {}
    for topic_id in assigned_topic_ids:
        indexes = [index for index, assigned in enumerate(topics) if assigned == topic_id]
        selected_documents = [documents[index] for index in indexes]
        bow = topic_model.vectorizer_model.transform(selected_documents)
        document_ctfidf = topic_model.ctfidf_model.transform(bow)
        similarities = cosine_similarity(
            document_ctfidf,
            topic_model.c_tf_idf_[topic_row[topic_id] : topic_row[topic_id] + 1],
        ).ravel()
        ordered = sorted(
            range(len(indexes)),
            key=lambda local_index: (-float(similarities[local_index]), indexes[local_index]),
        )
        ranked[topic_id] = [indexes[local_index] for local_index in ordered[:limit]]
    return ranked


# ── 不相干專利篩選：反向取樣（每主題 c-TF-IDF 最低 N 筆） ─────────────────
#
# 規格唯一來源：irrelevant-patent-filter-spec.md 第 25-86 行（c-TF-IDF 最低 N 筆方案）。
# 🔴 紅線：相似度**分數**只用來「挑哪 N 筆」，keywords 與分數都**絕不外流給 CLI**。
# 本節函式只回傳 corpus index（供上層轉 patent_id），不回傳任何分數。

# 反向取樣每主題預設取樣比例：取主題內最不像的這一比例當「候選剔除」給使用者過目。
# 依主題大小按比例（非寫死單一數字，沿「簡單≠寫死」原則）。
IRRELEVANT_SAMPLE_RATIO = 0.20
# 取樣數上限：超大主題不隨資料量無限膨脹（AI 判讀成本可控、使用者過目量有限）。
IRRELEVANT_SAMPLE_MAX = 30
# 取樣數下限（主題夠大時至少取這麼多，避免大主題只取到 1 筆）。
IRRELEVANT_SAMPLE_MIN = 5


def irrelevant_sample_size(topic_size: int) -> int:
    """依主題大小決定反向取樣（候選剔除）筆數。

    規格第 78-81 行：**不固定單一數字**——小主題總數不到預設 N 時不得取到整題。
    公式（可解釋）：
    - 取 topic_size × 比例，四捨五入取整；
    - 夾在 [下限, 上限] 之間（主題夠大時至少取下限，超大主題封頂）；
    - **強制 ≤ topic_size - 1**：至少保留一筆在主題內，永不取整題（小主題安全閥）；
    - 只有 1 筆或空主題回 0（無法既保留成員又取樣）。
    """
    if topic_size <= 1:
        return 0
    scaled = round(topic_size * IRRELEVANT_SAMPLE_RATIO)
    # 夾上下限；下限對小主題可能超過 topic_size-1，故最後再統一封在 topic_size-1。
    bounded = max(IRRELEVANT_SAMPLE_MIN, min(IRRELEVANT_SAMPLE_MAX, scaled))
    # 小主題安全閥：永遠留一筆在主題內，不取整題。
    return max(1, min(bounded, topic_size - 1))


def rank_ctfidf_least_representative_documents(
    *,
    topic_model: Any,
    documents: list[str],
    topics: list[int],
    limit: int | None = None,
) -> dict[int, list[int]]:
    """反向取樣：每主題取 c-TF-IDF cosine similarity **最低**（最不像該主題）的 N 筆。

    與 rank_ctfidf_representative_documents 同源、同空間、同一 cosine 演算法，**只把排序
    方向反過來**（規格第 32 行：函式邏輯不必重寫）。用途：找出每主題最不典型的候選，
    交 AI 讀文獻備註輔助使用者判斷是否剔除。

    limit=None（預設）時，每主題依 irrelevant_sample_size(topic_size) 決定取樣數
    （依主題大小調整、小主題不取整題）；limit 明給時各主題統一取該數（測試用）。

    🔴 只回傳 corpus index（供上層映射 patent_id）；**不回傳相似度分數**——分數只在此
    函式內用於排序，不外流（避免與 keywords 一同誤傳給 CLI）。
    """
    if len(documents) != len(topics):
        raise ValueError("documents and topics must have the same length")
    if limit is not None and limit < 1:
        raise ValueError("representative document limit must be positive")

    try:
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError as exc:
        raise RuntimeError("scikit-learn is required to rank representative documents") from exc

    model_topic_ids = sorted(int(topic_id) for topic_id in topic_model.get_topics())
    topic_row = {topic_id: row for row, topic_id in enumerate(model_topic_ids)}
    assigned_topic_ids = sorted({topic_id for topic_id in topics if topic_id != -1})
    missing = [topic_id for topic_id in assigned_topic_ids if topic_id not in topic_row]
    if missing:
        raise ValueError(f"BERTopic c-TF-IDF rows missing assigned topics: {missing}")

    ranked: dict[int, list[int]] = {}
    for topic_id in assigned_topic_ids:
        indexes = [index for index, assigned in enumerate(topics) if assigned == topic_id]
        selected_documents = [documents[index] for index in indexes]
        bow = topic_model.vectorizer_model.transform(selected_documents)
        document_ctfidf = topic_model.ctfidf_model.transform(bow)
        similarities = cosine_similarity(
            document_ctfidf,
            topic_model.c_tf_idf_[topic_row[topic_id] : topic_row[topic_id] + 1],
        ).ravel()
        # ⚠ 排序方向與正向相反：以「+similarity」為主鍵 → 相似度**低**者排前。
        # 同分時仍以 corpus index 遞增穩定排序（與正向同一 tie-break 規則）。
        ordered = sorted(
            range(len(indexes)),
            key=lambda local_index: (float(similarities[local_index]), indexes[local_index]),
        )
        take = limit if limit is not None else irrelevant_sample_size(len(indexes))
        ranked[topic_id] = [indexes[local_index] for local_index in ordered[:take]]
    return ranked


def partial_fit_bertopic(
    topic_model: Any,
    documents: list[str],
    matrix: ReducedEmbeddingMatrix,
) -> list[int]:
    """以新批次更新 MiniBatchKMeans 與 OnlineCountVectorizer，回傳本批主題。"""
    import numpy as np

    if len(documents) != len(matrix.vectors):
        raise ValueError("documents and reduced embeddings must have the same length")
    if not documents:
        return []
    embedding_array = np.asarray(matrix.vectors, dtype=float)
    topic_model.partial_fit(documents, embeddings=embedding_array)
    return [int(topic) for topic in topic_model.topics_]


def compare_dimension_schemes(
    documents: list[str],
    matrix: EmbeddingMatrix,
    *,
    schemes: Iterable[DimensionScheme],
    config: ModelConfig | None = None,
) -> list[TopicModelRunResult]:
    """逐一執行各維度方案，並附上只供排序參考的綜合 score。"""
    config = config or ModelConfig()
    results: list[TopicModelRunResult] = []
    for scheme in schemes:
        # 768D 直接聚類；PCA100D/PCA50D 只替換 clustering space，其餘參數完全相同。
        if scheme.n_components is None:
            scheme_matrix: EmbeddingMatrix | ReducedEmbeddingMatrix = matrix
        else:
            scheme_matrix = reduce_with_incremental_pca(
                matrix,
                n_components=scheme.n_components,
                batch_size=min(config.kmeans_batch_size, len(matrix.vectors)),
            )
        results.append(
            fit_bertopic(
                documents,
                scheme_matrix,
                scheme_name=scheme.scheme_name,
                config=config,
            )
        )
    return attach_ranking_scores(results)


def default_dimension_schemes() -> list[DimensionScheme]:
    """回傳目前固定比較的 768D、PCA100D、PCA50D 三種方案。"""
    return [
        DimensionScheme(scheme_name="768D", n_components=None),
        DimensionScheme(scheme_name="PCA100D", n_components=100),
        DimensionScheme(scheme_name="PCA50D", n_components=50),
    ]


# 分群品質評估：c_v、diversity、balance、small topic ratio。
def evaluate_topic_model(
    documents: list[str],
    *,
    topics: list[int],
    topic_model: Any,
    min_topic_docs: int = 5,
) -> dict[str, float]:
    """從 BERTopic 結果計算目前定案的四個品質指標。"""
    top_terms = {
        int(topic_id): [term for term, _ in (topic_model.get_topic(topic_id) or [])[:10]]
        for topic_id in sorted(set(topics))
        if topic_id != -1
    }
    return {
        "coherence": topic_cv_coherence(documents, topics=topics, top_terms=top_terms),
        "diversity": topic_diversity(top_terms),
        "balance": topic_balance(topics),
        "small_topic_ratio": small_topic_ratio(topics, min_topic_docs=min_topic_docs),
    }


def attach_ranking_scores(results: list[TopicModelRunResult]) -> list[TopicModelRunResult]:
    """加入比較用加權 score，同時保留所有原始指標供前端呈現。"""
    if not results:
        return []

    coherence = _normalize_metric([result.metrics["coherence"] for result in results], higher_is_better=True)
    diversity = _normalize_metric([result.metrics["diversity"] for result in results], higher_is_better=True)
    balance = _normalize_metric([result.metrics["balance"] for result in results], higher_is_better=True)
    small = _normalize_metric([result.metrics["small_topic_ratio"] for result in results], higher_is_better=False)

    scored: list[TopicModelRunResult] = []
    for index, result in enumerate(results):
        score = (
            0.40 * coherence[index]
            + 0.25 * diversity[index]
            + 0.25 * balance[index]
            + 0.10 * small[index]
        )
        scored.append(
            TopicModelRunResult(
                scheme_name=result.scheme_name,
                topic_model=result.topic_model,
                topics=result.topics,
                assignments=result.assignments,
                topic_info=result.topic_info,
                representative_docs=result.representative_docs,
                representative_doc_indices=result.representative_doc_indices,
                metrics=result.metrics,
                score=float(score),
                elapsed_seconds=result.elapsed_seconds,
            )
        )
    return scored


def topic_diversity(top_terms: dict[int, list[str]]) -> float:
    """計算不同 topics 的 top terms 有多少不重複。"""
    terms = [term for terms in top_terms.values() for term in terms]
    if not terms:
        return 0.0
    return len(set(terms)) / len(terms)


def topic_balance(topics: list[int]) -> float:
    """用 normalized entropy 衡量各 topic 文件數是否平衡。"""
    counts = _topic_counts(topics)
    if len(counts) <= 1:
        return 0.0
    total = sum(counts.values())
    entropy = 0.0
    for count in counts.values():
        probability = count / total
        entropy -= probability * math.log(probability)
    return entropy / math.log(len(counts))


def small_topic_ratio(topics: list[int], *, min_topic_docs: int) -> float:
    """計算文件數低於門檻的小 topic 比例。"""
    counts = _topic_counts(topics)
    if not counts:
        return 0.0
    small_count = sum(1 for count in counts.values() if count < min_topic_docs)
    return small_count / len(counts)


def topic_cv_coherence(
    documents: list[str],
    *,
    topics: list[int],
    top_terms: dict[int, list[str]],
) -> float:
    """以 BERTopic top terms 與 gensim c_v 計算文件數加權 coherence。"""
    per_topic_scores = topic_cv_coherence_per_topic(
        documents,
        topics=topics,
        top_terms=top_terms,
    )
    if not per_topic_scores:
        return 0.0
    counts = _topic_counts(topics)
    total_weight = sum(counts.get(topic_id, 0) for topic_id in per_topic_scores)
    if total_weight <= 0:
        return float(sum(per_topic_scores.values()) / len(per_topic_scores))
    return float(
        sum(score * counts.get(topic_id, 0) for topic_id, score in per_topic_scores.items())
        / total_weight
    )


def topic_cv_coherence_per_topic(
    documents: list[str],
    *,
    topics: list[int],
    top_terms: dict[int, list[str]],
) -> dict[int, float]:
    """一次計算每個 topic 的 c_v，供 topic 落庫與第二層拆分判斷。"""
    try:
        from gensim.corpora import Dictionary
        from gensim.models import CoherenceModel
    except ImportError as exc:
        raise RuntimeError("gensim is required for c_v topic coherence") from exc

    tokenized_documents = [_tokenize_for_cv_coherence(document) for document in documents]
    tokenized_documents = [tokens for tokens in tokenized_documents if tokens]
    if not tokenized_documents:
        return {}

    dictionary = Dictionary(tokenized_documents)
    if not dictionary:
        return {}

    coherence_topics: list[list[str]] = []
    coherence_topic_ids: list[int] = []
    for topic_id, terms in top_terms.items():
        normalized_terms = [
            normalized
            for term in terms
            if (normalized := _normalize_topic_term_for_cv(term)) in dictionary.token2id
        ]
        unique_terms = list(dict.fromkeys(normalized_terms))
        if len(unique_terms) < 2:
            continue
        coherence_topics.append(unique_terms[:10])
        coherence_topic_ids.append(topic_id)

    if not coherence_topics:
        return {}

    # 使用者已定案 coherence="c_v"；不是自行近似的 NPMI 分數。
    # Windows 上 gensim 自動多程序可能卡在 worker join，第一版固定單程序確保可重演。
    coherence_model = CoherenceModel(
        topics=coherence_topics,
        texts=tokenized_documents,
        dictionary=dictionary,
        coherence="c_v",
        processes=1,
    )
    per_topic_scores = coherence_model.get_coherence_per_topic()
    return {
        topic_id: float(score)
        for topic_id, score in zip(coherence_topic_ids, per_topic_scores, strict=True)
    }


def _tokenize_for_cv_coherence(document: str) -> list[str]:
    """將文件切成 c_v 使用的 unigram/bigram，並對齊 BERTopic 詞形。"""
    unigrams = _COHERENCE_TOKEN_PATTERN.findall(document.lower())
    bigrams = [f"{left}_{right}" for left, right in zip(unigrams, unigrams[1:], strict=False)]
    return unigrams + bigrams


def _normalize_topic_term_for_cv(term: str) -> str:
    """將 BERTopic term 正規化成 c_v 文件詞典中的 token 格式。"""
    tokens = _COHERENCE_TOKEN_PATTERN.findall(term.lower())
    if not tokens:
        return ""
    return "_".join(tokens)


# 內部向量與指標工具。
def chunk_weights(item: ProcessedText, fallback_chunk_count: int | None = None) -> list[float]:
    """以 chunk token 數產生聚合權重，缺資料時才退回等權重。"""
    chunk_count = item.chunk_count or len(item.chunk_texts or []) or (fallback_chunk_count or 0)
    if chunk_count <= 0:
        return []

    token_counts = item.chunk_token_counts or []
    if len(token_counts) != chunk_count or any(count <= 0 for count in token_counts):
        return [1.0] * chunk_count

    # 長 chunk 對 patent-level embedding 的貢獻較大，但所有權重總和固定為 1。
    total = float(sum(token_counts))
    return [count / total for count in token_counts]


def weighted_mean_vectors(vectors: list[list[float]], weights: list[float]) -> list[float]:
    """依權重將多個 chunk vectors 合併成一個 patent-level vector。"""
    if not vectors:
        raise ValueError("vectors must not be empty")
    if len(vectors) != len(weights):
        raise ValueError("vectors and weights must have the same length")

    dimension = len(vectors[0])
    if dimension == 0:
        raise ValueError("vectors must not be empty-dimensional")
    if any(len(vector) != dimension for vector in vectors):
        raise ValueError("all vectors must have the same dimension")

    weight_total = float(sum(weights))
    if weight_total <= 0:
        raise ValueError("weights must sum to a positive value")

    result = [0.0] * dimension
    for vector, weight in zip(vectors, weights, strict=True):
        normalized_weight = weight / weight_total
        for index, value in enumerate(vector):
            result[index] += float(value) * normalized_weight
    return result


def _encode_texts(
    model: TextEmbeddingModel,
    texts: list[str],
    *,
    config: ModelConfig,
) -> list[list[float]]:
    """批次呼叫 embedding model，並統一轉成 Python float lists。"""
    if not texts:
        return []

    try:
        encoded = model.encode(
            texts,
            batch_size=config.batch_size,
            normalize_embeddings=config.normalize_embeddings,
            show_progress_bar=config.show_progress_bar,
            convert_to_numpy=True,
        )
    except TypeError:
        encoded = model.encode(texts)

    return [_vector_to_float_list(vector) for vector in encoded]


def _vector_to_float_list(vector: Any) -> list[float]:
    """將 numpy array、tensor 或一般 iterable 轉成 float list。"""
    return [float(value) for value in vector]


def _clean_number_value(value: Any) -> str | None:
    """在專利號優先選擇前統一空值與字串格式。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def _clean_country_code(value: Any) -> str | None:
    """只接受兩碼英文字母國別碼，避免把異常來源值組進顯示專利號。"""
    cleaned = _clean_number_value(value)
    if cleaned is None:
        return None
    normalized = cleaned.upper()
    if not re.fullmatch(r"[A-Z]{2}", normalized):
        return None
    return normalized


def _topic_counts(topics: list[int]) -> dict[int, int]:
    """統計各 BERTopic label 的文件數，排除 outlier topic -1。"""
    counts: dict[int, int] = {}
    for topic in topics:
        if topic == -1:
            continue
        counts[topic] = counts.get(topic, 0) + 1
    return counts


def _normalize_metric(values: list[float], *, higher_is_better: bool) -> list[float]:
    """只為候選排序將單一指標正規化到 0..1。"""
    if not values:
        return []
    minimum = min(values)
    maximum = max(values)
    if math.isclose(minimum, maximum):
        return [1.0 for _ in values]
    normalized = [(value - minimum) / (maximum - minimum) for value in values]
    if higher_is_better:
        return normalized
    return [1.0 - value for value in normalized]
