"""初階篩選的範圍相關性建議（PRE-008）。

## 🔴 這個模組的存在理由是「建議不得變成決定」

AI 只能建議，使用者才有決定權。落到程式上有三條護欄，全部在寫入端：

1. **不碰 `status`**——`UPDATE` 的 `SET` 子句裡沒有 `status`。
   ⚠ 這是護欄不是 code review：只要有人往 SET 裡加一個 status，測試就紅。
2. **只寫 `status='pending'` 的列**——已裁決保留或剔除的，建議到得再晚也不動。
   使用者 2026-08-21：「填上後不要影響使用者」。
3. **值域由 DB CHECK 守**（0057），不靠呼叫端自律。

## 「還沒跑」與「跑了但沒依據」必須分得開

| `scope_verdict` | 意思 | 畫面 |
|---|---|---|
| `NULL` | 尚未產生建議 | ⏳ 尚無建議 |
| `no_basis` | 三個判讀欄位皆空 | ⚪ 無判讀依據 |

🔴 兩者都用空白表示的話，使用者會把後者當成前者而一直等；而空白本身
會被讀成「沒問題」。⚠ 缺席型偏差：看不到的東西不會引起懷疑。
"""
from __future__ import annotations

from typing import Any, Iterable

from backend.app.clustering.exclusions import _conn_ctx

#: 允許的建議值。與 0057 的 CHECK 對應。
#: ⚠ 這裡擋是為了給出**看得懂的錯誤**；真正的守門在 DB，
#: 不然繞過本函式直接寫 SQL 就破功了。
VALID_VERDICTS = ("keep", "exclude", "no_basis")


def pending_targets(workspace_id: int, *,
                    conn: Any | None = None) -> list[int]:
    """要送去判讀的專利＝**待裁決且尚無建議**者。

    ⚠ 已有建議者不重送：重跑要花錢，而且同一批輸入的答案不保證相同——
    重送只會讓使用者看到建議無故變動，那比沒有建議更糟。

    ⚠ 只取 `pending`：已保留或已剔除者使用者已經決定過了，再給建議沒有用途。
    """
    with _conn_ctx(conn) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT patent_id FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id = %s AND status = 'pending' "
                "  AND scope_verdict IS NULL ORDER BY patent_id",
                (workspace_id,))
            return [int(r[0]) for r in cur.fetchall()]


def store_suggestions(workspace_id: int,
                      items: Iterable[dict[str, Any]], *,
                      conn: Any | None = None) -> int:
    """寫入建議，回傳實際更新筆數。

    每筆需含 `patent_id` 與 `verdict`，可含 `reason`。

    ⚠ 用 `UPDATE` 不用 `INSERT ... ON CONFLICT`：建議只對**已經在待裁決清單裡**
    的專利有意義。用 upsert 的話，AI 回了一個不在清單裡的 patent_id 就會憑空
    插一列——那是把 AI 的輸出直接變成待辦，正好違反「AI 不決定正式資料」。

    Raises:
        ValueError: verdict 不在 `VALID_VERDICTS` 內。
    """
    rows = []
    for item in items:
        pid = item.get("patent_id")
        if pid is None:
            continue
        verdict = str(item.get("verdict") or "").strip()
        if verdict not in VALID_VERDICTS:
            raise ValueError(
                f"建議值 {verdict!r} 不在允許範圍 {VALID_VERDICTS}")
        reason = str(item.get("reason") or "").strip() or None
        rows.append((verdict, reason, workspace_id, int(pid)))
    if not rows:
        return 0

    with _conn_ctx(conn) as c:
        with c.cursor() as cur:
            cur.executemany(
                "UPDATE derived_layer.workspace_excluded_patents SET "
                # 🔴 SET 裡沒有 status——建議不得改變裁決狀態（PRE-008）。
                "    scope_verdict = %s, "
                "    scope_reason = %s, "
                "    scope_judged_at = now() "
                "WHERE workspace_id = %s AND patent_id = %s "
                # ⚠ 只動待裁決者：已保留／已剔除是使用者的決定，
                #   建議到得再晚也不能把它翻掉。
                "  AND status = 'pending'",
                rows)
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(rows)
