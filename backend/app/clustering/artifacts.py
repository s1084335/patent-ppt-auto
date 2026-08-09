"""workspace 分群模型 artifact 的版本化保存與完整性驗證。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import pickle
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from backend.app.settings import get_model_artifact_root


ARTIFACT_NAMESPACE = "clustering"

#: artifact schema 版本。v1＝只有 MiniBatchKMeans；v2 起帶 algorithm 與
#: DP-Means 狀態（openspec replace-clustering-with-dpmeans）。
ARTIFACT_SCHEMA_VERSION = 2

#: 分群演算法識別。⚠ 2026-08-09 使用者定案「feature flag 並存」——兩套引擎
#: 同時存在，artifact **必須自己說出它是哪個演算法產的**。否則 DP-Means 的
#: run 會被當成 KMeans 讀，中心格式對不上，而且是「載入成功、結果卻莫名
#: 其妙」的靜默錯。
ALGORITHM_KMEANS = "minibatch_kmeans"
ALGORITHM_DPMEANS = "dpmeans"


@dataclass
class WorkspaceTopicArtifact:
    """保存 incremental 必需的 PCA、BERTopic 與模型來源資訊。"""

    workspace_id: int
    source_field: str
    run_id: int
    artifact_version: int
    reducer: Any
    topic_model: Any
    embedding_model: str
    embedding_model_version: str
    preprocessing_version: str
    #: 產生這份 artifact 的演算法。⚠ 預設舊引擎：feature flag 並存期間沒特別
    #: 指定就是舊的，而且舊 pickle 根本沒有這個欄位（見 load_artifact 補值）。
    algorithm: str = ALGORITHM_KMEANS
    #: DP-Means 的狀態（centers／counts／lambda）；KMeans 的 run 為 None。
    #: 純基本型別，不 pickle 演算法物件——artifact 要能被檢視與比對。
    dpmeans_state: dict[str, Any] | None = None


def artifact_key(
    *,
    workspace_id: int,
    source_field: str,
    run_id: int,
) -> str:
    """建立寫入 DB 的穩定相對 key，不包含任何機器或容器路徑。"""
    safe_source = source_field.replace("/", "_").replace("\\", "_")
    return PurePosixPath(
        ARTIFACT_NAMESPACE,
        f"workspace_{workspace_id}",
        safe_source,
        f"run_{run_id}.pkl",
    ).as_posix()


def artifact_path(
    *,
    workspace_id: int,
    source_field: str,
    run_id: int,
    root: Path | None = None,
) -> Path:
    """將穩定 artifact key 映射到目前環境的實體檔案路徑。"""
    return resolve_artifact_path(
        artifact_key(workspace_id=workspace_id, source_field=source_field, run_id=run_id),
        root=root,
    )


def resolve_artifact_path(value: str | Path, *, root: Path | None = None) -> Path:
    """解析新相對 key 與舊絕對路徑，讓既有 run 可跨環境繼續載入。"""
    raw_value = str(value).strip()
    if not raw_value:
        raise ValueError("clustering artifact path is empty")

    artifact_root = (root or get_model_artifact_root()).expanduser().resolve()
    native_path = Path(raw_value).expanduser()

    # 舊資料若在目前作業系統仍指向有效檔案，優先保留原位置。
    if native_path.is_absolute() and native_path.is_file():
        return native_path.resolve()

    normalized = raw_value.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    legacy_index = _last_model_artifacts_index(parts)
    if legacy_index is not None:
        # 舊 Windows 或 /app 絕對路徑改映射到目前 MODEL_ARTIFACT_ROOT。
        return _resolve_under_root(artifact_root, parts[legacy_index + 1 :])

    if PurePosixPath(normalized).is_absolute() or PureWindowsPath(raw_value).is_absolute():
        raise FileNotFoundError(f"legacy clustering artifact cannot be remapped: {raw_value}")
    return _resolve_under_root(artifact_root, parts)


def _last_model_artifacts_index(parts: list[str]) -> int | None:
    """找出舊絕對路徑中最後一個 model_artifacts 節點。"""
    indexes = [index for index, part in enumerate(parts) if part.casefold() == "model_artifacts"]
    return indexes[-1] if indexes else None


def _resolve_under_root(root: Path, parts: list[str]) -> Path:
    """安全組合 root 與相對節點，拒絕 ``..`` 逃離模型儲存根目錄。"""
    if not parts or any(part in {"..", ""} for part in parts):
        raise ValueError("invalid clustering artifact key")
    candidate = root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("clustering artifact key escapes MODEL_ARTIFACT_ROOT") from exc
    return candidate


def save_artifact(artifact: WorkspaceTopicArtifact, path: Path) -> str:
    """先寫暫存檔再原子替換，回傳供 DB 驗證的 SHA-256。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("wb") as handle:
        pickle.dump(artifact, handle, protocol=pickle.HIGHEST_PROTOCOL)
    temporary_path.replace(path)
    return file_sha256(path)


