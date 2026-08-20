"""母體嫌疑清單：找出繞過 `run_report` 自行查 DB 的彙總（tasks §1.1，唯讀）。

## 為什麼要掃

同型錯誤已出現三次：
1. 報表引擎母體 61 vs 實際 55（2026-08-17 已修）
2. 受理局頁家族註記 187 vs 實際 48
3. 封面三分法 281 件（設計 21）vs 實際 55（設計 11）——SQL 直接沒有 WHERE

⚠ 逐次修不如一次掃完。判準：**自行 `cur.execute`／`conn.execute` 且 SQL 帶彙總**
（`count(`／`sum(`／`GROUP BY`／`array_agg` 等）的函式，都是嫌疑。

本腳本只列清單、不下判斷——「是不是真的該吃 patent_ids」要人工看，
因為有些彙總本來就是全庫用途（例如公司別稱治理）。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("backend/app/reports", "backend/app/app_layer", "backend/app/derived",
             "backend/app/api", "backend/app/repositories", "backend/app/mcp_server")

#: 彙總的跡象（SQL 字面裡出現即算）
AGG = re.compile(r"\b(count\s*\(|sum\s*\(|avg\s*\(|min\s*\(|max\s*\(|"
                 r"array_agg|jsonb_agg|string_agg|group\s+by)", re.IGNORECASE)
#: 母體條件的跡象——有這些就先當「已接母體」，仍列出供人工複核
SCOPED = re.compile(r"patent_ids|workspace_id|= ANY\s*\(|patent_id\s+IN\b", re.IGNORECASE)


def sql_literals(node: ast.AST) -> list[str]:
    """取出函式體內所有字串常數（f-string 取其字面片段）。"""
    out: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
        elif isinstance(sub, ast.JoinedStr):
            out.append("".join(v.value for v in sub.values
                               if isinstance(v, ast.Constant) and isinstance(v.value, str)))
    return out


def executes_sql(node: ast.AST) -> bool:
    """函式體內有沒有自行 execute（cur.execute／conn.execute）。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr in ("execute", "executemany"):
                return True
    return False


rows: list[dict] = []
for rel in SCAN_DIRS:
    for path in sorted((ROOT / rel).rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not executes_sql(node):
                continue
            sql = "\n".join(sql_literals(node))
            if not AGG.search(sql):
                continue
            rows.append({
                "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                "func": node.name,
                "line": node.lineno,
                "scoped": bool(SCOPED.search(sql)),
            })

unscoped = [r for r in rows if not r["scoped"]]
scoped = [r for r in rows if r["scoped"]]

print(f"自行查 DB 且含彙總的函式：{len(rows)} 個\n")
print(f"🔴 未見母體條件（{len(unscoped)} 個）——逐個人工判定是否為全庫用途")
for r in unscoped:
    print(f"    {r['file']}:{r['line']}  {r['func']}")
print(f"\n·  已見母體條件（{len(scoped)} 個）——仍需複核條件是否真的生效")
for r in scoped:
    print(f"    {r['file']}:{r['line']}  {r['func']}")

sys.exit(0)
