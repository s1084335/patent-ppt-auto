"""workspace 分群模型 artifact 的版本化保存與完整性驗證。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import pickle
from pathlib import Path
from typing import Any


DEFAULT_ARTIFACT_ROOT = Path("data/model_artifacts/clustering")


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


def artifact_path(
    *,
    workspace_id: int,
    source_field: str,
    run_id: int,
    root: Path = DEFAULT_ARTIFACT_ROOT,
) -> Path:
    """建立不受使用者輸入控制的 workspace artifact 路徑。"""
    safe_source = source_field.replace("/", "_").replace("\\", "_")
    return root / f"workspace_{workspace_id}" / safe_source / f"run_{run_id}.pkl"


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
