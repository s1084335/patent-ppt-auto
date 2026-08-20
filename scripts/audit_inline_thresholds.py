"""第二遍：函式內**沒有名字**的數值門檻（唯讀）。

第一遍只掃 module-level 具名常數。但同一種病更常見的形狀是
`if total < 5:` ——數字連名字都沒有，改的人不知道它從哪來、影響誰。

⚠ 只掃「與資料比較」的數字（`Compare` 節點），不掃算術。算術裡的數字多半是
單位換算（/100、*72），不是判準。
⚠ 排除 0 與 1：`> 0`、`== 1`、`>= 1` 幾乎都是存在性檢查，不是門檻。
"""
from __future__ import annotations

import ast
from pathlib import Path

# ⚠ 由檔案位置推導，不寫死路徑——寫死的話換一個 worktree 就掃到別人的樹，
#   而且會「掃得很成功」地掃錯（同 §9.9「把一份觀察當成常數」的形狀）。
ROOT = Path(__file__).resolve().parents[1]
DIRS = [ROOT / "backend/app/reports", ROOT / "backend/app/clustering",
        ROOT / "backend/app/derived", ROOT / "backend/app/mappings"]

# 這些名字出現在比較的另一邊 ⇒ 是在跟**資料**比，不是跟索引或長度上限比
DATA_WORDS = ("count", "total", "share", "ratio", "median", "value", "score",
              "year", "num", "n_", "size", "len", "sum", "avg", "mean",
              "patent", "applicant", "topic", "row")


class Visitor(ast.NodeVisitor):
    def __init__(self, path: Path, src: list[str]):
        self.path, self.src, self.hits = path, src, []
        self.func = None

    def visit_FunctionDef(self, node):
        prev, self.func = self.func, node.name
        self.generic_visit(node)
        self.func = prev

    def visit_Compare(self, node):
        parts = [node.left, *node.comparators]
        nums = [p for p in parts
                if isinstance(p, ast.Constant)
                and isinstance(p.value, (int, float))
                and not isinstance(p.value, bool)
                and p.value not in (0, 1)]
        if nums:
            blob = ast.unparse(node).lower()
            if any(w in blob for w in DATA_WORDS):
                self.hits.append((node.lineno, self.func or "<模組層>",
                                  ast.unparse(node)[:88]))
        self.generic_visit(node)


total = 0
for d in DIRS:
    for p in sorted(d.rglob("*.py")):
        src = p.read_text(encoding="utf-8-sig")
        v = Visitor(p, src.splitlines())
        v.visit(ast.parse(src))
        if not v.hits:
            continue
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        print(f"\n=== {rel} ({len(v.hits)}) ===")
        for ln, fn, code in v.hits:
            print(f"  L{ln:<6} {fn:<38} {code}")
        total += len(v.hits)

print(f"\n合計：函式內與資料比較的裸數字 {total} 處")
