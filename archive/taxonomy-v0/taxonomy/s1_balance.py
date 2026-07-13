"""站1-b 語料平衡：把過量線抽樣降到目標量，溢出的移入備用檔

為什麼要做：AutoPhrase 的片語統計是全語料一起跑的。批4/批5 之後 motor 3,367、
ebike 2,453，兩線就吃掉近半語料，片語會偏向馬達與自行車詞彙，其餘八線的技術手段
被稀釋 → 共現訊號跟著歪。

作法：對超過 --target 的線隨機抽樣（固定 seed，可重現）保留 target 筆，溢出的整列
移入 備用語料.xlsx（**不是廢棄**：這些是領域內的好資料，只是這一輪用不到，日後要
擴充語料直接從這裡撈回）。歸屬清單（全量履歷）不動，履歷仍可回溯。

用法:
  uv run --no-project --with pandas --with openpyxl --python 3.12 python s1_balance.py --target 1000 --dry-run
  uv run --no-project --with pandas --with openpyxl --python 3.12 python s1_balance.py --target 1000
冪等：已經在目標量以下的線不動；重跑不會重複搬移。
"""
import argparse
import pathlib
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).parent
CORPUS = HERE / "../../data/signal corpus/共現語料資料.xlsx"
RESERVE = HERE / "../../data/signal corpus/備用語料.xlsx"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1000, help="每線保留上限")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    dl = pd.read_excel(CORPUS, sheet_name="download")
    own = pd.read_excel(CORPUS, sheet_name="歸屬清單")

    # 主鍵用三號碼任一匹配：未授權的公開案沒有公告號（空字串不是 NaN），
    # 只拿公告號當 key 會讓這些筆的線歸屬整批遺失。
    ID_COLS = ["授权公告号", "未审查的公开号", "申请号"]

    def norm(v) -> str:
        s = str(v).strip()
        return "" if s.lower() in ("nan", "none", "") else s

    line_of = {}
    for _, r in own.iterrows():
        for c in ID_COLS:
            k = norm(r[c])
            if k:
                line_of.setdefault(k, r["歸屬線"])

    def lookup(row):
        for c in ID_COLS:
            k = norm(row[c])
            if k in line_of:
                return line_of[k]
        return None

    dl["_line"] = dl.apply(lookup, axis=1)
    unmapped = int(dl["_line"].isna().sum())
    if unmapped:
        print(f"⚠ {unmapped} 筆在歸屬清單找不到歸屬線（三號碼皆未命中）")

    before = Counter(dl["_line"].dropna())
    over = {k: v for k, v in before.items() if v > a.target and k != "領域外"}
    print(f"目標量: {a.target} 筆/線")
    print("現況:", dict(sorted(before.items(), key=lambda x: -x[1])))
    if not over:
        print("沒有超量的線，不需平衡。")
        return
    print("超量線:", {k: v for k, v in sorted(over.items(), key=lambda x: -x[1])})

    keep_idx, move_idx = [], []
    for line, grp in dl.groupby("_line", dropna=True):
        if line in over:
            kept = grp.sample(n=a.target, random_state=a.seed)
            keep_idx += list(kept.index)
            move_idx += [i for i in grp.index if i not in set(kept.index)]
        else:
            keep_idx += list(grp.index)
    keep_idx += [i for i in dl.index if pd.isna(dl.loc[i, "_line"])]

    kept_df = dl.loc[sorted(keep_idx)].drop(columns=["_line"])
    moved_df = dl.loc[sorted(move_idx)].drop(columns=["_line"])
    moved_lines = Counter(dl.loc[move_idx, "_line"])

    print(f"\n保留 {len(kept_df)} 筆 | 移入備用 {len(moved_df)} 筆 {dict(moved_lines)}")
    print("平衡後各線:", dict(sorted(Counter(dl.loc[keep_idx, '_line'].dropna()).items(),
                                     key=lambda x: -x[1])))
    if a.dry_run:
        print("\n[乾跑] 未寫入任何檔案。")
        return

    # 備份到系統暫存，不落在 data 目錄：語料夾只該有 共現語料資料/廢棄資料/備用語料 三個檔
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp = pathlib.Path(tempfile.gettempdir()) / "signal-corpus-bak"
    tmp.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CORPUS, tmp / f"{CORPUS.stem}.bak_{stamp}.xlsx")
    print(f"備份（系統暫存，可救援）: {tmp}")

    # 備用檔：領域內、這輪用不到的好資料（跟 廢棄資料.xlsx 的「領域外」語意不同）
    if RESERVE.exists():
        old = pd.read_excel(RESERVE, sheet_name=0)
        moved_df = pd.concat([old, moved_df], ignore_index=True)
    with pd.ExcelWriter(RESERVE, engine="openpyxl") as w:
        moved_df.to_excel(w, sheet_name="download", index=False)

    rec = pd.read_excel(CORPUS, sheet_name="處理紀錄")
    cond = pd.read_excel(CORPUS, sheet_name="檢索條件")
    rec_add = pd.DataFrame({
        rec.columns[0]: [f"語料平衡（{datetime.now():%Y-%m-%d}）"],
        rec.columns[1]: [f"每線上限 {a.target}（seed {a.seed} 隨機抽樣）；"
                         f"移入 備用語料.xlsx {len(moved_df)} 筆 {dict(moved_lines)}；"
                         f"原因：motor/ebike 過量會讓 AutoPhrase 片語統計偏斜。"
                         f"備用≠廢棄，領域內好資料，日後擴充語料可直接撈回。"],
    })
    with pd.ExcelWriter(CORPUS, engine="openpyxl") as w:
        kept_df.to_excel(w, sheet_name="download", index=False)
        pd.concat([rec, rec_add], ignore_index=True).to_excel(w, sheet_name="處理紀錄", index=False)
        own.to_excel(w, sheet_name="歸屬清單", index=False)
        cond.to_excel(w, sheet_name="檢索條件", index=False)

    print(f"\n已寫入: download {len(dl)} → {len(kept_df)}；備用語料.xlsx {len(moved_df)} 筆")


if __name__ == "__main__":
    main()
