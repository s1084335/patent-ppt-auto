"""不相干專利排除清單：寫入排除表 ＋ 取成員單一函式收口。

規格唯一來源：irrelevant-patent-filter-spec.md 第 58-76、128-140 行。

本模組收口兩件事，避免排除語意散落各處：

1. **排除表存取**（derived_layer.workspace_excluded_patents，0035 migration）：
   exclude_patents 回寫標記（可追溯、可反悔）、excluded_patent_ids 讀回。

2. **取成員單一函式收口**（規格第 76 行，關鍵）：現有讀取點分散
   （clustering/runner.py load_clustering_corpus、workspace_service._workspace_patent_ids 等），
   若各自扣除排除清單必有遺漏。故：
   - `analysis_member_patent_ids`：**分析用**取成員——扣除排除清單。分群、報表統計等
     一律走這條，被剔除者不參與分析。
   - `display_member_patent_ids`：**顯示用**取成員——**不扣**。使用者仍要看得到被排除的
     專利與其標記。
   - ⚠ **全庫 workspace 不扣除**（規格第 62-64 行）：排除是 workspace 級，同一 patent_id 在
     特定 ws 被排除、在全庫仍照常參與。analysis_member_patent_ids 對全庫直接回全部成員。

3. **剔除不重跑分群**（規格第 128-140 行）：exclude_patents 只寫排除表、移除該筆
   topic_assignments、移出 workspace patent_ids_json；**model artifact 完全不動、
   distance_to_centroid 不重算**——「不重跑」的關鍵是絕不碰 artifact 與既有向量。

conn 可注入：測試餵拋棄式 DB 連線、正式走連線池。所有寫入不自行 commit（交由呼叫端
控制交易邊界，與既有 store 一致由呼叫端 conn.commit）。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Sequence

from backend.app.db.connection import get_pool


@contextmanager
def _conn_ctx(conn: Any | None):
    """統一連線來源：注入的 conn 直接用（不代管交易）；未注入時借連線池。"""
    if conn is not None:
        yield conn
    else:
        with get_pool().connection() as pooled:
            yield pooled


def _workspace_row(cur: Any, workspace_id: int) -> tuple[list[int], bool]:
    """讀 workspace 的成員清單（patent_ids_json）與 is_global 旗標（同一查詢，不多開連線）。

    成員來源 0021：workspaces.patent_ids_json（workspace_patents 已刪）。
    is_global 一起讀出，避免收口函式為判斷全庫再開一條連線（沿 global_workspace 查欄不猜 id）。
    """
    cur.execute(
        "SELECT patent_ids_json, is_global FROM app_layer.workspaces WHERE workspace_id = %s",
        (workspace_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"workspace not found: {workspace_id}")
    if isinstance(row, dict):
        raw_ids, is_global = row["patent_ids_json"], row["is_global"]
    else:
        raw_ids, is_global = row[0], row[1]
    member_ids = [int(item) for item in (raw_ids or [])]
    return member_ids, bool(is_global)


def excluded_patent_ids(workspace_id: int, *, conn: Any | None = None) -> set[int]:
    """讀某 workspace 的排除 patent_id 集合（供扣除；順序無關故回 set）。"""
    with _conn_ctx(conn) as active:
        with active.cursor() as cur:
            cur.execute(
                "SELECT patent_id FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id = %s",
                (workspace_id,),
            )
            rows = cur.fetchall()
    return {int(r[0] if not isinstance(r, dict) else r["patent_id"]) for r in rows}


def analysis_member_patent_ids(workspace_id: int, *, conn: Any | None = None) -> list[int]:
    """**分析用**取成員：扣除排除清單（全庫例外——不扣）。

    分群、報表統計等「精準分析」路徑一律走這條。回傳保留 patent_ids_json 的原順序
    （只濾除、不重排）。全庫 workspace 直接回全部成員（規格第 62-64 行：全庫是總覽、
    對 A 不相干者對全庫可能相干，故全庫不套此限制）。
    """
    with _conn_ctx(conn) as active:
        with active.cursor() as cur:
            member_ids, is_global = _workspace_row(cur, workspace_id)
            if is_global:
                # 全庫不扣除——同一 patent_id 在特定 ws 被排除、在全庫仍照常參與。
                return member_ids
            cur.execute(
                "SELECT patent_id FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id = %s",
                (workspace_id,),
            )
            excluded = {
                int(r[0] if not isinstance(r, dict) else r["patent_id"])
                for r in cur.fetchall()
            }
    return [pid for pid in member_ids if pid not in excluded]


def display_member_patent_ids(workspace_id: int, *, conn: Any | None = None) -> list[int]:
    """**顯示用**取成員：**不扣**排除清單（使用者仍要看得到被排除者與標記）。

    與 analysis_member_patent_ids 刻意分開：顯示路徑（前端列表、審視被排除專利）走這條，
    永遠回全部成員。被排除的標記由呼叫端另取 excluded_patent_ids 疊上。
    """
    with _conn_ctx(conn) as active:
        with active.cursor() as cur:
            member_ids, _is_global = _workspace_row(cur, workspace_id)
    return member_ids


def exclude_patents(
    workspace_id: int,
    entries: Iterable[tuple[int, str | None]],
    *,
    conn: Any | None = None,
    remove_assignments: bool = True,
    remove_from_member_ids: bool = False,
) -> int:
    """使用者判定「不相干」後的剔除處理：回寫排除表（可追溯），並落實「不重跑分群」語意。

    entries＝(patent_id, reason) 序列。步驟：
    1. **回寫排除表**（ON CONFLICT 更新理由與時間，複合 PK 天然去重、可反悔）。
       ⚠ 排除表是排除狀態的**唯一事實來源**（規格第 68 行定案「保留成員 ＋ 另記排除清單」，
       而非直接移出 patent_ids_json）——如此顯示用取成員仍看得到被排除者、可反悔。
    2. remove_assignments：移除該筆在本 workspace 各通道的 topic_assignments
       （剔除＝移出該 workspace 分析；⚠ 只刪 assignment，**不動 model artifact、
       不重算 distance_to_centroid**——「不重跑」的關鍵）。
    3. remove_from_member_ids（**預設 False**）：是否連帶從 workspaces.patent_ids_json 移出。
       預設不移出——保留成員讓顯示路徑看得到被排除者（規格第 68 行優先於第 136 行的舊
       「移出成員」機制：line 68 明確定案採「保留成員＋另記排除清單」取代直接移出）。
       僅在呼叫端明確要「連成員清單一起硬移除」時才設 True。

    ⚠ 全庫 workspace 不應被剔除（排除是 workspace 級、全庫照收）；此處不硬擋（呼叫端
    語意上不會對全庫剔除），但全庫的 analysis_member_patent_ids 本就不扣除，即使誤寫也
    不影響全庫分析。回傳實際寫入排除表的筆數。

    不自行 commit：交易邊界交由呼叫端（與既有 store 一致）。
    """
    rows = [(int(pid), reason) for pid, reason in entries]
    if not rows:
        return 0
    patent_ids = [pid for pid, _ in rows]
    written = 0
    with _conn_ctx(conn) as active:
        with active.cursor() as cur:
            # 1. 回寫排除表（可追溯、可反悔）；ON CONFLICT 更新理由與時間。
            for pid, reason in rows:
                cur.execute(
                    "INSERT INTO derived_layer.workspace_excluded_patents "
                    "(workspace_id, patent_id, reason) VALUES (%s, %s, %s) "
                    "ON CONFLICT (workspace_id, patent_id) "
                    "DO UPDATE SET reason = EXCLUDED.reason, excluded_at = now()",
                    (workspace_id, pid, reason),
                )
                written += 1

            # 2. 移除該筆在本 workspace 的 topic_assignments（⚠ 不碰 artifact／不重算距離）。
            #    assignments 經 topic_runs → workflow_runs 歸屬到 workspace，故以子查詢定位。
            if remove_assignments:
                cur.execute(
                    """
                    DELETE FROM derived_layer.topic_assignments ta
                    USING derived_layer.topic_runs tr,
                          app_layer.workflow_runs wr
                    WHERE ta.run_id = tr.run_id
                      AND tr.workflow_run_id = wr.run_id
                      AND wr.workspace_id = %s
                      AND ta.patent_id = ANY(%s)
                    """,
                    (workspace_id, patent_ids),
                )

            # 3. 從 workspaces.patent_ids_json 移出被剔除的 id（保留其餘順序）。
            if remove_from_member_ids:
                cur.execute(
                    """
                    UPDATE app_layer.workspaces w
                    SET patent_ids_json = COALESCE(
                        (
                            SELECT jsonb_agg(elem ORDER BY ord)
                            FROM jsonb_array_elements_text(w.patent_ids_json)
                                 WITH ORDINALITY AS t(elem, ord)
                            WHERE (elem)::bigint <> ALL(%s)
                        ),
                        '[]'::jsonb
                    )
                    WHERE w.workspace_id = %s
                    """,
                    (patent_ids, workspace_id),
                )
    return written
