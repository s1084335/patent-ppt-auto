"""WorkflowOutputsRepository：app_layer.workflow_outputs 的版本化寫入／讀取。

契約（0021 定案）：
- data_json 走版本化 append：同 (run_id, output_type) 新版本 = max(version)+1，
  只 INSERT 不 UPDATE，舊版本值永不覆蓋（PK (run_id, output_type, version) 保底）。
- artifact_manifest_json 只存圖檔/PPT 資訊（.png/.svg/.jpg/.jpeg/.pptx）；
  表格數據一律走 data_json，不得塞進 artifact manifest。
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

# artifact 只准圖表圖檔與 PPT；CSV／表格數據必須走 data_json
ALLOWED_ARTIFACT_SUFFIXES = {".png", ".svg", ".jpg", ".jpeg", ".pptx"}


def _validate_artifact_manifest(manifest: dict[str, Any]) -> None:
    """驗證 manifest 只描述圖檔/PPT；artifact_key 缺失或副檔名非法即拒絕。"""
    keys = [manifest.get("artifact_key")]
    keys += [f.get("artifact_key") for f in manifest.get("files", [])]
    keys = [k for k in keys if k]
    if not keys:
        raise ValueError("artifact manifest requires artifact_key")
    for key in keys:
        suffix = PurePosixPath(str(key)).suffix.lower()
        if suffix not in ALLOWED_ARTIFACT_SUFFIXES:
            raise ValueError(
                f"artifact manifest only accepts chart images/PPT {sorted(ALLOWED_ARTIFACT_SUFFIXES)}, "
                f"got {key!r}")


class PostgresWorkflowOutputsRepository:
    """以 psycopg 操作 app_layer.workflow_outputs（append-only 版本化）。"""

    def __init__(self, connect_kwargs: dict[str, Any] | None = None):
        self._connect_kwargs = connect_kwargs

    def _connect(self):
        import psycopg

        from backend.app.db.connection import get_connection_kwargs

        return psycopg.connect(**(self._connect_kwargs or get_connection_kwargs()))

    def _append(self, run_id: int, output_type: str, *, data_json: dict[str, Any] | None,
                artifact_manifest_json: dict[str, Any] | None) -> int:
        """共用 append：版本 = 現有 max+1，單一 INSERT，一個 transaction。"""
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            version = conn.execute(
                """
                INSERT INTO app_layer.workflow_outputs
                    (run_id, output_type, version, data_json, artifact_manifest_json)
                VALUES (%s, %s,
                    COALESCE((SELECT max(version) FROM app_layer.workflow_outputs
                              WHERE run_id = %s AND output_type = %s), 0) + 1,
                    %s, %s)
                RETURNING version
                """,
                (run_id, output_type, run_id, output_type,
                 Jsonb(data_json or {}), Jsonb(artifact_manifest_json or {})),
            ).fetchone()[0]
            conn.commit()
        return version

    def append_output(self, run_id: int, output_type: str, data_json: dict[str, Any]) -> int:
        """版本化寫入結構化結果（data_json），回傳新版本號；不覆蓋既有版本。"""
        return self._append(run_id, output_type, data_json=data_json, artifact_manifest_json=None)

    def append_artifact_output(self, run_id: int, output_type: str,
                               manifest: dict[str, Any]) -> int:
        """版本化寫入 artifact 資訊；manifest 僅接受圖檔/PPT，違規拒絕且不落任何列。"""
        _validate_artifact_manifest(manifest)
        return self._append(run_id, output_type, data_json=None, artifact_manifest_json=manifest)

    def get_output(self, run_id: int, output_type: str,
                   version: int | None = None) -> dict[str, Any] | None:
        """讀取指定版本（未指定取最新）；不存在回 None。"""
        sql = (
            "SELECT version, data_json, artifact_manifest_json, exported_at "
            "FROM app_layer.workflow_outputs WHERE run_id = %s AND output_type = %s "
        )
        params: list[Any] = [run_id, output_type]
        if version is None:
            sql += "ORDER BY version DESC LIMIT 1"
        else:
            sql += "AND version = %s"
            params.append(version)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return {"version": row[0], "data_json": row[1],
                "artifact_manifest_json": row[2], "exported_at": row[3]}
