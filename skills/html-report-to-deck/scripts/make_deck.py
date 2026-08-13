"""用 content JSON 產出簡報（手動跑的單一入口）。

content JSON 的 schema 見 SKILL.md；範本見 references/content-template.json。
內容由撰稿者（agent 或人）填，本腳本只負責組版與印出驗收數據。

用法：python make_deck.py <content.json> <png_dir> <out.pptx>
回傳碼：0 = 版面無溢出；1 = 有溢出（必須改文字或配置後重跑）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deck_layout import build   # noqa: E402


def main() -> int:
    content = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    # JSON 沒有 tuple：把封面統計與兩個說明區塊還原成 (值, 標籤) 的形式
    content["stats"] = [tuple(x) for x in content["stats"]]
    for k in ("read_me", "chart_rule"):
        content[k] = tuple(content[k])
    bad = build(content, sys.argv[2], sys.argv[3])
    if bad:
        print(f"\n⚠ 有 {bad} 個區域溢出，請縮短該處文字後重跑；不要直接交付。")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
