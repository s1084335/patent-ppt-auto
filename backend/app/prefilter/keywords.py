"""負面關鍵字的治理（PRE-001）。

## 三條不變量

1. **關鍵字以 workspace 為單位**——每個查詢都帶 `workspace_id`，沒有跨庫讀法。
2. **停用者保留紀錄但不參與比對**——`active_match_terms` 過濾 `enabled`。
3. **未確認的比對詞不得生效**——`active_match_terms` 同時過濾 `terms_confirmed`。

⚠ 第 3 條的落點有兩層：schema 預設 `false`（migration 0055）＋本模組的查詢條件。
兩層都要，因為應用層漏一條路徑就破功，而 schema 預設擋不住「已經寫成 true」。

## 全庫 workspace 的判定不在這裡

委派 `clustering.exclusions.is_global_workspace`——那是既有的唯一定義處。
⚠ 自己查 `workspaces.is_global` 會變成第二份定義；兩份會各自演進而不報錯。
"""
from __future__ import annotations

from typing import Any

from backend.app.clustering.exclusions import _conn_ctx, is_global_workspace

#: 表名寫一次，下面全部引用——避免字面散在各查詢裡。
TABLE = "derived_layer.workspace_negative_keywords"

#: 回傳給呼叫端的欄位順序（`SELECT *` 會讓欄序隨 migration 漂移）。
_COLUMNS = (
    "keyword_id", "workspace_id", "original_term", "match_terms",
    "terms_confirmed", "enabled", "created_at", "updated_at",
)
_SELECT = ", ".join(f'"{c}"' for c in _COLUMNS)


class PrefilterScopeError(ValueError):
    """關鍵字的 workspace 範圍不合法（例如全庫）。"""


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(zip(_COLUMNS, row))


def _require_scoped_workspace(workspace_id: int, *, conn: Any) -> None:
    """全庫 workspace 不得建立關鍵字（沿用 CLU-007 既有限制）。

    ⚠ 理由不是技術限制而是語意：初階篩選的目的是「把不該進入這次分析的專利篩掉」，
    而全庫視角沒有「這次分析」，篩掉等於對全庫做破壞性標記。
    """
    if is_global_workspace(workspace_id, conn=conn):
        raise PrefilterScopeError(
            f"workspace {workspace_id} 是全庫視角，不得建立初階篩選關鍵字")


def list_keywords(workspace_id: int, *, conn: Any | None = None) -> list[dict[str, Any]]:
    """列出該 workspace 的全部關鍵字（含停用者）。

    ⚠ 含停用者是刻意的：治理介面要看得到停用紀錄才能重新啟用。
    「哪些真的會比對」請用 `active_match_terms`。
    """
    with _conn_ctx(conn) as c:
        with c.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT} FROM {TABLE} WHERE workspace_id = %s "
                f"ORDER BY created_at, keyword_id",
                (int(workspace_id),),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]


def create_keyword(workspace_id: int, original_term: str, *,
                   conn: Any | None = None) -> dict[str, Any]:
    """建立一筆負面關鍵字。

    ⚠ `match_terms` 一律留空、`terms_confirmed` 一律 `false`——比對詞由切片 B 的
    AI 轉換或使用者自行填入，且都要經確認才生效。
    """
    term = (original_term or "").strip()
    if not term:
        raise ValueError("original_term 不得為空")
    with _conn_ctx(conn) as c:
        _require_scoped_workspace(workspace_id, conn=c)
        with c.cursor() as cur:
            cur.execute(
                f"INSERT INTO {TABLE} (workspace_id, original_term) "
                f"VALUES (%s, %s) "
                f"ON CONFLICT (workspace_id, original_term) DO UPDATE "
                f"SET updated_at = now() "
                f"RETURNING {_SELECT}",
                (int(workspace_id), term),
            )
            return _row_to_dict(cur.fetchone())


def update_keyword(keyword_id: int, *,
                   match_terms: list[str] | None = None,
                   terms_confirmed: bool | None = None,
                   enabled: bool | None = None,
                   conn: Any | None = None) -> dict[str, Any]:
    """更新比對詞、確認狀態或啟用旗標（只改有傳的欄）。

    ⚠ 三個參數都預設 `None` 而非各自的預設值：`None` 代表「不動」，
    才分得開「沒傳」與「傳了 False」。
    """
    sets: list[str] = []
    params: list[Any] = []
    if match_terms is not None:
        sets.append('"match_terms" = %s')
        params.append([str(t).strip() for t in match_terms if str(t).strip()])
    if terms_confirmed is not None:
        sets.append('"terms_confirmed" = %s')
        params.append(bool(terms_confirmed))
    if enabled is not None:
        sets.append('"enabled" = %s')
        params.append(bool(enabled))
    if not sets:
        raise ValueError("至少要指定一個要更新的欄位")
    sets.append('"updated_at" = now()')
    params.append(int(keyword_id))

    with _conn_ctx(conn) as c:
        with c.cursor() as cur:
            cur.execute(
                f"UPDATE {TABLE} SET {', '.join(sets)} "
                f"WHERE keyword_id = %s RETURNING {_SELECT}",
                tuple(params),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"keyword {keyword_id} 不存在")
            return _row_to_dict(row)


def delete_keyword(keyword_id: int, *, conn: Any | None = None) -> bool:
    """刪除一筆關鍵字。停用請用 `update_keyword(enabled=False)`。"""
    with _conn_ctx(conn) as c:
        with c.cursor() as cur:
            cur.execute(f"DELETE FROM {TABLE} WHERE keyword_id = %s",
                        (int(keyword_id),))
            return cur.rowcount > 0


def active_match_terms(workspace_id: int, *,
                       conn: Any | None = None) -> list[str]:
    """該 workspace 目前**真的會拿去比對**的英文詞（去重、排序穩定）。

    🔴 兩個過濾條件缺一不可：
    - `enabled`——停用者不參與比對（PRE-001）
    - `terms_confirmed`——未確認的比對詞不得生效（PRE-002）

    ⚠ 排序固定（`ORDER BY term`）：PRE-001 要求「重跑可重現」，
    而陣列展開的順序不保證，不排序會讓兩次結果看起來不同。
    """
    with _conn_ctx(conn) as c:
        with c.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT term FROM {TABLE}, "
                f"LATERAL unnest(match_terms) AS term "
                f"WHERE workspace_id = %s AND enabled AND terms_confirmed "
                f"AND term <> '' ORDER BY term",
                (int(workspace_id),),
            )
            return [r[0] for r in cur.fetchall()]
