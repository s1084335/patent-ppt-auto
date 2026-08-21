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

from psycopg.types.json import Jsonb

from backend.app.db.connection import get_pool
# 🔴 顯示用專利號的唯一定義處——該檔註解記著它曾被複製四份而各自漂移
# （TW 案顯示西元前綴、M 開頭授權案空白）。消費端一律 import，不自寫 COALESCE。
from backend.app.transforms.patent_numbers import display_number_sql


# 需要使用者裁決的 AI 判讀值（繁體中文，對齊
# worker.ai_irrelevant_filter_runner.VALID_VERDICTS）。
# 「相干」不寫入（本就留在原主題）、「無法判斷」不寫入（備註為空、無判讀依據），
# 兩者寫進待複核清單只會製造無意義的待辦。
# ⚠ 這裡刻意不 import runner 常數：clustering 層不依賴 worker 層（避免反向相依），
#   改動時兩邊須同步——由 test_irrelevant_filter_persists_pending 的契約測試把關。
REVIEWABLE_VERDICTS = frozenset({"不相干", "可疑"})


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


def is_global_workspace(workspace_id: int, *, conn: Any | None = None) -> bool:
    """該 workspace 是否為全庫。

    供呼叫端擋掉「對全庫做 workspace 級操作」——排除是 workspace 級，全庫是總覽本就
    該全收（規格第 62-64 行：對 A 不相干的專利，對全庫可能屬另一技術領域、是相干的）。
    全庫的 analysis_member_patent_ids 本就不扣除，對它跑篩選只會白燒 CLI 額度。
    """
    with _conn_ctx(conn) as active:
        with active.cursor() as cur:
            _member_ids, is_global = _workspace_row(cur, workspace_id)
    return is_global


