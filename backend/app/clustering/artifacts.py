"""workspace 分群模型 artifact 的版本化保存與完整性驗證。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import pickle
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from backend.app.settings import get_model_artifact_root


ARTIFACT_NAMESPACE = "clustering"


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