def load_artifact(path: Path, *, expected_hash: str | None = None) -> WorkspaceTopicArtifact:
    """驗證檔案 hash 後載入，拒絕錯版或被修改的模型狀態。"""
    if not path.is_file():
        raise FileNotFoundError(f"clustering artifact not found: {path}")
    actual_hash = file_sha256(path)
    if expected_hash and actual_hash != expected_hash:
        raise ValueError(f"clustering artifact hash mismatch: {path}")
    with path.open("rb") as handle:
        artifact = pickle.load(handle)
    if not isinstance(artifact, WorkspaceTopicArtifact):
        raise TypeError(f"unsupported clustering artifact payload: {type(artifact)!r}")
    return artifact


def file_sha256(path: Path) -> str:
    """串流計算 artifact SHA-256，避免一次把大型模型讀入記憶體。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def serialize_dpmeans_state(state: Any, *, lambda_: float) -> dict[str, Any]:
    """DP-Means 狀態 → 純 JSON 型別（centers／counts／lambda）。

    ⚠ 不 pickle 演算法物件：這份狀態要能被人打開來看、被 diff、被比對。
    分群是「結果對不對很難一眼看出」的程式，狀態再變成黑盒就無從查起。
    """
    return {
        "centers": [[float(x) for x in center] for center in state.centers],
        "counts": [int(c) for c in state.counts],
        "lambda": float(lambda_),
    }


def deserialize_dpmeans_state(payload: dict[str, Any]) -> tuple[Any, float]:
    """還原 DP-Means 狀態，回傳 (ClusterState, lambda)。存讀後要能繼續增量。"""
    from backend.app.clustering.dpmeans import ClusterState

    if not payload:
        raise ValueError("dpmeans_state is empty; artifact 可能來自 KMeans run")
    state = ClusterState(
        centers=[[float(x) for x in center] for center in payload["centers"]],
        counts=[int(c) for c in payload["counts"]],
    )
    return state, float(payload["lambda"])


def build_run_metadata(
    *,
    algorithm: str,
    lambda_result: Any = None,
    pca_normalized: bool = False,
    topics_before: int | None = None,
    topics_after: int | None = None,
) -> dict[str, Any]:
    """run metadata：這次用了什麼演算法、lambda 怎麼來的、長出幾個新主題。

    ⚠ CLU-008 要求 lambda 的**值與推導方法**都要留在 run metadata——只存數字
    的話，日後改了公式就再也回答不了「當時為什麼是這個值」。
    ⚠ 舊引擎不塞假的 lambda：沒有就是沒有（None），不要為了欄位齊全而編。
    """
    meta: dict[str, Any] = {
        "algorithm": algorithm,
        "pca_l2_normalized": bool(pca_normalized),
        "lambda": None,
    }
    if lambda_result is not None:
        meta["lambda"] = {
            "value": lambda_result.value,
            "method": lambda_result.method,
            "version": lambda_result.version,
            "sample_size": lambda_result.sample_size,
        }
    if topics_before is not None and topics_after is not None:
        meta["topics_before"] = int(topics_before)
        meta["topics_after"] = int(topics_after)
        meta["topics_new"] = int(topics_after) - int(topics_before)
    return meta
