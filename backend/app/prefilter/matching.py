"""初階篩選的確定性比對（PRE-003／PRE-004）。

## 🔴 為什麼是「前綴詞界」而不是子字串或完整詞界

三種比對方式，**子字串與完整詞界各自都會出錯，而且方向相反**（2026-08-21 實測，
割草機 workspace 母體）：

| 方式 | `ion` | `mow` | 問題 |
|---|---|---|---|
| 子字串 `ILIKE '%ion%'` | 265 | 187 | 🔴 `combustion`／`composition` 全中——大量誤剔除 |
| 完整詞界 `~* '\\mion\\M'` | 0 | 11 | 🔴 `mower`／`mowing` 不中——漏掉該剔除的 |
| **前綴詞界 `~* '\\m詞'`** | **0** | **177** | ✅ 兩端都對 |

⚠ 不能用 `LIKE 'term%'`：比對要落在**單字的開頭**，不是**欄位的開頭**。
`LIKE 'mow%'` 只在 title 剛好以 mow 起頭時命中，`"Lawn mower blade"` 會漏。
`\\m` 是 PostgreSQL 的「詞首」錨點，才是要的語意。

## 比對過程不涉及 AI

AI 只在切片 B 把中文轉成英文詞，且產出要經使用者確認。到了本模組，
輸入已經是一組確定的英文詞，比對是純 SQL 運算——**同樣的關鍵字與資料，
兩次結果必定相同**（PRE-001「重跑可重現」）。
"""
from __future__ import annotations

import re
from typing import Any

from backend.app.clustering.exclusions import _conn_ctx, display_member_patent_ids

#: 三個比對欄位（PRE-003）：(對外鍵名, 資料表欄名, 顯示標籤)。
#:
#: ⚠ 獨立項的欄名是中文帶國別後綴、**不含 "claim" 字樣**——用關鍵字猜欄名會漏掉它
#: （切片 0 量比對數字時實際踩過，少量一欄導致數字對不上規格）。
#:
#: ⚠ 三者綁成一個序列而不是三份平行清單：欄名、鍵名、標籤是同一份知識的三個面，
#: 拆成三份就會各自演進，而**不一致本身不會報錯**——症狀會出現在別的地方。
#: 顯示優先序即本序列順序（標題 > 摘要 > 獨立項）。
MATCH_FIELDS = (
    ("title", "title", "標題"),
    ("abstract", "abstract", "摘要"),
    ("claims", "獨立項[KR,JP,US,CN,EP,IN]", "獨立項"),
)

MATCH_COLUMNS = tuple(col for _, col, _ in MATCH_FIELDS)

SOURCE_TABLE = "derived_layer.report_patent_base"

#: 命中詞前後各保留的字數。實測正式庫抽出來 ≤86 字，畫面一行半。
SNIPPET_CONTEXT = 40

#: 命中文本硬上限。⚠ 這是**護欄不是格式**：比對詞本身可能很長，
#: 前後各 40 字不足以保證總長有界。長獨立項單篇逾萬字，
#: 沒有上限就會把整份請求項灌進畫面與 AI prompt。
SNIPPET_MAX = 200

#: 每個比對詞最多揭露幾種詞形。
#: ⚠ 沒有上限的話，一個很寬的詞幹會回傳上百個詞形，把確認畫面灌爆
#: ——而那正好是使用者最需要看清楚的一格。
MAX_FORMS = 8


def normalize_term(term: str) -> str:
    """把使用者／AI 給的比對詞轉成可直接放進 `~*` 的正規式片段。

    🔴 **跳脫只能在這裡做**（C.5）：使用者輸入 `c++`、`(a)`、`a|b` 是常態，
    不跳脫的後果是 `psycopg.errors.InvalidRegularExpression`——而且是
    **執行篩選時才炸**，不是輸入時，錯誤訊息也看不出是哪個詞造成的。

    ⚠ `\\m` 是詞首錨點，必須加在跳脫**之後**——先跳脫會把反斜線本身也跳掉。
    """
    text = (term or "").strip()
    if not text:
        return ""
    return r"\m" + re.escape(text)


