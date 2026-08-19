"""掃描：分析門檻是否綁死在「某一份 workspace 的資料」上（唯讀）。

## 為什麼掃

`classify_topic_status` 的門檻註解自承是從一份資料切出來的
（`STATUS_GROWTH_HIGH = 0.70` 註解寫「全庫基準 R＝38/55」，55 是滑雪機成員數），
而它是 module-level 常數、套用到每個 workspace。使用者：「分類器在這環節之前
就要依據建立在 workspace 的資料才有意義，這應該去掃程式。」

## 分三類，不混為一談

- **絕對年份**：最危險。時間會走，2020–2024 到 2027 年會讓全部主題判成衰退，
  而且沒有任何警報。
- **比例／門檻**：0<x<1 或與資料值比較的數字——該不該 per-run 推導要逐個看。
- **樣本下限**：min sample 之類，通常有統計理由，但下限值本身常是從一份資料抓的。

## 訊號：註解裡提到單一資料集

「本案」「實測」「全庫基準」「這批」「該批」「滑雪機」「割草機」——
出現這些字，幾乎確定是從一份觀察抓出來的數字。這是最強的判別訊號，
比看數值本身準。

⚠ 版面幾何（字級、邊距、欄寬）**不是**這種病：它們本來就該固定，
與資料無關。掃描要把它們分開列，不能混進來充數。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

# ⚠ 由檔案位置推導，不寫死路徑——寫死的話換一個 worktree 就掃到別人的樹，
#   而且會「掃得很成功」地掃錯（同 §9.9「把一份觀察當成常數」的形狀）。
ROOT = Path(__file__).resolve().parents[1]

# 分析路徑（會做判定／統計）。版面路徑另列，不混。
ANALYSIS_DIRS = [
    ROOT / "backend/app/reports",
    ROOT / "backend/app/clustering",
    ROOT / "backend/app/derived",
    ROOT / "backend/app/mappings",
]

# 單一資料集訊號
DATASET_HINT = re.compile(
    r"本案|實測|全庫基準|這批|該批|滑雪機|割草機|本輪|目前資料|現有資料|實際資料")

# 版面幾何的名稱特徵（這些固定是對的，分開列）
LAYOUT_NAME = re.compile(
    r"WIDTH|HEIGHT|MARGIN|PAD|GAP|SIZE|_PX|_PT|_IN$|RADIUS|FONT|COL|ROW_H|DPI|SCALE_",
    re.IGNORECASE)


def _num_of(node) -> object | None:
    """取常數節點的數值；tuple 回 tuple。非純數值回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Tuple):
        vals = [_num_of(e) for e in node.elts]
        if vals and all(v is not None for v in vals):
            return tuple(vals)
    return None


def _classify(name: str, value) -> str:
    flat = value if isinstance(value, tuple) else (value,)
    if all(isinstance(v, int) and 1900 <= v <= 2100 for v in flat):
        return "絕對年份"
    if LAYOUT_NAME.search(name):
        return "版面幾何"
    if all(isinstance(v, float) and 0 < v < 1 for v in flat):
        return "比例門檻"
    if any(k in name for k in ("MIN", "MAX", "LIMIT", "THRESHOLD", "SAMPLE", "TOP", "CUTOFF")):
        return "樣本／數量門檻"
    return "其他數值"


rows = []
for d in ANALYSIS_DIRS:
    for p in sorted(d.rglob("*.py")):
        text = p.read_text(encoding="utf-8-sig")  # ⚠ 有些檔帶 BOM，utf-8 讀進來 ast 會炸
        lines = text.splitlines()
        tree = ast.parse(text)
        for node in tree.body:
            name = None
            if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id
            if not name or not name.isupper():
                continue
            val = _num_of(node.value)
            if val is None:
                continue
            # 往上抓連續註解列，找單一資料集訊號
            i = node.lineno - 2
            comment = []
            while i >= 0 and lines[i].lstrip().startswith("#"):
                comment.insert(0, lines[i].strip())
                i -= 1
            blob = " ".join(comment)
            rows.append({
                "檔": str(p.relative_to(ROOT)).replace("\\", "/"),
                "行": node.lineno, "名": name, "值": val,
                "類": _classify(name, val),
                "單一資料集訊號": bool(DATASET_HINT.search(blob)),
                "註解摘要": (blob[:70] + "…") if len(blob) > 70 else blob,
            })

order = ["絕對年份", "比例門檻", "樣本／數量門檻", "其他數值", "版面幾何"]
print(f"分析路徑下的模組層數值常數共 {len(rows)} 個\n")
for kind in order:
    group = [r for r in rows if r["類"] == kind]
    flagged = [r for r in group if r["單一資料集訊號"]]
    print(f"{'='*78}\n{kind}：{len(group)} 個"
          f"（註解提到單一資料集：{len(flagged)}）")
    if kind == "版面幾何":
        print("  （固定是對的，列出僅供確認沒有分析門檻被誤歸類）")
        for r in group[:6]:
            print(f"    {r['檔']}:{r['行']} {r['名']} = {r['值']}")
        if len(group) > 6:
            print(f"    …另有 {len(group) - 6} 個")
        continue
    for r in sorted(group, key=lambda x: (not x["單一資料集訊號"], x["檔"], x["行"])):
        mark = "🔴" if r["單一資料集訊號"] else "  "
        print(f"  {mark} {r['檔']}:{r['行']}  {r['名']} = {r['值']}")
        if r["註解摘要"]:
            print(f"       └ {r['註解摘要']}")
