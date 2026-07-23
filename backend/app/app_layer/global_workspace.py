"""全庫 workspace（專利總覽）的建立、識別、成員同步與護欄。

2026-07-23 定案「專利總覽＝全庫 workspace」：保留一個特殊 workspace，成員為全部專利，
分群／報表／AI 全部沿用既有機制（0028 以 workspaces.is_global 標記，partial unique index
保證只有一個）。

三件事集中在本模組，避免識別邏輯散落各處：

- **識別**：一律查 `is_global` 欄（`get_global_workspace_id`），**不得**寫死 workspace_id
  （不假設是 id=0 或 1）——使用者紅線。
- **成員同步**：每次匯入完把該批 patent_ids union 進全庫（`sync_global_workspace_patents`），
  沿用 `add_patents_to_workspace` 的 union 去重，不重複收錄。
- **護欄**：全庫 workspace 不得被刪除／改名／手動增減成員，由 `assert_not_global` 統一擋。
"""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.clustering.sources import source_fields
from backend.app.db.connection import get_pool


# 全庫 workspace 的顯示名稱。識別**不靠名稱**（一律查 is_global 欄），此值只供 UI 顯示；
# 名稱撞既有 workspace 時仍會被 ux_workspaces_name 擋下，屬正確行為（請使用者改名讓路）。
GLOBAL_WORKSPACE_NAME = "專利總覽（全庫）"


class GlobalWorkspaceProtectedError(ValueError):
    """試圖刪除／改名／手動增減全庫 workspace（→ 409／403，屬受保護物件）。"""

    def __init__(self, workspace_id: int, action: str) -> None:
        self.workspace_id = workspace_id
        self.action = action
        super().__init__(
            f"global workspace is protected: cannot {action} workspace {workspace_id}"
        )


def get_global_workspace_id() -> int | None:
    """回傳全庫 workspace 的 id；尚未建立時回 None。

    識別依據是 `is_global` 欄位（DB 層 partial unique index 保證至多一列），
    不依賴 workspace_id 數值或名稱。
    """
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT workspace_id FROM app_layer.workspaces WHERE is_global LIMIT 1"
            )
            row = cur.fetchone()
    return int(row["workspace_id"]) if row else None


def is_global_workspace(workspace_id: int) -> bool:
    """判斷指定 workspace 是否為全庫 workspace（查欄位，不猜 id）。"""
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT is_global FROM app_layer.workspaces WHERE workspace_id = %s",
                (workspace_id,),
            )
            row = cur.fetchone()
    return bool(row and row["is_global"])


def assert_not_global(workspace_id: int, *, action: str) -> None:
    """護欄：目標若為全庫 workspace 即 raise，供刪除／改名／手動增減成員的路徑呼叫。

    全庫 workspace 的成員只能由匯入自動同步（sync_global_workspace_patents），
    使用者不得手動增減；名稱與存在性也不開放變更，否則專利總覽會失去唯一落點。
    """
    if is_global_workspace(workspace_id):
        raise GlobalWorkspaceProtectedError(workspace_id, action)


def ensure_global_workspace() -> int:
    """取得全庫 workspace 的 id，不存在則建立（冪等）。

    併發下兩個匯入 job 可能同時發現不存在而都嘗試建立；此時 partial unique index
    （ux_workspaces_is_global）會讓後者拿到 UniqueViolation，改回查既有那筆即可——
    這正是把唯一性放在 DB 層的用途，程式不需另外加鎖。
    """
    existing = get_global_workspace_id()
    if existing is not None:
        return existing
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO app_layer.workspaces
                        (workspace_name, patent_ids_json, settings_json, is_global)
                    VALUES (%s, '[]'::jsonb, jsonb_strip_nulls(%s::jsonb), true)
                    RETURNING workspace_id
                    """,
                    (
                        GLOBAL_WORKSPACE_NAME,
                        Jsonb(
                            {
                                "description": "所有匯入專利的總和，成員由匯入自動同步",
                                "created_by": "system",
                                "purpose": "general",
                                "parameters": {"clustering_sources": list(source_fields())},
                            }
                        ),
                    ),
                )
                workspace_id = int(cur.fetchone()["workspace_id"])
            except psycopg.errors.UniqueViolation:
                # 併發下已被另一個流程建好；rollback 後改查既有那筆。
                conn.rollback()
                created = get_global_workspace_id()
                if created is None:
                    raise
                return created
        conn.commit()
    return workspace_id


def sync_global_workspace_patents(patent_ids: list[int]) -> int:
    """把這批專利 union 進全庫 workspace（不存在則建立），回全庫 workspace_id。

    union 去重沿用 app_layer.workspace_create.add_patents_to_workspace（FOR UPDATE 鎖列，
    既有成員在前、新成員接後、重複只留一次），不自寫第二套併集邏輯。護欄擋的是**使用者
    手動**增減成員，此處是系統自動同步，故直接走底層寫入函式。
    """
    from backend.app.app_layer import workspace_create

    workspace_id = ensure_global_workspace()
    unique_ids = list(dict.fromkeys(int(value) for value in patent_ids))
    if unique_ids:
        workspace_create.add_patents_to_workspace(
            workspace_id=workspace_id, patent_ids=unique_ids, _allow_global=True)
    return workspace_id