def _quoted_columns() -> list[str]:
    return [f'"{c}"' for c in MATCH_COLUMNS]


def _match_clause() -> str:
    """任一比對欄位命中即算命中。"""
    return " OR ".join(f"{c} ~* %s" for c in _quoted_columns())


def match_patent_ids(terms: list[str], *,
                     patent_ids: list[int] | None = None,
                     conn: Any | None = None) -> dict[str, list[int]]:
    """逐比對詞回傳命中的 patent_id（升冪，穩定）。

    ⚠ **逐詞分開查而不是一次 OR 起來**：PRE-005 要求命中原因可追溯——
    使用者要知道「這件是被哪個詞抓到的」。一次查完只知道「有中」。

    ⚠ 空清單回空 dict，**不組 SQL**：`WHERE` 後面接空條件會變成命中全部，
    那是最糟的失敗形式（看起來像「篩選很有效」）。
    """
    if not terms:
        return {}

    scope = ""
    scope_params: tuple = ()
    if patent_ids is not None:
        # ⚠ 空清單也要帶條件——「這個 workspace 沒有成員」與「全庫」不是同一件事。
        scope = " AND patent_id = ANY(%s)"
        scope_params = ([int(i) for i in patent_ids],)

    out: dict[str, list[int]] = {}
    with _conn_ctx(conn) as c:
        with c.cursor() as cur:
            for term in terms:
                pattern = normalize_term(term)
                if not pattern:
                    out[term] = []
                    continue
                cur.execute(
                    f"SELECT patent_id FROM {SOURCE_TABLE} "
                    f"WHERE ({_match_clause()}){scope} ORDER BY patent_id",
                    tuple([pattern] * len(MATCH_COLUMNS)) + scope_params,
                )
                out[term] = [int(r[0]) for r in cur.fetchall()]
    return out


def blank_field_patent_ids(*, patent_ids: list[int] | None = None,
                           conn: Any | None = None) -> list[int]:
    """三個比對欄位皆空（NULL 或全空白）的 patent_id。

    ⚠ 這不是統計裝飾：這些專利**永遠不會被任何關鍵字命中**。
    使用者要知道「有幾件根本沒東西可比」，否則會誤以為它們都通過了篩選
    ——那是缺席型偏差，看不到的東西不會引起懷疑。
    """
    blank = " AND ".join(
        f"coalesce(btrim({c}), '') = ''" for c in _quoted_columns())
    scope = ""
    params: tuple = ()
    if patent_ids is not None:
        scope = " AND patent_id = ANY(%s)"
        params = ([int(i) for i in patent_ids],)

    with _conn_ctx(conn) as c:
        with c.cursor() as cur:
            cur.execute(
                f"SELECT patent_id FROM {SOURCE_TABLE} "
                f"WHERE ({blank}){scope} ORDER BY patent_id", params)
            return [int(r[0]) for r in cur.fetchall()]






def _scope(patent_ids: list[int] | None) -> tuple[str, tuple]:
    """`patent_ids` → SQL 片段與參數。None＝不限縮。

    ⚠ 空清單也要帶條件——「這個 workspace 沒有成員」與「全庫」不是同一件事。
    """
    if patent_ids is None:
        return "", ()
    return " AND patent_id = ANY(%s)", ([int(i) for i in patent_ids],)


