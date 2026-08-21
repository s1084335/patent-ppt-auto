"""保留期硬刪（PRE-007）。

## 🔴 這是本 change 唯一不可逆的動作

`restore_patents` 只還原「封存」狀態；硬刪是真的把列從 `core_layer.patents`
移除，**11 條 CASCADE 外鍵會連帶清掉**附屬欄、圖、人物、技術／功效向量、
主題指派、檢索詞（2026-08-21 實查確認全部 CASCADE）。刪掉沒有還原路徑。

⇒ 三道保險，缺一不可：
1. **預設 dry-run**——不可逆操作不得靠呼叫端記得傳參數。
2. **只刪候選清單裡的**——呼叫端傳什麼就刪什麼的話，這支函式本身就是
   一把沒有保險的刀。
3. **逐筆獨立交易**——一筆失敗不影響其餘筆，且逐筆回報。

## ⚠ 規格沒防到、本模組補上的一條

`PRE-007` 只說「封存滿一年者成為硬刪對象」，**沒說要檢查它在別的 workspace
還是不是有效成員**。但排除是 **workspace 級**的（0035／0056 明訂）：
同一件專利可以在 A 被剔除、在 B 照常分析。

🔴 照規格字面實作會**把 B 的專利刪掉**，而 B 的使用者從頭到尾不知道——
症狀是分析母體莫名少一件，沒有任何錯誤訊息。故候選判定多一條：
**在所有 workspace 都不是有效成員**才算候選。

## ⚠ 無 FK 保護的引用要主動清（2026-08-21 實查）

| 引用 | FK | 處理 |
|---|---|---|
| 11 張附屬表 | CASCADE | 自動 |
| `derived_layer.report_patent_base` | 它是 **view** | 自動（無實體列） |
| `app_layer.workspaces.patent_ids_json` | 🔴 **無**（jsonb 陣列） | 主動移除 |
| `derived_layer.workspace_excluded_patents` | 🔴 **無** | 主動刪列 |

⚠ 無 FK 的兩處留孤兒**不會報錯**，症狀要等到成員數對不上才浮現。

## 尚未實作：報表版本標記（PRE-007 最後一條）

`SHALL 標記受影響的既有報表版本為「來源已不完整」`——**目前做不到**，
因為資料模型答不出「哪個版本含哪些專利」：

- `app_layer.report_artifacts` 主鍵是 `(version, filename)`，**沒有
  workspace_id、沒有專利名單**
- `workflow_outputs.data_json` 的頂層鍵不含 `patent_ids`／`patent_count`
- `core_layer.patents` **沒有匯入時間欄**，連「這個版本產生時它在不在」
  都推不出來

⇒ 只能做到「整個 workspace 的版本全標」（過度標記）。這需要使用者裁決
與一次 migration，故本切片先不做，並在此明列而非靜默略過。
"""
from __future__ import annotations

from typing import Any

from backend.app.clustering.exclusions import _conn_ctx

#: 保留期（天）。PRE-007 定為一年。
RETENTION_DAYS = 365

#: 單次刪除筆數上限。⚠ 不是效能考量——是**爆炸半徑**：
#: 判定若有錯，上限決定一次錯多少。
DEFAULT_LIMIT = 50


class PurgeError(RuntimeError):
    """刪除對象不合資格，或前置條件不成立。"""


