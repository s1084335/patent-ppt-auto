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
    content_path = Path(sys.argv[1])
    content = json.loads(content_path.read_text(encoding="utf-8"))
    # JSON 沒有 tuple：把封面統計與兩個說明區塊還原成 (值, 標籤) 的形式
    content["stats"] = [tuple(x) for x in content["stats"]]
    for k in ("read_me", "chart_rule"):
        content[k] = tuple(content[k])
    # 來源行蓋章（design §7.1，機械）：值取自同目錄 report.json 的
    # report_meta.source_file——**CLI 不參與**，content.json 自帶也蓋掉，
    # 不給 CLI 竄改來源的通道。report.json 不在（開發側直跑舊素材）就不印。
    report_path = content_path.parent / "report.json"
    if report_path.is_file():
        meta = json.loads(report_path.read_text(encoding="utf-8")).get("report_meta") or {}
        content["_source_version"] = meta.get("source_file") or None
    else:
        content.pop("_source_version", None)
    bad = build(content, sys.argv[2], sys.argv[3])
    # 可選第 4 參數：頁面 SVG 輸出目錄（B 案目視截圖的來源；runner 產線用）。
    # ⚠ build 與 build_svg 共用 _compose（唯一落點），兩者輸出的是同一份版面。
    if len(sys.argv) > 4:
        from deck_layout import build_svg
        pages = build_svg(content, sys.argv[2], sys.argv[4])
        print(f"SVG：{len(pages)} 頁 → {sys.argv[4]}")
    if bad:
        # ⚠ 涵蓋兩類：版面溢出與圖內字級不足。上方 build 已逐項印出是哪一種。
        print(f"\n⚠ 有 {bad} 個問題（版面溢出或圖內字級不足），"
              f"請依上方逐項建議處理後重跑；不要直接交付。")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