def match_snippets(terms: list[str], *,
                   patent_ids: list[int] | None = None,
                   conn: Any | None = None) -> dict[int, list[dict[str, Any]]]:
    """逐專利、逐比對詞回傳**命中的那段原文**（2026-08-21 使用者裁決）。

    ## 🔴 為什麼要文本，不是只給「被哪個詞命中」

    只寫「被 blower 命中」，使用者無從判斷那是不是誤剔。正式庫 #591
    `VEHICLE WITH UNDER-BODY BLOWER` 是**帶吹風平台的割草載具**，不是吹葉機——
    看得到那句話才分得出來。判斷成本從「自己去查那件專利」降到「讀一行字」。

    ## 一個詞只回一段

    ⚠ 三個欄位各回一段的話，兩個關鍵字就六段——待裁決清單會變成沒人看的牆。
    取優先序最前的那欄（標題 > 摘要 > 獨立項），其餘欄位以 `also` 標示。

    🔴 `also` 不可省略：只顯示標題那段、不說「摘要與獨立項也命中」，
    使用者會低估命中強度。看不到的東西不會引起懷疑。

    ## 跳脫沿用 `normalize_term`

    ⚠ 本函式**不得自己再拼一次正規式**——那會變成第二個跳脫定義處，
    兩邊各自演進後 `c++` 之類的輸入會在其中一條路上炸。

    回傳：`{patent_id: [{term, field, label, snippet, also}]}`；未命中者不出現。
    """
    clean = [t for t in (terms or []) if str(t or "").strip()]
    if not clean:
        return {}

    scope_sql, scope_params = _scope(patent_ids)
    quoted = _quoted_columns()
    # 每欄抽一段：命中詞往前後各取 SNIPPET_CONTEXT 字。
    # `\w*` 讓詞形變化（mower／mowing）整個字被涵蓋，不會斷在字中間。
    sel = ", ".join(
        f"(regexp_match({c}, '.{{0,{SNIPPET_CONTEXT}}}' || %s || "
        f"'\\w*.{{0,{SNIPPET_CONTEXT}}}', 'i'))[1]"
        for c in quoted)

    out: dict[int, list[dict[str, Any]]] = {}
    with _conn_ctx(conn) as c:
        with c.cursor() as cur:
            for term in clean:
                pattern = normalize_term(term)
                if not pattern:
                    continue
                cur.execute(
                    f"SELECT patent_id, {sel} FROM {SOURCE_TABLE} "
                    f"WHERE ({_match_clause()}){scope_sql} ORDER BY patent_id",
                    tuple([pattern] * len(MATCH_FIELDS))      # SELECT 的抽取
                    + tuple([pattern] * len(MATCH_FIELDS))    # WHERE 的判定
                    + scope_params,
                )
                for row in cur.fetchall():
                    pid = int(row[0])
                    # regexp_match 沒中回 NULL——哪幾欄有值就是哪幾欄命中，
                    # 不需要另外再判一次。
                    matched = [(key, label, row[i + 1])
                               for i, (key, _, label) in enumerate(MATCH_FIELDS)
                               if row[i + 1]]
                    if not matched:
                        continue
                    key, label, raw = matched[0]   # MATCH_FIELDS 序即優先序
                    snippet = " ".join(str(raw).split())[:SNIPPET_MAX]
                    out.setdefault(pid, []).append({
                        "term": term,
                        "field": key,
                        "label": label,
                        "snippet": snippet,
                        "also": [k for k, _, _ in matched[1:]],
                    })
    return out


