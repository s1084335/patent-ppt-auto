"""`govern_company_names` 必須改讀寫四欄（2026-07-28 使用者定案）。

## 使用者原話

「申請人代碼是穎文兩碼加數字，喬山健康科技是公司中文名稱，TW-CHIHUA 正規化名稱，
你確認一下寫入機制有沒有對不到，資料庫欄位要改成目標的，依序就是
申請人代碼、公司中文名稱、正規化名稱、別稱」

## 問題

0040 拆四欄後，兩條主寫入路徑（`apply_confirmed_display_names`、AI 草稿 runner）
都已改寫新欄，但 `govern_company_names` **讀與寫都還鎖在舊的 `公司名稱`**：

| 行 | 動作 | 欄位 |
|---|---|---|
| 198 | `SELECT` 既有組 | `"公司名稱"` |
| 234 | `INSERT` 新變體 | `"公司名稱"` |

## 為何是斷鏈而不只是「舊欄沒清乾淨」

拆欄後寫入的列 `公司名稱` 是 **NULL**（0040 已放寬 NOT NULL）。於是：

1. `names_by_code[code]` 收到 `{None}` —— 長度 1，通過 `conflicting_code` 檢查
2. `canonical_name = None` → 新變體以 `公司名稱=NULL` 寫入，**三個名稱欄全空**
3. 該列只剩代碼與別稱，顯示端 COALESCE 全落空

比「寫錯欄」更糟的是它**不會報錯**——又一次靜默失敗。

⚠ 若某代碼是拆欄前後混寫（舊列有值、新列 NULL），`names_by_code` 會收到
`{"喬山健康科技", None}`，長度 2 → 全批判 `conflicting_code` 丟人工清單。

## 定案的欄位語意

| 欄 | 放什麼 | 喬山的例子 |
|---|---|---|
| `申請人代碼` | WIPS 查來的真代碼（英文兩碼＋數字） | 使用者去 WIPS 查 |
| `公司中文名稱` | 中文正式名 | 喬山健康科技 |
| `正規化名稱` | **英文正式名** | Chi Hua Fitness Co., Ltd. |
| `別稱` | 各種雜亂寫法，一列一個 | CHI HUA FITNESS CO LTD 等 |

⚠ `TW-CHIHUA` 這種自編字串不屬於任何一欄——它既不是 WIPS 代碼也不是英文正式名。
既有 4 列由使用者從前端重走一次歸位（本測試不涉及資料搬移）。
"""
from __future__ import annotations

import unittest


class GovernReadsSplitColumnsTests(unittest.TestCase):
    """讀取端：既有組的名稱要從新兩欄取，不能只看舊欄。"""

    def test_select_reads_split_columns(self):
        """SELECT 必須帶出 `公司中文名稱` 與 `正規化名稱`。

        ⚠ 不搜「有沒有出現這兩個欄名」——本檔 docstring 自己就寫滿了，
        會被自己餵飽。改為抓真正的 SELECT 語句再驗。
        """
        sql = _extract_sql_containing("SELECT", "company_aliases")
        self.assertNotIn("公司名稱", sql.replace("公司中文名稱", ""),
                         "0041 已移除舊欄，SELECT 不得再讀它")
        for col in ("公司中文名稱", "正規化名稱"):
            with self.subTest(col=col):
                self.assertIn(col, sql, f"SELECT 沒帶 {col}，拆欄後讀不到新資料")

    def test_insert_writes_normalized_not_legacy(self):
        """INSERT 的英文正式名要寫 `正規化名稱`，不是舊 `公司名稱`。"""
        sql = _extract_sql_containing("INSERT INTO", "company_aliases")
        self.assertIn("正規化名稱", sql, "INSERT 沒寫 正規化名稱")

    def test_needs_zh_detection_reads_zh_column(self):
        """待中文化偵測的判準要改成「`公司中文名稱` 為空」。

        原判準是「`公司名稱` 不含 CJK」——拆欄後 `公司名稱` 是 NULL，
        PostgreSQL 的 `NULL !~ '...'` 結果是 NULL 不是 TRUE，
        **該代碼永遠不會浮現待中文化**，等於這條偵測整個失效。
        """
        sql = _extract_sql_containing("SELECT DISTINCT", "company_aliases")
        self.assertIn("公司中文名稱", sql,
                      "待中文化偵測仍看舊欄，拆欄後寫入的組永遠不會浮現")


