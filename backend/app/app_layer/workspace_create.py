"""建立一般 workspace 的寫入服務（單一 transaction）。

對應 Backend Contract Readiness Matrix #2「建立一般 workspace」。設計約束與 compose
一致：不改 clustering/workspace_service.py（避免踩分群模組邊界），在 app_layer 自足地
於單一 transaction 內完成「驗證專利存在 → 建 workspace（成員即寫入 patent_ids_json）」，
任一步失敗整筆 rollback，不留半成品 workspace。

- patent_ids 去重後不可為空，且必須全部存在於 core_layer.patents，否則 422。
- workspace_name 唯一（ux_workspaces_name），撞名回 409。
- 0021 對齊：成員專利存 app_layer.workspaces.patent_ids_json（bigint 陣列，去重、
  jsonb_array_elements→::bigint 讀），不再寫已下沉 legacy_0021 的 workspace_patents；
  審計資訊（created_by/description）與分群 clustering_sources 慣例改承載於 settings_json
  （沿讀取路徑對 settings_json 的既有用法，不新增資料庫欄）。
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.clustering.sources import source_fields
from backend.app.db.connection import get_pool


class WorkspaceValidationError(ValueError):
    """輸入問題：去重後 patent_ids 為空、名稱空白等（→ 422）。"""


class PatentsNotFoundError(WorkspaceValidationError):
    """有 patent_id 不存在於 core_layer.patents（→ 422，屬輸入錯誤）。"""

    def __init__(self, ids: list[int]) -> None:
        self.ids = ids
        super().__init__(f"patent_ids not found in core_layer.patents: {ids}")


class WorkspaceNameConflictError(ValueError):
    """workspace_name 已存在（workspaces.workspace_name 唯一）（→ 409）。"""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"workspace name already exists: {name!r}")


def create_workspace(
    *,
    workspace_name: str,
    patent_ids: list[int],
    created_by: str = "api-user",
    description: str | None = None,
) -> dict[str, Any]:
    """建立一般 workspace 並寫入明確專利集合，全程單一 transaction。

    回傳 {workspace_id, workspace_name, patent_count}。去重後專利集為空或有專利不存在
    回 422（ValueError 子類）；撞名回 409（WorkspaceNameConflictError）；任一步失敗整筆
    rollback，不留半完成 workspace。
    """
    # 去重（保序）；不改核心專利值，只用於成員寫入與存在性檢查。
    unique_patent_ids = list(dict.fromkeys(int(value) for value in patent_ids))
    if not unique_patent_ids:
        raise WorkspaceValidationError("patent_ids must not be empty after dedup")
    name = workspace_name.strip()
    if not name:
        raise WorkspaceValidationError("workspace_name must not be empty")

    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # 先驗證所有專利存在，缺任一個即 422，且此時尚未寫入任何資料。
            cur.execute(
                "SELECT id FROM core_layer.patents WHERE id = ANY(%s)",
                (unique_patent_ids,),
            )
            existing = {int(row["id"]) for row in cur.fetchall()}
            missing = [pid for pid in unique_patent_ids if pid not in existing]
            if missing:
                raise PatentsNotFoundError(missing)

            # 建立 workspace：成員直接進 patent_ids_json（0021 已無 workspace_patents 明細表），
            # 審計與 clustering_sources 慣例承載於 settings_json（jsonb_strip_nulls 去 None，
            # 與 0021 migration 回填 settings_json 的欄名一致）。撞名（ux_workspaces_name）
            # 轉成可讀 409，交易隨例外 rollback，不留半成品。
            try:
                cur.execute(
                    """
                    INSERT INTO app_layer.workspaces
                        (workspace_name, patent_ids_json, settings_json)
                    VALUES (
                        %s,
                        %s,
                        jsonb_strip_nulls(%s::jsonb)
                    )
                    RETURNING workspace_id
                    """,
                    (
                        name,
                        Jsonb(unique_patent_ids),
                        Jsonb(
                            {
                                "description": description,
                                "created_by": created_by,
                                "parameters": {"clustering_sources": list(source_fields())},
                            }
                        ),
                    ),
                )
                workspace_id = int(cur.fetchone()["workspace_id"])
            except psycopg.errors.UniqueViolation as exc:
                raise WorkspaceNameConflictError(name) from exc
        conn.commit()

    return {
        "workspace_id": workspace_id,
        "workspace_name": name,
        "patent_count": len(unique_patent_ids),
    }