def purge_candidates(*, retention_days: int = RETENTION_DAYS,
                     limit: int | None = None,
                     conn: Any | None = None) -> list[dict[str, Any]]:
    """列出可硬刪的專利。

    資格三條**同時**成立：
    1. 狀態為 `excluded`（🔴 `pending` 是還沒裁決、`kept` 是使用者說要留）
    2. 最後一次封存已滿保留期
    3. **在所有 workspace 都不是有效成員**（見模組 docstring）

    ⚠ 第 3 條用 `patent_ids_json` 判定「是不是成員」，並扣掉在該 workspace
    已 `excluded` 者——「A 剔除、B 還在用」與「兩邊都剔除了」是兩回事。
    """
    sql = """
        WITH archived AS (
            -- 每筆專利在各 workspace 的剔除狀況
            SELECT patent_id,
                   max(excluded_at) FILTER (WHERE status = 'excluded')
                       AS last_excluded_at,
                   count(*) FILTER (WHERE status = 'excluded') AS excluded_in
            FROM derived_layer.workspace_excluded_patents
            GROUP BY patent_id
        ),
        membership AS (
            -- 展開所有 workspace 的成員名單（jsonb 陣列，無 FK）
            SELECT w.workspace_id, (m.value)::bigint AS patent_id
            FROM app_layer.workspaces w
            CROSS JOIN LATERAL jsonb_array_elements(
                COALESCE(w.patent_ids_json, '[]'::jsonb)) AS m(value)
        ),
        active_member AS (
            -- 仍是「有效成員」＝在該 workspace 的名單上且未被剔除
            SELECT m.patent_id, count(*) AS live_in
            FROM membership m
            LEFT JOIN derived_layer.workspace_excluded_patents ex
                   ON ex.workspace_id = m.workspace_id
                  AND ex.patent_id = m.patent_id
                  AND ex.status = 'excluded'
            WHERE ex.patent_id IS NULL
            GROUP BY m.patent_id
        )
        SELECT a.patent_id, a.last_excluded_at, a.excluded_in
        FROM archived a
        LEFT JOIN active_member am ON am.patent_id = a.patent_id
        WHERE a.last_excluded_at IS NOT NULL
          AND a.last_excluded_at < now() - make_interval(days => %(days)s)
          -- 🔴 還有任一 workspace 把它當有效成員就不准刪
          AND COALESCE(am.live_in, 0) = 0
        ORDER BY a.patent_id
    """
    if limit is not None:
        sql += "\n        LIMIT %(limit)s"

    with _conn_ctx(conn) as c:
        with c.cursor() as cur:
            cur.execute(sql, {"days": int(retention_days), "limit": limit})
            return [{"patent_id": int(r[0]),
                     "last_excluded_at": r[1],
                     "excluded_in_workspaces": int(r[2])}
                    for r in cur.fetchall()]


def _delete_one(conn: Any, patent_id: int) -> None:
    """刪一筆專利與它**沒有 FK 保護**的引用。

    ⚠ 順序有意義：先清無 FK 的引用，最後刪本體。反過來的話，
    刪本體成功但清引用失敗時會留下孤兒，而孤兒不會報錯。
    """
    with conn.cursor() as cur:
        # ① 剔除名單（無 FK）
        cur.execute(
            "DELETE FROM derived_layer.workspace_excluded_patents "
            "WHERE patent_id = %s", (patent_id,))
        # ② 成員名單的 jsonb 陣列（無 FK）——逐個 workspace 濾掉該 id
        cur.execute(
            """
            UPDATE app_layer.workspaces w
            SET patent_ids_json = (
                SELECT COALESCE(jsonb_agg(v), '[]'::jsonb)
                FROM jsonb_array_elements(w.patent_ids_json) AS t(v)
                WHERE (v)::bigint <> %(pid)s
            )
            WHERE w.patent_ids_json @> to_jsonb(%(pid)s::bigint)
            """,
            {"pid": patent_id})
        # ③ 本體——11 條 CASCADE 外鍵連帶清掉附屬資料
        cur.execute("DELETE FROM core_layer.patents WHERE id = %s", (patent_id,))


def purge_patents(patent_ids: list[int], *,
                  dry_run: bool = True,
                  retention_days: int = RETENTION_DAYS,
                  limit: int | None = DEFAULT_LIMIT,
                  conn: Any | None = None) -> dict[str, Any]:
    """硬刪指定專利。**預設 dry-run**。

    🔴 `dry_run` 預設為 `True`：不可逆操作不得靠呼叫端記得傳參數。
    真的要刪必須明寫 `dry_run=False`。

    🔴 只刪出現在 `purge_candidates` 裡的——傳入不合資格者直接 `PurgeError`，
    不是靜默略過：呼叫端以為刪了、實際沒刪，比報錯更難查。

    ⚠ 逐筆獨立交易：一筆失敗不影響其餘筆（PRE-007「失敗隔離」），
    並逐筆回報結果。

    回傳 `{planned, deleted, failed}`。
    """
    wanted = [int(p) for p in (patent_ids or [])]
    if not wanted:
        return {"planned": [], "deleted": 0, "failed": []}

    with _conn_ctx(conn) as c:
        eligible = {row["patent_id"]
                    for row in purge_candidates(retention_days=retention_days,
                                                conn=c)}
        bad = [p for p in wanted if p not in eligible]
        if bad:
            raise PurgeError(
                f"下列專利不在可刪除清單內（未滿保留期、未確定剔除、"
                f"或仍是其他 workspace 的有效成員）：{sorted(bad)}")

        planned = wanted if limit is None else wanted[:limit]
        if dry_run:
            return {"planned": planned, "deleted": 0, "failed": []}

        deleted = 0
        failed: list[dict[str, Any]] = []
        for pid in planned:
            try:
                # ⚠ 逐筆獨立交易：用 savepoint 讓失敗只回滾這一筆。
                with c.transaction():
                    _delete_one(c, pid)
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                failed.append({"patent_id": pid, "error": str(exc)})
        return {"planned": planned, "deleted": deleted, "failed": failed}
