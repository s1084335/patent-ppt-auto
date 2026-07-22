"""Workspace 組合建立服務（單一 transaction）。

patent-backend-worker-plan.md「Workspace 組合建立需求」：使用者選 2 個以上既有
workspace，建立一個新的組合 workspace，取所有來源專利的聯集並去重。

設計約束：
- 不修改 clustering/workspace_service.py。因需在「單一 transaction」內完成 workspace
  聯集與 lineage 寫入，而 create_workspace 自帶獨立連線無法併入，故 compose 自足地直接寫。
- 0021 對齊：來源成員與新 workspace 成員皆走 app_layer.workspaces.patent_ids_json
  （bigint 陣列，jsonb_array_elements→::bigint 讀、去重後聯集寫回，與讀取路徑形狀一致）；
  0018 的 workspace_patents 明細表已下沉，不再讀寫。compose 來源明細寫
  legacy_0021.workspace_compose_sources（讀取路徑即從此讀，欄位以 information_schema
  實有者 source_patent_count/created_at 為準）。
- 不動 core_layer.patents 原始值；不繼承來源 topics／模型 artifact；不建立任何分群 job
  （分群由後續 job 另行觸發）。來源 workspace 的成員、topics、assignment 完全不動。
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
    """寫入組合 lineage 到 legacy_0021.workspace_compose_sources（讀取路徑即從此讀）。

    抽成函式供測試注入失敗，驗證單一 transaction rollback。created_at 由表 DEFAULT now()
    填入，不在此顯式指定。
    """
    cur.executemany(
        """
        INSERT INTO legacy_0021.workspace_compose_sources
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

            # 各來源專利件數（去重前）與聯集去重成員，皆由 patent_ids_json 求得
            # （jsonb_array_elements→::bigint，與讀取路徑對 patent_ids_json 的形狀一致）。
            # 各來源件數＝該來源陣列長度；供回傳與 lineage。
            cur.execute(
                "SELECT workspace_id, jsonb_array_length(patent_ids_json) AS n "
                "FROM app_layer.workspaces WHERE workspace_id = ANY(%s)",
                (unique_sources,),
            )
            per_source = {int(row["workspace_id"]): int(row["n"]) for row in cur.fetchall()}
            source_rows = [(wid, per_source.get(wid, 0)) for wid in unique_sources]

            # 聯集去重：攤平各來源 patent_ids_json、轉 bigint、去重排序成 bigint 陣列
            # （空來源自然貢獻 0 筆；全空聯集為空陣列，形狀仍為合法 jsonb '[]'）。
            cur.execute(
                """
                SELECT COALESCE(
                    jsonb_agg(pid ORDER BY pid),
                    '[]'::jsonb
                ) AS union_ids
                FROM (
                    SELECT DISTINCT (m.pid)::bigint AS pid
                    FROM app_layer.workspaces w
                    JOIN LATERAL jsonb_array_elements(w.patent_ids_json) AS m(pid) ON TRUE
                    WHERE w.workspace_id = ANY(%s)
                ) u
                """,
                (unique_sources,),
            )
            union_ids = cur.fetchone()["union_ids"]
            union_count = len(union_ids)

            # 建立新 workspace：聯集成員直接進 patent_ids_json；審計與 clustering_sources
            # 慣例（含 composed_from）承載於 settings_json。workspace_name 唯一；撞名轉成
            # 可讀衝突（交易會 rollback，不留半成品）。
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
                        Jsonb(union_ids),
                        Jsonb(
                            {
                                "description": description,
                                "created_by": created_by,
                                "parameters": {"clustering_sources": list(source_fields())},
                                "composed_from": unique_sources,
                            }
                        ),
                    ),
                )
                row = cur.fetchone()
            except psycopg.errors.UniqueViolation as exc:
                raise WorkspaceNameConflictError(name) from exc
            new_workspace_id = int(row["workspace_id"])

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
