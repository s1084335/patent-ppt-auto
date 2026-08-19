"""色票實查（唯讀）：chart／deck／HTML 三側色彩落點盤點。

⚠ 只產判斷依據，不改任何檔案。

## 量測方法的兩個坑（都踩過，已修）

1. **只看該行是不是 `NAME = ...`**：`STATUS_COLORS` 這類多行 dict 的續行會全被
   算成散落（第一版把散落灌水成 68）。
2. **用括號計數補救**：字串字面裡的括號也會被數進去——`chart_runner` 有大段
   內嵌 CSS 模板（`{{ }}`），depth 從此永遠對不回 0，於是 `RANKING_BAR_SCALE`
   這個**單行具名常數**也被判成散落（第二版 61）。

本版改用 `ast`：模組層 `Assign`／`AnnAssign` 的 `lineno..end_lineno` 就是常數區的
真實範圍，字串內容完全不影響。⚠ 教訓：正則猜語法結構在有內嵌 DSL 的檔案上必錯。
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

# ⚠ 由檔案位置推導，不寫死路徑——寫死的話換一個 worktree 就掃到別人的樹，
#   而且會「掃得很成功」地掃錯（同 §9.9「把一份觀察當成常數」的形狀）。
ROOT = Path(__file__).resolve().parents[1]

CHART_FILES = [ROOT / "backend/app/reports" / f for f in (
    "chart_runner.py", "chart_sizing.py", "content_blocks.py", "cluster_analytics.py")]
DECK_FILES = [ROOT / "skills/html-report-to-deck/scripts" / f for f in (
    "deck_layout.py", "svg_canvas.py", "fit_render_charts.py",
    "rebuild_chip_chart.py", "apply_chart_marks.py")]

HEX = re.compile(r"#([0-9a-fA-F]{6})\b")
RGBCOLOR = re.compile(r"RGBColor\(\s*(0x[0-9a-fA-F]{2}|\d{1,3})\s*,\s*"
                      r"(0x[0-9a-fA-F]{2}|\d{1,3})\s*,\s*"
                      r"(0x[0-9a-fA-F]{2}|\d{1,3})\s*\)")


def _num(t: str) -> int:
    return int(t, 16) if t.lower().startswith("0x") else int(t)


def _strip_comment(line: str) -> str:
    out, i = [], 0
    while i < len(line):
        if line[i] == "#":
            if HEX.match(line, i):
                out.append(line[i:i + 7])
                i += 7
                continue
            break
        out.append(line[i])
        i += 1
    return "".join(out)


def docstring_lines(tree: ast.AST) -> set[int]:
    """所有 docstring 佔用的行號。

    ⚠ `_strip_comment` 只處理 `#` 註解，**不處理 docstring**——而 docstring 裡
    引用色值（「原本 9 個 `--paper: #F4F6F9;` 寫死在模板裡」）非常自然。
    不排除的話掃描器會把說明文字算成散落用法，逼人去改註解來讓數字歸零
    ——那是為過閘門而改文件，本專案「註解破壞斷言」的第 8 次，
    只是這次方向是**製造假陽性**而不是假通過。
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            out |= set(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return out


def const_ranges(path: Path) -> dict[int, str]:
    """行號 -> 該行所屬的模組層常數名（不在常數內的行不出現在字典裡）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[int, str] = {}
    for node in tree.body:                      # 只看模組層，函式內一律算散落
        name = None
        if isinstance(node, ast.Assign) and node.targets:
            t = node.targets[0]
            name = t.id if isinstance(t, ast.Name) else None
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        if name and name.isupper():
            for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                out[ln] = name
    return out


def scan(paths):
    found = defaultdict(list)
    for p in paths:
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8-sig")
        consts = const_ranges(p)
        skip = docstring_lines(ast.parse(text))
        for i, raw in enumerate(text.splitlines(), 1):
            if i in skip:
                continue
            code = _strip_comment(raw)
            hits = [m.group(1).upper() for m in HEX.finditer(code)]
            hits += ["%02X%02X%02X" % tuple(_num(g) for g in m.groups())
                     for m in RGBCOLOR.finditer(code)]
            for h in hits:
                found[h].append((p.name, i, consts.get(i)))
    return found


def summarize(label, found):
    occ = [(h, f) for h, v in found.items() for f in v]
    const = [x for x in occ if x[1][2]]
    loose = [x for x in occ if not x[1][2]]
    named = {h for h, _ in const}
    print(f"\n===== {label} =====")
    print(f"  不同顏色 {len(found)} 種｜出現 {len(occ)} 次"
          f"（具名常數 {len(const)}／散落 {len(loose)}）")
    print(f"  ⚠ 完全沒有具名常數的顏色：{len({h for h, _ in loose} - named)} 種")
    per = defaultdict(lambda: [0, 0])
    for h, f in occ:
        per[f[0]][0 if f[2] else 1] += 1
    for fn, (c, s) in sorted(per.items(), key=lambda kv: -kv[1][1]):
        print(f"    {fn:<26} 具名 {c:>3}   散落 {s:>3}")
    return found


chart = summarize("chart 側（backend/app/reports）", scan(CHART_FILES))
deck = summarize("deck 側（html-report-to-deck/scripts）", scan(DECK_FILES))

both = sorted(set(chart) & set(deck))
print(f"\n===== 兩側都出現＝同一份知識兩個落點：{len(both)} 種 =====")
for h in both:
    cn = sorted({f[2] or f"{f[0]}:{f[1]}" for f in chart[h]})
    dn = sorted({f[2] or f"{f[0]}:{f[1]}" for f in deck[h]})
    print(f"  #{h}\n      chart: {cn}\n      deck : {dn}")

cr = scan([ROOT / "backend/app/reports/chart_runner.py"])
loose = sorted([(f[1], h) for h, v in cr.items() for f in v if not f[2]])
print(f"\n===== §6.4 判準：chart_runner 散落裸 hex = {len(loose)} 處 =====")
# 依區段分群，看它們各屬哪個媒介
buckets = {"SVG 圖元（函式內）": [], "HTML 報表 CSS 變數": [], "HTML 表格內嵌樣式": []}
for line, h in loose:
    if 3580 <= line <= 3730:
        buckets["HTML 報表 CSS 變數"].append((line, h))
    elif 4530 <= line <= 4550:
        buckets["HTML 表格內嵌樣式"].append((line, h))
    else:
        buckets["SVG 圖元（函式內）"].append((line, h))
for k, v in buckets.items():
    print(f"  {k}：{len(v)} 處  {sorted({h for _l, h in v})}")
