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


# 匯入批次用途標籤（2026-07-22 定案）：一般匯入 vs 案件比對匯入共用同一套匯入機制，
# 用途標籤讓專利總覽可區分。落 workspace settings_json.purpose（無需 migration，通用不綁死
# 案件比對；任何匯入批次皆可標）；未指定時預設 general。
DEFAULT_PURPOSE = "general"
PURPOSES: frozenset[str] = frozenset({"general", "case_comparison"})


def _validate_purpose(purpose: str | None) -> str:
    """驗證並正規化用途標籤；缺省回 general，非白名單值 → 422。"""
    value = (purpose or DEFAULT_PURPOSE).strip() or DEFAULT_PURPOSE
    if value not in PURPOSES:
        raise WorkspaceValidationError(f"unsupported purpose: {value!r}")
    return value


def create_workspace(
    *,
    workspace_name: str,
    patent_ids: list[int],
    created_by: str = "api-user",
    description: str | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    """建立一般 workspace 並寫入明確專利集合，全程單一 transaction。

    回傳 {workspace_id, workspace_name, patent_count, purpose}。去重後專利集為空或有專利
    不存在回 422（ValueError 子類）；撞名回 409（WorkspaceNameConflictError）；任一步失敗
    整筆 rollback，不留半完成 workspace。purpose（general／case_comparison）落 settings_json，
    供專利總覽過濾/顯示；缺省 general。
    """
    # 去重（保序）；不改核心專利值，只用於成員寫入與存在性檢查。
    unique_patent_ids = list(dict.fromkeys(int(value) for value in patent_ids))
    if not unique_patent_ids:
        raise WorkspaceValidationError("patent_ids must not be empty after dedup")
    name = workspace_name.strip()
    if not name:
        raise WorkspaceValidationError("workspace_name must not be empty")
    purpose_value = _validate_purpose(purpose)

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
            # 審計、用途標籤（purpose）與 clustering_sources 慣例承載於 settings_json
            # （jsonb_strip_nulls 去 None，與 0021 migration 回填 settings_json 的欄名一致）。
            # 撞名（ux_workspaces_name）轉成可讀 409，交易隨例外 rollback，不留半成品。
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
                                "purpose": purpose_value,
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
        "purpose": purpose_value,
    }


class WorkspaceNotFoundError(ValueError):
    """目標 workspace 不存在（→ 404）。"""

    def __init__(self, workspace_id: int) -> None:
        self.workspace_id = workspace_id
        super().__init__(f"workspace not found: {workspace_id}")


def add_patents_to_workspace(
    *,
    workspace_id: int,
    patent_ids: list[int],
    _allow_global: bool = False,
) -> dict[str, Any]:
    """把新專利 union 進既有 workspace 的 patent_ids_json（去重、保序），全程單一 transaction。

    對應「匯入帶既有 workspace」：成員陣列 union 去重（不重複收錄）。回傳
    {workspace_id, added_count, patent_count}。workspace 不存在回 404；空 patent_ids 為 no-op
    （added_count=0）；不驗證專利存在（匯入路徑的專利剛寫入 core_layer，且既有成員可能已被
    其他流程移除，重點是集合 union 冪等）。FOR UPDATE 鎖住該列避免併發 union 遺失。

    護欄（2026-07-23）：全庫 workspace 的成員只由匯入自動同步，不得手動增減，故預設擋下
    is_global 的 workspace。`_allow_global` 只給 global_workspace.sync_global_workspace_patents
    這條系統同步路徑使用，不對外開放。
    """
    if not _allow_global:
        from backend.app.app_layer import global_workspace

        global_workspace.assert_not_global(workspace_id, action="add patents to")
    incoming = list(dict.fromkeys(int(value) for value in patent_ids))
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT patent_ids_json FROM app_layer.workspaces "
                "WHERE workspace_id = %s FOR UPDATE",
                (workspace_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise WorkspaceNotFoundError(workspace_id)
            existing = [int(v) for v in (row["patent_ids_json"] or [])]
            existing_set = set(existing)
            added = [pid for pid in incoming if pid not in existing_set]
            merged = existing + added
            if added:
                cur.execute(
                    "UPDATE app_layer.workspaces SET patent_ids_json = %s "
                    "WHERE workspace_id = %s",
                    (Jsonb(merged), workspace_id),
                )
        conn.commit()
    return {
        "workspace_id": workspace_id,
        "added_count": len(added),
        "patent_count": len(merged),
    }
