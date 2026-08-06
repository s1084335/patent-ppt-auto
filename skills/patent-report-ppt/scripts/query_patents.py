"""唯讀專利資料查詢閘道——給報告產製 CLI 自主取證用。

## 用途

產報告的 CLI（headless Claude Code）看著圖表與 report_data 的數據，**自行判斷**
還需要哪些證據來寫分析（某申請人的逐年布局、某主題全部獨立項、某年度清單的
同族組成……），用本工具直接查資料庫取回。

⚠ 這是「給地圖，不是給白名單」：本工具不限定能查什麼，只保證**查不壞任何東西**。
schema 地圖與取證守則見同目錄 `data_access.md`。

## 用法（可攜，不 import 主專案）

    uv run --no-project --python 3.12 --with "psycopg[binary]" python query_patents.py \
        --sql "SELECT id, title FROM core_layer.patents LIMIT 5"

    # 多行 SQL 建議寫檔再帶入，避免 shell 逸出問題
    uv run --no-project --python 3.12 --with "psycopg[binary]" python query_patents.py \
        --sql-file q.sql --limit 200

輸出：stdout 一份 JSON——`{"columns": [...], "rows": [[...], ...], "row_count": N,
"truncated": bool}`。錯誤時 exit 2、stderr 印原因。

## 安全設計（為什麼敢開放自由查詢）

1. **連線層唯讀**：`default_transaction_read_only=on` 由 PostgreSQL 強制執行，
   任何寫入（含 CTE 夾帶 `DELETE ... RETURNING`）都會被 DB 端拒絕——
   這才是真護欄，關鍵字檢查只是友善的提前報錯。
2. `statement_timeout=30s`：慢查詢不會掛住整個產報流程。
3. 列數上限（預設 500、`--limit` 最高 2000）：防止一次拖回全表把 context 撐爆。
4. 單一語句：只接受一句 `SELECT`／`WITH`，分號串接直接拒絕。

## 連線

讀環境變數 `DATABASE_URL`（Companion 由專案 `.env` 載入）。
⚠ migration 之外的一般查詢 6543／5432 皆可；本工具只讀，不受 pooler 寫入限制影響。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal

# Windows console 預設 cp950，遇到罕用字會 UnicodeEncodeError；輸出一律 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

MAX_LIMIT = 2000
DEFAULT_LIMIT = 500
# 寫入類關鍵字（word boundary，"created_at" 不會誤中 "CREATE"）。
# ⚠ 只是提前報錯讓訊息好懂；真正的防線是連線層 read-only。
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|VACUUM|CALL|DO)\b",
    re.IGNORECASE,
)


def _fail(message: str) -> "NoReturn":  # noqa: F821 - py3.12 有 NoReturn，字串註記免 import
    print(f"query_patents: {message}", file=sys.stderr)
    raise SystemExit(2)


def _jsonable(value):
    """DB 值轉 JSON 可序列化：日期轉 ISO 字串、Decimal 轉 float、bytes 只回長度。

    ⚠ bytes（主附圖 bytea）不進輸出——圖片對取證毫無用處，卻能單筆撐爆 context。
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, memoryview)):
        return f"<bytes:{len(value)}>"
    return value


def validate_sql(sql: str) -> str:
    """單句、唯讀語法的提前檢查；回傳去除尾端分號的 SQL。"""
    text = sql.strip()
    if not text:
        _fail("SQL 是空的")
    text = text.rstrip(";").strip()
    if ";" in text:
        _fail("只接受單一語句（偵測到分號串接）")
    if not re.match(r"^(SELECT|WITH)\b", text, re.IGNORECASE):
        _fail("只接受 SELECT／WITH 開頭的唯讀查詢")
    hit = _FORBIDDEN.search(text)
    if hit:
        _fail(f"查詢含寫入類關鍵字 {hit.group(0)!r}——本工具唯讀；"
              "若只是字串字面值撞名，請改寫避開")
    return text


def run_query(sql: str, limit: int) -> dict:
    import psycopg

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        _fail("環境變數 DATABASE_URL 未設定（Companion 應由專案 .env 載入）")
    # options 由 server 端強制唯讀＋逾時；autocommit 免留 idle transaction。
    with psycopg.connect(
        dsn,
        autocommit=True,
        options="-c default_transaction_read_only=on -c statement_timeout=30000",
    ) as conn, conn.cursor() as cur:
        cur.execute(sql)
        columns = [d.name for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit + 1)
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {
        "columns": columns,
        "rows": [[_jsonable(v) for v in row] for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="唯讀專利資料查詢（取證用）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sql", help="單句 SELECT/WITH 查詢")
    group.add_argument("--sql-file", help="含單句查詢的檔案路徑（多行 SQL 建議用這個）")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"回傳列數上限（預設 {DEFAULT_LIMIT}、最高 {MAX_LIMIT}）")
    args = parser.parse_args()

    sql = args.sql
    if args.sql_file:
        try:
            sql = open(args.sql_file, encoding="utf-8").read()
        except OSError as exc:
            _fail(f"讀不到 --sql-file：{exc}")
    limit = max(1, min(args.limit, MAX_LIMIT))

    try:
        result = run_query(validate_sql(sql), limit)
    except SystemExit:
        raise
    except Exception as exc:  # DB 錯誤原样轉出，讓 CLI 能自行修正查詢
        _fail(f"查詢失敗：{type(exc).__name__}: {exc}")
    json.dump(result, sys.stdout, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