def term_hit_summary(terms: list[str], *,
                     patent_ids: list[int] | None = None,
                     conn: Any | None = None) -> list[dict[str, Any]]:
    """逐比對詞回傳「命中件數 ＋ 實際命中的詞形」，供**確認畫面**判斷詞夠不夠準。

    ## 🔴 為什麼需要這支

    AI 依 prompt 給的是**詞幹**（`machin`、`mechaniz`），因為比對採前綴詞界。
    詞幹的好處實測過：正式庫 `mow` 用完整詞界只中 **11** 件、用前綴中 **177**
    件——專利文字幾乎不會單獨出現 `mow`，都是 `mower`／`mowing deck`。

    ⚠ 但同一個機制的另一面是：`engine` 會命中 `engineering`。
    這不是缺陷，是放寬涵蓋率的固有代價——**但使用者要能在按下去之前看到它**，
    否則就是拿誤剔換涵蓋率而當事人不知情。

    畫面上只給四個看起來像拼錯的字，使用者沒有依據判斷哪個太寬，
    就只剩兩條路：全部照按（可能誤剔）或全部不敢按（功能等於沒有）。

    ## ⚠ 與 `preview_counts` 的分工

    `preview_counts` 只算**已確認**的關鍵字（PRE-002：未確認者不得產生命中）。
    確認畫面要看的正好是**還沒確認**的那些，故不能共用。

    🔴 但件數一律走 `match_patent_ids`——**計數只能有一個定義處**。
    另寫一份計數 SQL 的話兩份會各自演進，而不一致本身不會報錯，
    只會讓預覽數字與實際套用結果對不上。本函式只多做「詞形」這件事。
    """
    clean = [t for t in (terms or []) if str(t or "").strip()]
    if not clean:
        return []

    scope_sql, scope_params = _scope(patent_ids)
    quoted = _quoted_columns()
    # 三欄接成一段再抓詞形：用空白分隔，不會在欄位交界處拼出假的字。
    # ⚠ 這裡**只用來取詞形**，件數仍以 match_patent_ids 為準。
    joined = "concat_ws(' ', " + ", ".join(quoted) + ")"

    with _conn_ctx(conn) as c:
        counts = match_patent_ids(clean, patent_ids=patent_ids, conn=c)
        out: list[dict[str, Any]] = []
        with c.cursor() as cur:
            for term in clean:
                pattern = normalize_term(term)
                forms: list[str] = []
                if pattern:
                    cur.execute(
                        "SELECT DISTINCT lower(m[1]) AS form FROM ("
                        f"  SELECT regexp_matches({joined}, %s, 'gi') AS m "
                        f"  FROM {SOURCE_TABLE} WHERE true{scope_sql}"
                        ") s ORDER BY form LIMIT %s",
                        (f"({pattern}\\w*)",) + scope_params + (MAX_FORMS,))
                    forms = [r[0] for r in cur.fetchall()]
                out.append({
                    "term": term,
                    "patent_count": len(counts.get(term, [])),
                    "forms": forms,
                })
        return out


def preview_counts(workspace_id: int, *,
                   conn: Any | None = None) -> list[dict[str, Any]]:
    """逐關鍵字的命中件數預覽（PRE-004）。

    🔴 **零命中要回 0，不得省略該列**：省略的後果是使用者以為那個關鍵字
    還沒算完、或以為自己沒輸入過。「算過了，結果是 0」與「沒算」必須分得開。

    ⚠ 只算**已確認且啟用**的關鍵字——未確認者本來就不該產生任何命中（PRE-002）。
    """
    from backend.app.prefilter import keywords as kw

    with _conn_ctx(conn) as c:
        rows = [r for r in kw.list_keywords(workspace_id, conn=c)
                if r["enabled"] and r["terms_confirmed"]]
        if not rows:
            return []
        # 🔴 成員清單走既有唯一來源 `display_member_patent_ids`（契約＝**永遠回全部成員**），
        #    不自己讀 `patent_ids_json`。
        #    ⚠ 理由有兩層：
        #    ① 同一份知識只能有一個定義處——成員判定日後若改（例如組合 workspace
        #      的展開規則），自己讀那份就會靜默落後。
        #    ② 初篩之後還會有其他型態的 workspace（組合、案件比對、全庫），
        #      該函式已經一致處理它們；寫死讀 json 等於把機制綁在單一型態上。
        #    ⚠ **不能用 `analysis_member_patent_ids`**：那條會扣掉已剔除者，
        #      而剔除正是本功能要產生的——扣掉等於「已剔除的永遠不會再被檢視」。
        member_ids = display_member_patent_ids(workspace_id, conn=c)

        out: list[dict[str, Any]] = []
        for row in rows:
            terms = [t for t in (row["match_terms"] or []) if str(t).strip()]
            hits = match_patent_ids(terms, patent_ids=member_ids, conn=c)
            merged = sorted({pid for ids in hits.values() for pid in ids})
            out.append({
                "keyword_id": row["keyword_id"],
                "original_term": row["original_term"],
                "match_terms": terms,
                "patent_count": len(merged),
                "patent_ids": merged,
            })
        return out
