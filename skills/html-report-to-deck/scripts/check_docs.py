"""稽核 skill 文件與程式是否一致——防止文件悄悄失真。

⚠ **為什麼需要它**：2026-08-11 一天之內踩到兩次「照自己寫的文件做，然後炸掉」——
   `PLAYWRIGHT_BROWSERS_PATH` 指錯一層、撰稿步驟編號寫成第 4 步（實際是第 5 步）。
   兩次都是**跌到才發現**。文件錯不會有任何測試變紅，只會讓下一個照做的人浪費一輪。

檢查三類：
  ① 引用的腳本／檔案／連結是否存在，以及有沒有腳本從未被文件提到
  ② 文件裡寫的數字是否等於程式算出來的值（**從文件裡把數字讀出來比對**，
     不是在這裡另寫一份——否則這支腳本自己也會失真）
  ③ 步驟編號：CLI 區塊的註解編號與內文引用的「第 N 步」是否對得上

用法：python check_docs.py        # 回傳碼 0 = 一致
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from deck_layout import (BAND_BOT, CHART_TOP, CW, LABEL_GAP,  # noqa: E402
                         LABEL_W, LS_RENDER, MIN_CHART_PT,
                         MIN_CHART_PT_MULTI, _per_line, _text_page_lines)

# 文件裡的數字 → 程式算出來的值。regex 的第 1 個 group 就是文件寫的數字。
# ⚠ 新增規則時**不要把期望值寫死**，一律指向程式算得出來的東西。
CLAIMS: list[tuple[str, str, float]] = [
    ("單圖頁字級下限", r"單圖頁圖內字 ≥ (\d+)pt", MIN_CHART_PT),
    ("雙圖頁字級下限", r"\*\*雙圖頁 ≥ (\d+)pt\*\*", MIN_CHART_PT_MULTI),
    ("純文字頁行數", r"上限約 (\d+) 個顯示行", _text_page_lines()),
    ("滿版行長", r"滿版單欄每行約 \*\*(\d+) 個全形字\*\*", round(_per_line(CW - 0.52))),
    ("標籤欄內文行長", r"行長降到約 \*\*(\d+) 個全形字\*\*",
     round(_per_line(CW - 0.52 - LABEL_W - LABEL_GAP))),
    ("標籤欄容量", r"標籤要短（≤ (\d+\.\d+) 單位）", round(_per_line(LABEL_W), 1)),
    ("左欄寬", r"左欄吃掉 (\d+\.\d+)in", LABEL_W),
    ("渲染行高倍率", r"字級的 (\d+\.\d+) 倍", LS_RENDER),
]


def main() -> int:
    docs = [ROOT / "SKILL.md"] + sorted((ROOT / "references").glob("*.md"))
    text = {d: d.read_text(encoding="utf-8") for d in docs}
    joined = "\n".join(text.values())
    bad: list[str] = []

    # ── ① 引用完整性 ────────────────────────────────────────
    for d, t in text.items():
        for m in re.finditer(r"(?:<S>|<skill>)[\\/](?:scripts[\\/])?([A-Za-z_]+\.py)", t):
            if not (ROOT / "scripts" / m.group(1)).is_file():
                bad.append(f"{d.name}: 引用不存在的腳本 {m.group(1)}")
        for m in re.finditer(r"`(scripts|references)/([A-Za-z0-9_.\-]+)`", t):
            if not (ROOT / m.group(1) / m.group(2)).is_file():
                bad.append(f"{d.name}: 引用不存在的檔案 {m.group(1)}/{m.group(2)}")
        for m in re.finditer(r"\]\((references/[A-Za-z0-9_.\-]+)\)", t):
            if not (ROOT / m.group(1)).is_file():
                bad.append(f"{d.name}: 連結目標不存在 {m.group(1)}")
    for f in sorted((ROOT / "scripts").glob("*.py")):
        if f.name not in joined:
            bad.append(f"scripts/{f.name} 存在但沒有任何文件提到")

    # ── ② 數字一致性 ────────────────────────────────────────
    print("文件數字 vs 程式實際值：")
    for label, pat, actual in CLAIMS:
        m = re.search(pat, joined)
        if not m:
            bad.append(f"找不到「{label}」的敘述（regex 沒對上，文件改寫過就要同步改這裡）")
            print(f"  ？ {label}：文件裡找不到")
            continue
        doc_val = float(m.group(1))
        ok = abs(doc_val - float(actual)) < 0.05
        print(f"  {'✓' if ok else '✗'} {label}：文件 {m.group(1)}｜程式 {actual}")
        if not ok:
            bad.append(f"「{label}」文件寫 {m.group(1)}，程式是 {actual}")

    # ── ③ 步驟編號 ─────────────────────────────────────────
    skill = text[ROOT / "SKILL.md"]
    # ⚠ 檔案裡有多個 ```powershell 區塊，要挑**流程**那一塊（含 extract_report.py），
    #   不能拿 re.search 找到的第一個（那是資料庫補查的範例）。
    blocks = [b for b in re.findall(r"```powershell\n(.*?)```", skill, re.S)
              if "extract_report.py" in b]
    if blocks:
        steps = {int(n): d.strip() for n, d in
                 re.findall(r"^# (\d+) (.+)$", blocks[0], re.M)}
        print(f"\nCLI 步驟：" + "、".join(f"{k} {v[:6]}" for k, v in sorted(steps.items())))
        # 內文寫「第 N 步撰稿」時，編號要對得上 CLI 區塊。
        # ⚠ 只在**該詞真的是某個步驟的名字**時才比對——「第 6 步會擋」「第 4 步（實際
        #   第 5 步）」這種敘述句裡的詞不是步驟名，硬比會噴一堆假警報（實測踩過）。
        for m in re.finditer(r"第 (\d+) 步[（(]?([一-鿿]{2})", skill):
            n, word = int(m.group(1)), m.group(2)
            owners = [k for k, v in steps.items() if word in v]
            if owners and n not in owners:
                bad.append(f"SKILL.md 寫「第 {n} 步{word}」，但「{word}」在 CLI 是"
                           f"第 {owners[0]} 步（第 {n} 步是「{steps.get(n, '?')[:12]}」）")

    print(f"\n問題 {len(bad)} 項")
    for b in bad:
        print("  ✗", b)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
