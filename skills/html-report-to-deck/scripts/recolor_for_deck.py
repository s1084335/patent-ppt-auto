"""SVG 進 deck 前整批換色（tasks §6.2b）。

## 為什麼要換

使用者裁決（§6.2，2026-08-19）：兩套深藍**都留，但不得同頁**。
分界不是「哪個模組」而是「哪個媒介」——HTML 報表用 `#00094A`、
PPTX 簡報用 `#0B2545`。報表側產的 SVG 直接嵌進投影片時，
圖內標題會是報表色、頁面文字是 deck 色，**同一頁兩種深藍**（實測 ΔE2000 = 10.53，
看得出來）。這支腳本在圖進 deck 之前把對照表上的色換成 deck 側的值。

## 對照表不在這裡

`chart_sizing.REPORT_TO_DECK` 是唯一定義處。本檔只消費、不定義——
兩份對照表會分岔，而分岔的症狀是「換了一半」，不是報錯。

## 擺在鏈上的哪裡

`assemble → plan → chip → **recolor** → fit → …`

- 必須在 `rebuild_chip_chart`（chip）**之後**：它會寫入報表側的色。
- 必須在 `fit_render_charts`（fit）**之前**：fit 產的 PNG 就是進投影片的畫素，
  之後再換就來不及了。
- `apply_chart_marks` 在更後面，但它寫的 `#B0123C` 本來就是 deck 側的色
  （`deck_layout.ROSE`），不需要轉換。

⚠ 換色**必須在 §6.3a 之後**才安全：`rebuild_chip_chart` 原本靠 `#9CA3AF`
這個色值辨認「哪段文字是註記」，先換色會讓它靜默回空字串、註記與頁尾消失。
該問題已於 2026-08-19 改用 `data-role` 解決。

用法：python recolor_for_deck.py <svg_dir> [--check]
回傳碼：0 = 通過；1 = 產物仍有報表側的色（--check 模式）
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from backend.app.reports.chart_sizing import (  # noqa: E402
    REPORT_TO_DECK,
    known_colours,
)

HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def _text_nodes(svg_text: str) -> int:
    """SVG 裡的 <text> 節點數。⚠ 這是 §6.2f 的守衛：換色不得增刪內容。"""
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return -1        # 解析不了就回 -1，讓前後比較必然不相等而露出來
    return sum(1 for el in root.iter() if el.tag.split("}")[-1] == "text")


def recolor_text(svg_text: str) -> str:
    """把對照表左欄的色換成右欄。

    ⚠ 大小寫都要換：SVG 裡可能寫成小寫，只換大寫等於換了一半，
    而「數大寫出現次數」的閘門會顯示已完成。
    ⚠ 只換對照表上的色——多換等於把別的設計一起改掉，而且不會有人發現。
    """
    def sub(m: re.Match) -> str:
        return REPORT_TO_DECK.get(m.group(0).upper(), m.group(0))

    return HEX.sub(sub, svg_text)


def recolor_dir(svg_dir: Path) -> dict:
    """就地換色整個目錄，回傳前後對帳數字（§6.2f）。"""
    files = sorted(Path(svg_dir).glob("*.svg"))
    before = after = 0
    changed = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        before += _text_nodes(text)
        new = recolor_text(text)
        if new != text:
            f.write_text(new, encoding="utf-8")
            changed += 1
        after += _text_nodes(new)
    return {"files": len(files), "changed": changed,
            "text_nodes_before": before, "text_nodes_after": after}


def check_dir(svg_dir: Path) -> list[str]:
    """§6.2c：驗**產物**——對照表左欄不得出現在進 deck 的 SVG 裡。

    ⚠ 驗產物不驗原始碼。斷言「原始碼有呼叫換色函式」是代理指標：
    本專案踩過「函式在、字串在、資料到不了，照樣綠」。
    """
    bad: list[str] = []
    for f in sorted(Path(svg_dir).glob("*.svg")):
        found = {m.group(0).upper() for m in HEX.finditer(f.read_text(encoding="utf-8"))}
        for src in sorted(found & set(REPORT_TO_DECK)):
            bad.append(f"{f.name} 仍有報表側的色 {src}（應為 {REPORT_TO_DECK[src]}）")
    return bad


def unknown_colours(svg_dir: Path) -> list[str]:
    """§6.2d：兩張色票都沒有的色。

    ⚠ 只擋已知左欄的話，新冒出來的第三種藍不會被發現——缺席型偏差。
    這裡不擋、只列出來：擋了會誤傷合理的新增，不列則等於沒發生過。
    """
    # ⚠ 走 known_colours()：漏掉色階的話，四套色階的每個色都會被報成未知，
    #   訊號被雜訊淹掉＝等於沒有這個功能。
    known = known_colours()
    seen: set[str] = set()
    for f in sorted(Path(svg_dir).glob("*.svg")):
        seen |= {m.group(0).upper() for m in HEX.finditer(f.read_text(encoding="utf-8"))}
    return sorted(seen - known)


def main() -> int:
    svg_dir = Path(sys.argv[1])
    if "--check" in sys.argv[2:]:
        bad = check_dir(svg_dir)
        for line in bad:
            print("✗", line)
        unknown = unknown_colours(svg_dir)
        if unknown:
            # 只揭露不擋：讓沒被涵蓋的色現形，由人判斷該不該進色票
            print(f"⚠ 不在色票內的色 {len(unknown)} 種：{', '.join(unknown)}")
        print("換色檢查：", "通過" if not bad else f"{len(bad)} 處未換")
        return 1 if bad else 0

    result = recolor_dir(svg_dir)
    print(f"換色完成：{result['changed']}/{result['files']} 張有變更")
    if result["text_nodes_before"] != result["text_nodes_after"]:
        # §6.2f：換色不得增刪內容。不相等即失敗，不靜默通過
        print(f"✗ 文字節點數變了：{result['text_nodes_before']} → "
              f"{result['text_nodes_after']}")
        return 1
    print(f"文字節點數對帳：{result['text_nodes_before']} = {result['text_nodes_after']}")
    unknown = unknown_colours(svg_dir)
    if unknown:
        print(f"⚠ 不在色票內的色 {len(unknown)} 種：{', '.join(unknown)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
