"""Workspace 組合建立服務（單一 transaction）。

patent-backend-worker-plan.md「Workspace 組合建立需求」：使用者選 2 個以上既有
workspace，建立一個新的組合 workspace，取所有來源專利的聯集並去重。

設計約束：
- 不修改 clustering/workspace_service.py。因需在「單一 transaction」內完成 workspace、
  workspace_patents 聯集與 lineage 三段寫入，而 create_workspace 自帶獨立連線無法併入，
  故 compose 自足地直接寫這三段。
- 只寫 app_layer 表；不動 core_layer.patents 原始值；不繼承來源 topics／模型 artifact；
  不建立任何分群 job（分群由後續 job 另行觸發）。
- 來源 workspace 的成員、topics、assignment 完全不動。
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.clustering.sources import source_fields
from backend.app.db.connection import get_pool


class ComposeValidationError(ValueError):
    """來源不足兩個（去重後）或名稱無效等輸入問題（→ 422）。"""


class WorkspaceNameConflictError(ValueError):
    """新 workspace 名稱已存在（workspaces.workspace_name 唯一）（→ 409）。"""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"workspace name already exists: {name!r}")


class SourceWorkspaceNotFoundError(ValueError):
    """有來源 workspace_id 不存在（→ 404）。"""

    def __init__(self, ids: list[int]) -> None:
        self.ids = ids
        super().__init__(f"source workspaces not found: {ids}")


class SourceWorkspaceNotActiveError(ValueError):
    """有來源 workspace 非 active 狀態（→ 409）。"""

    def __init__(self, ids: list[int]) -> None:
        self.ids = ids
        super().__init__(f"source workspaces not active: {ids}")


def _insert_lineage(cur: Any, workspace_id: int, source_rows: list[tuple[int, int]]) -> None:
    """寫入組合 lineage；抽成函式供測試注入失敗，驗證單一 transaction rollback。"""
    cur.executemany(
        """
        INSERT INTO app_layer.workspace_compose_sources
            (workspace_id, source_workspace_id, source_patent_count)
        VALUES (%s, %s, %s)
        """,
        [(workspace_id, source_id, count) for source_id, count in source_rows],
    )


def compose_workspaces(
    *,
    workspace_name: str,
    source_workspace_ids: list[int],
    created_by: str = "api-user",
    description: str | None = None,
) -> dict[str, Any]:
    """由多個來源 workspace 建立組合 workspace，取專利聯集去重，全程單一 transaction。

    回傳 workspace_id、各來源件數、重複件數與聯集件數。任一步失敗整筆 rollback，
    不留半完成 workspace。
    """
    # 去重來源 ID（保序）；重複來源 ID 視為同一個，去重後仍需 >= 2。
    unique_sources = list(dict.fromkeys(int(value) for value in source_workspace_ids))
    if len(unique_sources) < 2:
        raise ComposeValidationError("compose requires at least 2 distinct source workspaces")
    name = workspace_name.strip()
    if not name:
        raise ComposeValidationError("workspace_name must not be empty")

    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # SET TRANSACTION 必須是第一個 SQL；只固定本次交易 snapshot，不污染連線池。
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")

            # 驗證來源存在且 active；FOR SHARE 鎖住來源列，避免 compose 期間被改狀態或刪除。
            cur.execute(
                "SELECT workspace_id, status FROM app_layer.workspaces "
                "WHERE workspace_id = ANY(%s) FOR SHARE",
                (unique_sources,),
            )
            status_by_id = {int(row["workspace_id"]): row["status"] for row in cur.fetchall()}
            missing = [wid for wid in unique_sources if wid not in status_by_id]
            if missing:
                raise SourceWorkspaceNotFoundError(missing)
            inactive = [wid for wid in unique_sources if status_by_id[wid] != "active"]
            if inactive:
                raise SourceWorkspaceNotActiveError(inactive)

            # 各來源專利件數（去重前），供回傳與 lineage。
            cur.execute(
                "SELECT workspace_id, count(*) AS n FROM app_layer.workspace_patents "
                "WHERE workspace_id = ANY(%s) GROUP BY workspace_id",
                (unique_sources,),
            )
            per_source = {int(row["workspace_id"]): int(row["n"]) for row in cur.fetchall()}
            source_rows = [(wid, per_source.get(wid, 0)) for wid in unique_sources]

            # 建立新 workspace（clustering_sources 與 create_workspace 一致；記 composed_from）。
            # workspace_name 唯一；撞名轉成可讀衝突（交易會 rollback，不留半成品）。
            try:
                cur.execute(
                    """
                    INSERT INTO app_layer.workspaces
                        (workspace_name, description, created_by, parameters_json)
                    VALUES (%s, %s, %s, %s)
                    RETURNING workspace_id
                    """,
                    (
                        name,
                        description,
                        created_by,
                        Jsonb(
                            {
                                "clustering_sources": list(source_fields()),
                                "composed_from": unique_sources,
                            }
                        ),
                    ),
                )
                row = cur.fetchone()
            except psycopg.errors.UniqueViolation as exc:
                raise WorkspaceNameConflictError(name) from exc
            new_workspace_id = int(row["workspace_id"])

            # 聯集去重寫入成員。source_type 只能是 manual/import/filter/incremental_import
            # （workspace_patents CHECK），故用 'manual'；組合來源記在 lineage 表，不靠此欄。
            cur.execute(
                """
                INSERT INTO app_layer.workspace_patents
                    (workspace_id, patent_id, source_type, added_by)
                SELECT %s, u.patent_id, 'manual', %s
                FROM (
                    SELECT DISTINCT patent_id
                    FROM app_layer.workspace_patents
                    WHERE workspace_id = ANY(%s)
                ) u
                """,
                (new_workspace_id, created_by, unique_sources),
            )
            union_count = int(cur.rowcount)

            _insert_lineage(cur, new_workspace_id, source_rows)
        conn.commit()

    total_source_patents = sum(per_source.get(wid, 0) for wid in unique_sources)
    return {
        "workspace_id": new_workspace_id,
        "source_counts": [
            {"source_workspace_id": wid, "patent_count": per_source.get(wid, 0)}
            for wid in unique_sources
        ],
        "duplicate_count": total_source_patents - union_count,
        "union_count": union_count,
    }
