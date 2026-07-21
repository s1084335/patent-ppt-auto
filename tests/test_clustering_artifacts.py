"""分群模型 artifact 路徑可攜性與完整性測試。"""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from backend.app.clustering.artifacts import (
    WorkspaceTopicArtifact,
    artifact_key,
    artifact_path,
    load_artifact,
    resolve_artifact_path,
    save_artifact,
)
from backend.app.settings import get_model_artifact_root


class ArtifactPathTests(unittest.TestCase):
    """驗證 DB key 不含環境路徑，並可映射新舊儲存格式。"""

    def test_generated_key_is_stable_and_relative(self) -> None:
        """確認新 run 只產生 POSIX 相對 key。"""
        key = artifact_key(
            workspace_id=21,
            source_field="wips_independent_claims",
            run_id=46,
        )
        self.assertEqual(
            key,
            "clustering/workspace_21/wips_independent_claims/run_46.pkl",
        )
        self.assertFalse(Path(key).is_absolute())

    def test_artifact_path_uses_explicit_root(self) -> None:
        """確認實體路徑由 root 與穩定 key 組成。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = artifact_path(
                workspace_id=2,
                source_field="effect_summary",
                run_id=9,
                root=root,
            )
            self.assertEqual(
                path,
                root.resolve() / "clustering" / "workspace_2" / "effect_summary" / "run_9.pkl",
            )

    def test_environment_selects_artifact_root(self) -> None:
        """確認部署環境可用 MODEL_ARTIFACT_ROOT 覆寫儲存位置。"""
        with TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"MODEL_ARTIFACT_ROOT": directory}):
                self.assertEqual(get_model_artifact_root(), Path(directory).resolve())

    def test_relative_environment_root_is_project_relative(self) -> None:
        """確認從不同 cwd 啟動時，相對設定仍固定以專案根目錄解析。"""
        with mock.patch.dict(os.environ, {"MODEL_ARTIFACT_ROOT": "data/custom-artifacts"}):
            self.assertEqual(
                get_model_artifact_root(),
                (Path(__file__).resolve().parents[1] / "data" / "custom-artifacts").resolve(),
            )

    def test_relative_key_resolves_under_current_root(self) -> None:
        """確認 DB 新格式會解析到目前環境 root。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = resolve_artifact_path(
                "clustering/workspace_1/effect_summary/run_3.pkl",
                root=root,
            )
            self.assertEqual(
                path,
                root.resolve() / "clustering" / "workspace_1" / "effect_summary" / "run_3.pkl",
            )

    def test_legacy_container_path_is_remapped(self) -> None:
        """確認舊 /app 絕對路徑可映射到新 root。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = resolve_artifact_path(
                "/app/data/model_artifacts/clustering/workspace_4/source/run_8.pkl",
                root=root,
            )
            self.assertEqual(
                path,
                root.resolve() / "clustering" / "workspace_4" / "source" / "run_8.pkl",
            )

    def test_legacy_windows_path_is_remapped(self) -> None:
        """確認舊 Windows 絕對路徑在 Linux 容器也能映射。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = resolve_artifact_path(
                r"D:\patent\data\model_artifacts\clustering\workspace_5\source\run_9.pkl",
                root=root,
            )
            self.assertEqual(
                path,
                root.resolve() / "clustering" / "workspace_5" / "source" / "run_9.pkl",
            )

    def test_existing_native_absolute_path_remains_readable(self) -> None:
        """確認目前系統仍存在的舊絕對檔案不會被強制搬移。"""
        with TemporaryDirectory() as directory, TemporaryDirectory() as root_directory:
            legacy = Path(directory) / "legacy.pkl"
            legacy.write_bytes(b"legacy")
            self.assertEqual(
                resolve_artifact_path(legacy, root=Path(root_directory)),
                legacy.resolve(),
            )

    def test_parent_traversal_is_rejected(self) -> None:
        """確認外部輸入不能用 .. 逃離 MODEL_ARTIFACT_ROOT。"""
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                resolve_artifact_path("clustering/../outside.pkl", root=Path(directory))


class ArtifactPersistenceTests(unittest.TestCase):
    """驗證 artifact 原子寫入、hash 與型別檢查。"""

    def test_save_and_load_with_hash(self) -> None:
        """確認保存後可用 DB hash 驗證並還原模型 metadata。"""
        artifact = WorkspaceTopicArtifact(
            workspace_id=1,
            source_field="effect_summary",
            run_id=2,
            artifact_version=1,
            reducer=None,
            topic_model=None,
            embedding_model="PatentSBERTa",
            embedding_model_version="test",
            preprocessing_version="test",
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.pkl"
            file_hash = save_artifact(artifact, path)
            loaded = load_artifact(path, expected_hash=file_hash)
        self.assertEqual(loaded.workspace_id, artifact.workspace_id)
        self.assertEqual(loaded.source_field, artifact.source_field)


if __name__ == "__main__":
    unittest.main()