def excluded_patent_ids(workspace_id: int, *, conn: Any | None = None) -> set[int]:
    """讀某 workspace 的**已確定**排除 patent_id 集合（供扣除；順序無關故回 set）。

    ⚠ 只回 status='excluded'。AI 判讀落 status='pending'（草稿、待人工裁決），
    不在此回傳、不影響任何分析——這是「AI 不決定正式資料」的護欄（0036）。
    """
    with _conn_ctx(conn) as active:
        with active.cursor() as cur:
            cur.execute(
                "SELECT patent_id FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id = %s AND status = 'excluded'",
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
            # 只扣 status='excluded'；pending（AI 判讀草稿）照常參與分析。
            cur.execute(
                "SELECT patent_id FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id = %s AND status = 'excluded'",
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


def _snapshot_assignments(
    cur: Any, workspace_id: int, patent_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    """在刪除前快照各專利的主題指派，供之後「放回原主題」還原（0037）。

    回 {patent_id: [{run_id, topic_key, distance}, ...]}——一筆專利在技術／功效
    各通道各有一筆，故為陣列。含 distance_to_centroid：放回時原樣還原、不重算
    （沿「剔除不重跑分群」的精神，放回同樣不重跑）。
    """
    if not patent_ids:
        return {}
    cur.execute(
        """
        SELECT ta.patent_id, ta.run_id, ta.topic_key, ta.distance_to_centroid
        FROM derived_layer.topic_assignments ta
        JOIN derived_layer.topic_runs tr ON tr.run_id = ta.run_id
        JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
        WHERE wr.workspace_id = %s AND ta.patent_id = ANY(%s)
        """,
        (workspace_id, patent_ids),
    )
    snapshot: dict[int, list[dict[str, Any]]] = {}
    for row in cur.fetchall():
        if isinstance(row, dict):
            pid, run_id, topic_key, distance = (
                row["patent_id"], row["run_id"], row["topic_key"], row["distance_to_centroid"])
        else:
            pid, run_id, topic_key, distance = row[0], row[1], row[2], row[3]
        snapshot.setdefault(int(pid), []).append({
            "run_id": int(run_id),
            "topic_key": str(topic_key),
            "distance": float(distance) if distance is not None else None,
        })
    return snapshot


def restore_patents(
    workspace_id: int,
    patent_ids: Iterable[int],
    *,
    conn: Any | None = None,
) -> int:
    """把已排除的專利放回原主題（2026-07-27 使用者要求：預防後悔）。

    步驟：讀 restored_topic_key 快照 → 還原各通道 assignment（含原 distance）
    → 刪除排除列。放回後該筆重新計入分析成員。

    ⚠ 只處理 status='excluded'；pending 不是排除（從未移除 assignment），
    要撤銷待複核項請用 keep_patents。
    ⚠ 原主題已不存在（run 被刪、topic 已停用）時該筆 assignment 還原失敗，
    但仍會移出排除清單——使用者要它回來，不能因為還原不了就繼續關著；
    該筆會變成無主題，可由下次分群重新指派。ON CONFLICT DO NOTHING 讓
    重複放回不炸。

    不自行 commit：交易邊界交由呼叫端。回傳實際放回的筆數。
    """
    ids = [int(pid) for pid in patent_ids]
    if not ids:
        return 0
    with _conn_ctx(conn) as active:
        with active.cursor() as cur:
            cur.execute(
                "SELECT patent_id, restored_topic_key "
                "FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id = %s AND patent_id = ANY(%s) AND status = 'excluded'",
                (workspace_id, ids),
            )
            rows = cur.fetchall()
            if not rows:
                return 0
            restore_ids = []
            for row in rows:
                if isinstance(row, dict):
                    pid, snapshot = row["patent_id"], row["restored_topic_key"]
                else:
                    pid, snapshot = row[0], row[1]
                restore_ids.append(int(pid))
                for item in (snapshot or []):
                    # 原 run 若已不存在，FK 會擋下——用子查詢確認存在才插，
                    # 不讓單一還原失敗炸掉整批放回。
                    cur.execute(
                        """
                        INSERT INTO derived_layer.topic_assignments
                            (run_id, patent_id, topic_key, distance_to_centroid)
                        SELECT %s, %s, %s, %s
                        WHERE EXISTS (
                            SELECT 1 FROM derived_layer.topic_runs WHERE run_id = %s
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        (item.get("run_id"), int(pid), item.get("topic_key"),
                         item.get("distance"), item.get("run_id")),
                    )
            cur.execute(
                "DELETE FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id = %s AND patent_id = ANY(%s) AND status = 'excluded'",
                (workspace_id, restore_ids),
            )
            return len(restore_ids)


def _delete_assignments(cur: Any, workspace_id: int, patent_ids: list[int]) -> None:
    """移除指定專利在本 workspace 各通道的 topic_assignments。

    ⚠ 只刪 assignment，不動 model artifact、不重算 distance_to_centroid——「不重跑分群」
    的關鍵。assignments 經 topic_runs → workflow_runs 歸屬到 workspace，故以子查詢定位。
    人工剔除與 AI 判讀確定排除共用此函式（兩條路徑的「歸到不相干」語意相同）。
    """
    if not patent_ids:
        return
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


def store_ai_verdicts(
    workspace_id: int,
    results: Iterable[dict[str, Any]],
    *,
    conn: Any | None = None,
) -> int:
    """把 ai:irrelevant_filter 的逐筆判讀落為**待複核草稿**（status='pending'）。

    results＝runner 回傳的 results 序列，每筆需含 patent_id，可含 verdict／reason。

    ⚠ verdict 為**繁體中文三分**（ai_irrelevant_filter_runner.VALID_VERDICTS：
    「相干」「可疑」「不相干」），另有程式判定的「無法判斷」（備註為空）。
    只寫入需要使用者裁決者——「不相干」與「可疑」；「相干」本就該留在原主題、
    「無法判斷」沒有判讀依據，兩者寫進來只會製造無意義的待辦。

    ⚠ 一律寫 status='pending'、source='ai'：AI 寫得進 pending，寫不進 excluded。
    正式排除必須經使用者按「確定」（confirm_exclusions）——這是 workflows.md
    「AI 只輔助、不決定正式資料」在本流程的落實。

    ⚠ 不覆蓋**已確定排除**者：ON CONFLICT 只在既有列為 'pending' 或 'kept' 時更新，
    已 excluded 的列保持原狀——重跑判讀不得把使用者的決定打回草稿。

    🔴 **`kept` 會被覆蓋是刻意的**（2026-08-21 使用者裁決）：AI 判讀的依據是
    「這一筆在它所屬主題裡最不像」，而主題來自分群。重跑通常伴隨重新分群，
    主題結構變了，前次「保留」的判斷基礎已不存在 ⇒ 重新判讀有意義。

    ⚠ **初階篩選相反**：它跳過 kept（見 `prefilter.decisions.apply_prefilter`）
    ——判斷依據是關鍵字比對，同樣的詞與資料答案必定相同，重問等於騷擾。
    「誰決定要不要重問」寫在**寫入端**而非保留端，因為理由屬於「這條線的
    判讀依據會不會變」，那是寫入端的知識。

    不自行 commit：交易邊界交由呼叫端（與 exclude_patents 一致）。回傳實際寫入筆數。
    """
    rows = []
    for item in results:
        pid = item.get("patent_id")
        if pid is None:
            continue
        verdict = str(item.get("verdict") or "").strip()
        if verdict not in REVIEWABLE_VERDICTS:
            continue
        rows.append((int(pid), verdict, str(item.get("reason") or "").strip() or None))
    if not rows:
        return 0
    # 一次 executemany 批次寫入，不逐筆往返——AI 判讀動輒數十上百筆。
    with _conn_ctx(conn) as active:
        with active.cursor() as cur:
            cur.executemany(
                "INSERT INTO derived_layer.workspace_excluded_patents "
                "(workspace_id, patent_id, reason, status, source, ai_verdict) "
                "VALUES (%s, %s, %s, 'pending', 'ai', %s) "
                "ON CONFLICT (workspace_id, patent_id) DO UPDATE SET "
                "    reason = EXCLUDED.reason, "
                "    ai_verdict = EXCLUDED.ai_verdict, "
                "    status = 'pending', "
                "    excluded_at = now() "
                # 'kept' 一併覆蓋回 pending——見上方 docstring 的兩條線差異說明。
                "WHERE derived_layer.workspace_excluded_patents.status "
                "      IN ('pending', 'kept')",
                [(workspace_id, pid, reason, verdict) for pid, verdict, reason in rows],
            )
    return len(rows)


def pending_reviews(workspace_id: int, *, conn: Any | None = None) -> list[dict[str, Any]]:
    """列出待複核清單（status='pending'），供前端逐筆呈現「保留／確定」。

    回傳依 patent_id 排序的 dict 序列，含 patent_id／ai_verdict／reason／excluded_at
    （excluded_at 在 pending 階段代表判讀時間）。走 0036 的部分索引，不全表掃。

    ⚠ 另帶 **topic_label（所屬主題）** 與 **patent_note（文獻備註）**
    （2026-07-27 使用者要求）：原本只有 patent_id，光看 ID 判斷不了要保留還是確定。

    ⚠ 2026-08-21 再補 **patent_number（顯示用專利號）** 與 **title**：
    初階篩選發生在**分群之前**，`topic_label` 對它永遠是 NULL，`patent_note` 也
    未必產過——那條線要靠標題判斷（關鍵字通常就命中在標題上）。
    🔴 專利號走 `transforms.patent_numbers.display_number_sql` 唯一定義處：
    該檔註解記著它曾被複製四份而漂移（TW 案顯示西元前綴、M 開頭授權案空白）。
    - 主題**跨 run 取**（DISTINCT ON 每個 patent 最新一筆）——incremental 只寫新增
      專利的 assignment，只查最新 run 會讓舊專利的主題顯示空白。
    - label 由該通道最新「topics 非空」的 run 的 state 解析（incremental run 無 topics）。
    - 兩者皆可為 None（尚未分群／備註未產生），該筆仍要列出，不得漏掉。
    """
    with _conn_ctx(conn) as active:
        with active.cursor() as cur:
            cur.execute(
                """
                WITH latest_assign AS (
                    SELECT DISTINCT ON (ta.patent_id)
                           ta.patent_id, ta.topic_key
                    FROM derived_layer.topic_assignments ta
                    JOIN derived_layer.topic_runs tr ON tr.run_id = ta.run_id
                    JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
                    WHERE wr.workspace_id = %(workspace_id)s
                    ORDER BY ta.patent_id, ta.run_id DESC
                ),
                topic_labels AS (
                    SELECT DISTINCT ON (t.value->>'topic_code')
                           t.value->>'topic_code' AS topic_code,
                           t.value->>'label'      AS label
                    FROM derived_layer.topic_runs tr
                    JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
                    CROSS JOIN LATERAL jsonb_array_elements(
                        COALESCE(tr.topic_state_json->'topics', '[]'::jsonb)) AS t(value)
                    WHERE wr.workspace_id = %(workspace_id)s
                    ORDER BY t.value->>'topic_code', tr.run_id DESC
                )
                -- ⚠ 新欄位一律加在**最後**：下方映射是按位置取值
                --   （row[0..N]），插在中間會讓既有欄位全部位移，
                --   而症狀是「主題欄顯示成 ai」這種看起來像資料錯的東西。
                SELECT ex.patent_id, ex.ai_verdict, ex.reason, ex.excluded_at,
                       tl.label   AS topic_label,
                       p."文獻備註" AS patent_note,
                       ex.source,
                       """ + display_number_sql("p") + """ AS patent_number,
                       p."title"  AS title,
                       p."country_code" AS country_code,
                       ex.scope_verdict, ex.scope_reason
                FROM derived_layer.workspace_excluded_patents ex
                LEFT JOIN latest_assign la ON la.patent_id = ex.patent_id
                LEFT JOIN topic_labels  tl ON tl.topic_code = la.topic_key
                LEFT JOIN core_layer.patents p ON p.id = ex.patent_id
                WHERE ex.workspace_id = %(workspace_id)s AND ex.status = 'pending'
                ORDER BY ex.patent_id
                """,
                {"workspace_id": workspace_id},
            )
            rows = cur.fetchall()
    result = []
    for row in rows:
        if isinstance(row, dict):
            values = (row["patent_id"], row["ai_verdict"], row["reason"],
                      row["excluded_at"], row["topic_label"], row["patent_note"],
                      row["source"], row["patent_number"], row["title"],
                      row["country_code"], row["scope_verdict"],
                      row["scope_reason"])
        else:
            values = tuple(row[i] for i in range(12))
        (patent_id, verdict, reason, reviewed_at, topic_label, note,
         source, patent_number, title, country_code,
         scope_verdict, scope_reason) = values
        result.append({
            "patent_id": int(patent_id),
            "ai_verdict": verdict,
            "reason": reason,
            "reviewed_at": reviewed_at,
            # 供前端逐筆判斷用；尚未分群／備註未產生時為 None，該筆仍要列出。
            "topic_label": topic_label,
            "patent_note": note,
            # 2026-08-21 補：初階篩選發生在分群之前，topic_label 對它永遠是 None、
            # patent_note 也未必產過——那條線靠標題與專利號判斷。
            "source": source,
            "patent_number": patent_number,
            "title": title,
            "country_code": country_code,
            # PRE-008（2026-08-21）：AI 對「與整批範圍的關係」的建議。
            # 🔴 與 `reason`（為什麼被列入）分欄——使用者要分得出
            # 「為什麼被抓到」與「為什麼建議剔除」。
            # ⚠ None＝尚未產生建議；'no_basis'＝跑過但三欄皆空。
            # 兩者前端必須顯示成不同的東西，不得都留白。
            "scope_verdict": scope_verdict,
            "scope_reason": scope_reason,
        })
    return result


def excluded_patent_rows(workspace_id: int, *, conn: Any | None = None) -> list[dict[str, Any]]:
    """列出「不相干」桶的內容（status='excluded'），供前端檢視。

    人工剔除（source='manual'）與 AI 判讀經使用者確定（source='ai'）者都在此——
    2026-07-27 使用者定案：兩種來源最終都要出現在「不相干」標籤。
    帶 source 供前端區分來源、ai_verdict 供追溯 AI 原始判定。
    與 excluded_patent_ids 分開：那條回 set 供扣除運算，這條回完整列供顯示。
    """
    with _conn_ctx(conn) as active:
        with active.cursor() as cur:
            cur.execute(
                "SELECT patent_id, source, reason, ai_verdict, excluded_at "
                "FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id = %s AND status = 'excluded' "
                "ORDER BY patent_id",
                (workspace_id,),
            )
            rows = cur.fetchall()
    result = []
    for row in rows:
        if isinstance(row, dict):
            result.append({
                "patent_id": int(row["patent_id"]),
                "source": row["source"],
                "reason": row["reason"],
                "ai_verdict": row["ai_verdict"],
                "excluded_at": row["excluded_at"],
            })
        else:
            result.append({
                "patent_id": int(row[0]),
                "source": row[1],
                "reason": row[2],
                "ai_verdict": row[3],
                "excluded_at": row[4],
            })
    return result


def confirm_exclusions(
    workspace_id: int,
    patent_ids: Iterable[int],
    *,
    conn: Any | None = None,
) -> int:
    """使用者按「確定」：pending → excluded，並移除 topic_assignments（歸到「不相干」）。

    ⚠ source 保持原值（不改寫成 'manual'）：供追溯這筆是 AI 建議後經人工確認，
    還是人工自行發起——AI 原始輸出與人工覆核結果分欄存放。
    只影響 status='pending' 的列；已 excluded 者為 no-op（重複按不重複扣）。

    不自行 commit：交易邊界交由呼叫端。回傳實際確定的筆數。
    """
    ids = [int(pid) for pid in patent_ids]
    if not ids:
        return 0
    with _conn_ctx(conn) as active:
        with active.cursor() as cur:
            # 先快照主題指派（0037），下面會刪掉——不先存就無法「放回原主題」。
            snapshot = _snapshot_assignments(cur, workspace_id, ids)
            cur.execute(
                "UPDATE derived_layer.workspace_excluded_patents AS ex "
                "SET status = 'excluded', excluded_at = now(), "
                "    restored_topic_key = %s::jsonb -> ex.patent_id::text "
                "WHERE ex.workspace_id = %s AND ex.patent_id = ANY(%s) "
                "  AND ex.status = 'pending'",
                (Jsonb({str(k): v for k, v in snapshot.items()}), workspace_id, ids),
            )
            confirmed = cur.rowcount
            # 確定排除＝移出本 workspace 分析，與人工剔除同樣移除指派（不重跑分群）。
            _delete_assignments(cur, workspace_id, ids)
    return confirmed


def keep_patents(
    workspace_id: int,
    patent_ids: Iterable[int],
    *,
    conn: Any | None = None,
) -> int:
    """使用者按「保留」：標記為 status='kept'——留在原主題，但**記得住這個決定**。

    🔴 **2026-08-21 推翻 0036 的「保留＝刪列」**（使用者裁決，CLU-017）。
    原設計反對第三種狀態，理由是「另立狀態會讓每個查排除清單的地方都要多一個
    過濾條件」。⚠ 該理由當時成立，但**需求變了**：刪列＝記不住誰被保留過，
    初階篩選每次重跑都會把同一批專利重新列出來要使用者再裁決一次。

    ⚠ 動工前窮舉全庫 11 個查排除清單的地方，**每一個都明確指定 status**，
    故 0036 擔心的「混進既有清單」不成立。該性質由
    `test_prefilter_decisions.test_every_exclusion_query_filters_status` 守住。

    ⚠ **兩條線對「已保留」的態度刻意不同**（2026-08-21 裁決）：
    - 初階篩選：**跳過** kept——判斷依據是關鍵字比對，重跑答案必定一樣，重問等於騷擾
    - AI 判讀：**可覆蓋** kept——判斷依據是主題結構，重新分群後依據已變，重判有意義

    只改 status='pending' 的列：已確定排除者要放回需走復原流程（另案），
    避免「保留」誤按把已確定的排除決定悄悄撤銷。
    topic_assignments 不動——該專利本就還在原主題（pending 階段從未移除指派）。

    不自行 commit：交易邊界交由呼叫端。回傳實際保留的筆數。
    """
    ids = [int(pid) for pid in patent_ids]
    if not ids:
        return 0
    with _conn_ctx(conn) as active:
        with active.cursor() as cur:
            cur.execute(
                "UPDATE derived_layer.workspace_excluded_patents "
                "SET status = 'kept', excluded_at = now() "
                "WHERE workspace_id = %s AND patent_id = ANY(%s) AND status = 'pending'",
                (workspace_id, ids),
            )
            return cur.rowcount


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
            # 0. 先快照主題指派（0037）：下面第 2 步會刪掉 assignment，不先存就再也
            #    回不到原主題——「可反悔」不能只做到「知道曾被排除」。
            snapshot = _snapshot_assignments(cur, workspace_id, patent_ids)

            # 1. 回寫排除表（可追溯、可反悔）；ON CONFLICT 更新理由與時間。
            #    明確寫 status='excluded'、source='manual'：人工剔除即為確定排除，
            #    若該筆原為 AI 判讀的 pending，這裡直接升級為已確定（人工裁決優先）。
            for pid, reason in rows:
                cur.execute(
                    "INSERT INTO derived_layer.workspace_excluded_patents "
                    "(workspace_id, patent_id, reason, status, source, restored_topic_key) "
                    "VALUES (%s, %s, %s, 'excluded', 'manual', %s) "
                    "ON CONFLICT (workspace_id, patent_id) "
                    "DO UPDATE SET reason = EXCLUDED.reason, excluded_at = now(), "
                    "              status = 'excluded', source = 'manual', "
                    "              restored_topic_key = EXCLUDED.restored_topic_key",
                    (workspace_id, pid, reason, Jsonb(snapshot.get(pid, []))),
                )
                written += 1

            # 2. 移除該筆在本 workspace 的 topic_assignments（⚠ 不碰 artifact／不重算距離）。
            if remove_assignments:
                _delete_assignments(cur, workspace_id, patent_ids)

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