class GovernBehaviourTests(unittest.TestCase):
    """行為層：拆欄後寫入的組，新變體要能正確歸戶。"""

    def test_new_variant_carries_both_names(self):
        """既有組只有新兩欄有值時，新變體要沿用同一組中英文名。

        這是真正的回歸案例——修前 `canonical_name` 會是 None，
        新變體三個名稱欄全空且不報錯。
        """
        rows = _run_govern(
            existing=[("TW1234567", "喬山健康科技", "Chi Hua Fitness Co., Ltd.",
                       "CHI HUA FITNESS CO LTD")],
            pairs=[("TW1234567", "Chi Hua Fitness Co.,Ltd")],
        )
        self.assertEqual(len(rows), 1, f"應寫入 1 列，實得 {len(rows)}")
        written = rows[0]
        self.assertEqual(written["公司中文名稱"], "喬山健康科技")
        self.assertEqual(written["正規化名稱"], "Chi Hua Fitness Co., Ltd.")
        self.assertEqual(written["別稱"], "Chi Hua Fitness Co.,Ltd")

    def test_group_without_any_name_goes_to_manual(self):
        """兩個名稱欄都空的組＝沒有可沿用的 canonical，丟人工不自行編名。

        ⚠ 這條原本是 `test_legacy_only_group_still_works`（驗舊欄 fallback），
        0041 移除 `公司名稱` 後前提消失，改寫為驗真正的邊界：
        `next(iter(names))` 遇空集合會 **StopIteration 中斷整批**，
        不是只跳過這一筆——必須在取值前擋掉。
        """
        rows = _run_govern(
            existing=[("TW9999999", None, None, "ACME CORPORATION")],
            pairs=[("TW9999999", "Acme Corp.")],
        )
        self.assertEqual(rows, [], "沒有 canonical 時不得寫入殘列")

    def test_same_name_group_is_not_conflict(self):
        """同一代碼多列、名稱組一致時，不得誤判 conflicting_code。"""
        rows = _run_govern(
            existing=[
                ("TW9999999", None, "ACME CORP", "ACME CORPORATION"),
                ("TW9999999", None, "ACME CORP", "ACME CO LTD"),
            ],
            pairs=[("TW9999999", "Acme Corp.")],
        )
        self.assertEqual(len(rows), 1, "同一組名稱的多列不該判成衝突")
        self.assertEqual(rows[0]["正規化名稱"], "ACME CORP")

    def test_different_names_same_code_is_conflict(self):
        """同一代碼配到兩組不同名稱＝真衝突，丟人工不自行挑一個。"""
        rows = _run_govern(
            existing=[
                ("TW9999999", None, "ACME CORP", "ACME CORPORATION"),
                ("TW9999999", None, "OTHER CORP", "OTHER CO"),
            ],
            pairs=[("TW9999999", "Acme Corp.")],
        )
        self.assertEqual(rows, [], "名稱不一致時不得自行挑一個寫入")


# ── 輔助 ──────────────────────────────────────────────────────────────

def _extract_sql_containing(stmt: str, table: str) -> str:
    """從 `govern_company_names` 原始碼抓含指定關鍵字的 SQL 字面。

    只取函式本體（不含 docstring 與註解），避免說明文字造成假通過。
    """
    import ast
    import inspect

    from backend.app.derived import company_alias_importer as m

    tree = ast.parse(inspect.getsource(m.govern_company_names))
    hits = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and stmt in n.value and table in n.value
    ]
    assert hits, f"找不到含 {stmt} 與 {table} 的 SQL"
    return "\n".join(hits)


def _run_govern(*, existing, pairs):
    """以 fake 連線跑一次 `govern_company_names`，回傳實際寫入的列。

    `existing` 每筆 = (代碼, 公司中文名稱, 正規化名稱, 別稱)
    """
    from unittest import mock

    written: list[dict] = []

    class FakeConn:
        def execute(self, sql, params=()):
            # ⚠ 兩條 SELECT 要分開處理：第一條取既有組（本測試的重點），
            # 第二條是待中文化偵測（另有專門測試驗其 SQL）。不能只看 "SELECT"
            # ——needs_zh 的 COALESCE 提到多個欄名，會被 _project 誤投影。
            if "SELECT DISTINCT" in sql:
                return FakeCursor([])
            if "SELECT" in sql:
                return FakeCursor(_project(sql, existing))
            if "INSERT INTO" in sql:
                written.append(_bind(sql, params))
            return FakeCursor([])

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    from backend.app.derived import company_alias_importer as m

    # ⚠ `govern_company_names` 是**函式內** `import psycopg`（延遲載入避免匯入期拉
    # DB 相依），所以 patch 模組屬性抓不到——必須換掉 `sys.modules` 裡的 psycopg。
    fake_psycopg = mock.MagicMock()
    fake_psycopg.connect.return_value = FakeConn()
    with mock.patch.dict("sys.modules", {"psycopg": fake_psycopg}):
        m.govern_company_names(pairs, connect_kwargs={})
    return written


_ALL_COLS = ("申請人代碼", "公司中文名稱", "正規化名稱", "別稱")


def _project(sql, existing):
    """依 SELECT 實際列出的欄位投影 existing，模擬真實查詢結果的欄序。

    ⚠ 欄序必須取 **SQL 裡的出現順序**，不是 `_ALL_COLS` 的宣告順序——
    兩者不同時（本例 SQL 是 代碼/中文/正規化/公司名稱/別稱，宣告是
    代碼/公司名稱/中文/正規化/別稱）會錯位，值全變 None 而看不出原因。
    """
    import re

    # 只掃 SELECT…FROM 之間的投影清單——整句掃會把 `derived_layer.company_aliases`
    # 這種表名也算進去（`company_aliases` 剛好在 _ALL_COLS 外但引號內的欄名不只投影區）。
    select_list = sql.split("FROM", 1)[0]
    cols = [c for c in re.findall(r'"([^"]+)"', select_list) if c in _ALL_COLS]
    idx = {c: _ALL_COLS.index(c) for c in cols}
    return [tuple(row[idx[c]] for c in cols) for row in existing]


def _bind(sql, params):
    """把 INSERT 的欄位與 params 對回 dict，供斷言檢查。"""
    head = sql.split("(", 1)[1].split(")", 1)[0]
    cols = [c.strip().strip('"') for c in head.split(",")]
    return dict(zip(cols, params))


if __name__ == "__main__":
    unittest.main()
