"""初階篩選的命中落庫與瀏覽清單（PRE-005／PRE-006／CLU-017）。

## 不做第二套剔除機制

命中結果一律寫進**既有**的 `derived_layer.workspace_excluded_patents`，
schema 完全不改，裁決沿用既有的 `confirm_exclusions`／`keep_patents`／
`restore_patents`。本模組只負責「把比對結果變成待裁決項」與「瀏覽清單扣除」。

⚠ 另立一套排除表的代價是「同一個專利在兩張表裡狀態不一致」——而不一致本身
不會報錯，只會讓分群母體與瀏覽清單各說各話。

## 🔴 跳過 `kept` 是本模組與 AI 線的關鍵差異

| | AI 線 | 初階篩選（本模組） |
|---|---|---|
| 判斷依據 | 主題結構（分群結果） | 關鍵字比對 |
| 重跑後依據會變嗎 | 會 | **不會**（PRE-001 明訂重跑可重現） |
| 已保留者重跑時 | **覆蓋回 pending**（重判有意義） | **跳過**（重問等於騷擾） |

使用者 2026-08-21 裁決「分開：初篩記住、AI 線維持重判」。
"""
from __future__ import annotations

from typing import Any

from backend.app.clustering.exclusions import (
    _conn_ctx,
    display_member_patent_ids,
    excluded_patent_ids,
)
from backend.app.prefilter import matching

#: 排除清單裡屬於初階篩選的來源標記（migration 0056 的 CHECK 值域之一）。
SOURCE = "prefilter"


def _reason_text(hits_by_term: dict[str, list[str]]) -> str:
    """把「哪個關鍵字的哪個比對詞命中」寫成可讀的一行（PRE-005 可追溯）。

    ⚠ 記到**比對詞**層級而不只是關鍵字：使用者看到「割草」不知道是被 `mow`
    還是 `lawn mow` 抓到的，而那決定了要不要刪掉某個過度寬鬆的詞。
    """
    parts = [f"{term}（{'／'.join(sorted(matched))}）"
             for term, matched in sorted(hits_by_term.items())]
    return "初階篩選命中：" + "；".join(parts)


def apply_prefilter(workspace_id: int, *, conn: Any | None = None) -> int:
    """把已確認關鍵字的命中寫成待裁決項，回實際新增／更新筆數。

    🔴 **只寫 `status='pending'`**：初階篩選不決定正式資料，使用者裁決才算數
    ——與 `store_ai_verdicts` 只能寫 pending 同一個護欄。

    🔴 **跳過 `kept` 與 `excluded`**（CLU-017）：
    - `kept`——使用者已經說過要留，重跑答案必定一樣，不再問
    - `excluded`——已經封存了，重列會把它打回待裁決

    ⚠ 用 `ON CONFLICT ... WHERE status = 'pending'` 而不是先查再寫：
    先查再寫在併發下會漏（兩個請求同時看到「不存在」），而這個 WHERE
    讓「只更新仍在待裁決的列」由資料庫保證。
    """
    with _conn_ctx(conn) as c:
        preview = matching.preview_counts(workspace_id, conn=c)
        if not preview:
            return 0

        # patent_id → {關鍵字: {命中的比對詞}}
        per_patent: dict[int, dict[str, set[str]]] = {}
        member_ids = display_member_patent_ids(workspace_id, conn=c)
        for item in preview:
            hits = matching.match_patent_ids(
                item["match_terms"], patent_ids=member_ids, conn=c)
            for term, ids in hits.items():
                for pid in ids:
                    per_patent.setdefault(pid, {}).setdefault(
                        item["original_term"], set()).add(term)
        if not per_patent:
            return 0

        rows = [(workspace_id, pid, _reason_text(by_term))
                for pid, by_term in sorted(per_patent.items())]
        with c.cursor() as cur:
            cur.executemany(
                "INSERT INTO derived_layer.workspace_excluded_patents "
                "(workspace_id, patent_id, reason, status, source) "
                f"VALUES (%s, %s, %s, 'pending', '{SOURCE}') "
                "ON CONFLICT (workspace_id, patent_id) DO UPDATE SET "
                "    reason = EXCLUDED.reason, "
                "    excluded_at = now() "
                # 🔴 只更新仍在待裁決的列：kept／excluded 都是使用者已下的決定。
                "WHERE derived_layer.workspace_excluded_patents.status = 'pending'",
                rows,
            )
        return len(rows)


def pending_prefilter_reviews(workspace_id: int, *,
                              conn: Any | None = None) -> list[dict[str, Any]]:
    """初階篩選的待裁決清單：既有欄位 ＋ **命中的那段原文**。

    ## 🔴 為什麼只列 `source='prefilter'`

    AI 線（`ai:irrelevant_filter`）也往同一張表寫 pending，但它的列**沒有
    命中關鍵字**。混進來會是一列「沒有命中原因」的東西，使用者無從理解
    它為什麼在這裡。兩條線的待裁決各自呈現在各自的頁面。

    ⚠ `prefilter/summary` 的 `pending_count` 必須用同一個口徑，否則會出現
    「徽章說 5 筆、點進去只有 3 筆」——使用者會以為系統壞了。

    ## 命中文本即算不落庫

    ⚠ 文本是**推導得出**的（見 0057 註解）：存下來就是專利內文的第二份副本，
    會與來源漂移。這裡每次查詢重算，成本是每個關鍵字一次索引查詢。

    ⚠ **逐關鍵字分開查**而不是把所有詞混在一起：使用者要看到
    「割草（mow）」而不只是「mow」，而詞屬於哪個關鍵字只有分開查才知道。

    回傳每筆多一個 `hits`：`[{keyword, term, field, label, snippet, also}]`。
    """
    from backend.app.clustering.exclusions import pending_reviews
    from backend.app.prefilter import keywords as kw

    with _conn_ctx(conn) as c:
        rows = [r for r in pending_reviews(workspace_id, conn=c)
                if r.get("source") == SOURCE]
        if not rows:
            return []

        pending_ids = [r["patent_id"] for r in rows]
        hits_by_patent: dict[int, list[dict[str, Any]]] = {}
        for row in kw.list_keywords(workspace_id, conn=c):
            if not (row["enabled"] and row["terms_confirmed"]):
                continue
            terms = [t for t in (row["match_terms"] or []) if str(t).strip()]
            if not terms:
                continue
            found = matching.match_snippets(terms, patent_ids=pending_ids, conn=c)
            for pid, hits in found.items():
                for hit in hits:
                    hits_by_patent.setdefault(pid, []).append(
                        {"keyword": row["original_term"], **hit})

    for row in rows:
        row["hits"] = hits_by_patent.get(row["patent_id"], [])
    return rows


def browsable_patent_ids(workspace_id: int, *,
                         conn: Any | None = None) -> list[int]:
    """瀏覽清單用的成員：全部成員**扣除已封存者**（PRE-006）。

    ⚠ **不改 `display_member_patent_ids` 的語意**（D.7）：它的契約是
    「永遠回全部成員」，被排除的標記由呼叫端疊上——這是 0035／0036 既有的
    分工，改它會讓所有顯示路徑一起變。扣除疊在這裡。

    ⚠ 只扣 `excluded`：`pending`（待裁決）與 `kept`（已保留）都還在瀏覽清單裡
    ——前者尚未定案、後者使用者明確要留。
    """
    with _conn_ctx(conn) as c:
        members = display_member_patent_ids(workspace_id, conn=c)
        archived = excluded_patent_ids(workspace_id, conn=c)
    return [pid for pid in members if pid not in archived]
