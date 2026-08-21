"""保留期硬刪（PRE-007）——**不可逆**，故只做成腳本、不接進 Web UI。

## 為什麼不做成端點

Web UI 上的一顆按鈕會被誤按，而這個動作**沒有還原路徑**：刪掉的是
`core_layer.patents` 的列，11 條 CASCADE 外鍵會連帶清掉附屬欄、圖、人物、
技術／功效向量、主題指派、檢索詞。tasks 明訂「預設停用」，腳本就是那個
「停用」的形式——要跑必須有人主動打指令。

## 用法

    # ① 看有哪些候選（預設就是這個，什麼都不會改）
    python scripts/prefilter_purge.py

    # ② 看某幾筆的刪除計畫（仍不改任何東西）
    python scripts/prefilter_purge.py --ids 123,456

    # ③ 真的刪——必須同時給 --execute 與 --i-understand-this-is-irreversible
    python scripts/prefilter_purge.py --ids 123,456 --execute \
        --i-understand-this-is-irreversible

⚠ 兩個旗標缺一不可是刻意的：`--execute` 容易在複製貼上指令時被一起帶走，
第二個旗標長到不會有人不小心打出來。

## ⚠ 尚未實作：報表版本標記

`PRE-007` 最後一條要求標記受影響的報表版本為「來源已不完整」，
**目前資料模型答不出「哪個版本含哪些專利」**（見 `purge.py` 模組 docstring）。
本腳本會在執行前把這件事印出來，不靜默略過。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ⚠ 必須先 import settings：它負責 `load_dotenv(.env, override=False)`。
# 少了這行，連線會退回 PGHOST 預設值而 PoolTimeout——症狀看起來像 DB 掛了。
# （其他入口是 worker/ai_bridge 的 `load_local_env()`，同一件事。）
from backend.app import settings  # noqa: E402,F401
from backend.app.prefilter import purge  # noqa: E402

CONFIRM_FLAG = "--i-understand-this-is-irreversible"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ids", help="要刪的 patent_id，以逗號分隔；不給＝只列候選")
    p.add_argument("--retention-days", type=int, default=purge.RETENTION_DAYS)
    p.add_argument("--limit", type=int, default=purge.DEFAULT_LIMIT)
    p.add_argument("--execute", action="store_true",
                   help="真的刪除（需同時給確認旗標）")
    p.add_argument(CONFIRM_FLAG, dest="confirmed", action="store_true",
                   help="確認理解此操作不可逆")
    return p


def main() -> int:
    args = build_parser().parse_args()

    candidates = purge.purge_candidates(retention_days=args.retention_days)
    print(f"保留期：{args.retention_days} 天")
    print(f"符合資格的候選：{len(candidates)} 筆")
    for row in candidates[:20]:
        print(f"  #{row['patent_id']}  最後封存於 {row['last_excluded_at']}"
              f"（在 {row['excluded_in_workspaces']} 個 workspace 被剔除）")
    if len(candidates) > 20:
        print(f"  …另有 {len(candidates) - 20} 筆")

    if not args.ids:
        print("\n未指定 --ids，只列候選，未做任何變更。")
        return 0

    ids = [int(x) for x in args.ids.split(",") if x.strip()]

    if args.execute and not args.confirmed:
        print(f"\n🔴 拒絕執行：給了 --execute 但沒給 {CONFIRM_FLAG}。")
        print("   兩個旗標缺一不可——這個動作沒有還原路徑。")
        return 2

    dry = not (args.execute and args.confirmed)
    print(f"\n模式：{'dry-run（不改任何資料）' if dry else '🔴 真的刪除'}")
    if not dry:
        print("⚠ 報表版本標記尚未實作：受影響的既有報表版本**不會**被標記為"
              "「來源已不完整」（資料模型答不出哪個版本含哪些專利）。")

    try:
        result = purge.purge_patents(ids, dry_run=dry,
                                     retention_days=args.retention_days,
                                     limit=args.limit)
    except purge.PurgeError as exc:
        print(f"\n🔴 {exc}")
        return 1

    print(f"計畫刪除：{result['planned']}")
    print(f"實際刪除：{result['deleted']} 筆")
    for fail in result["failed"]:
        print(f"  🔴 #{fail['patent_id']} 失敗：{fail['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
