"""站1 訊號語料驗收：新撈批次 → 去重 → 歸屬判定 → 體檢 → 合併

規則（沿用 2026-07-10 三批的作法，見 work-logs 2026-07-10 §13/§14）：
  歸屬三分法  ① CPC(Main) 屬線內分類碼 → CPC命中
              ② 否則 標題+摘要 含線內關鍵字 → 關鍵字救回
              ③ 皆無 → 廢棄（領域外）
  去重        三號碼（授权公告号/未审查的公开号/申请号）任一命中既有語料即跳過
  廢棄不刪    移出 download，但保留在 歸屬清單（全量履歷）與 廢棄資料.xlsx
  截斷檢查    Excel 單格上限 32,767 字元，独立项 被截斷會讓 means 語料無聲缺尾
  單一事實來源 所有共現語料只存在 共現語料資料.xlsx（乾淨）與 廢棄資料.xlsx（廢棄）兩檔；
              WIPS 撈下來的 TextDown_*.xlsx 原始檔整理完就刪（履歷已存在歸屬清單，可回溯）

用法:
  # 乾跑：只出報告，不動任何檔案
  uv run --no-project --with pandas --with openpyxl --python 3.12 python s1_intake.py \
      --src "../../data/signal corpus/TextDown_20260713_am101554_3259.xlsx" --dry-run
  # 正式合併（會先備份 共現語料資料.xlsx / 廢棄資料.xlsx）
  uv run --no-project --with pandas --with openpyxl --python 3.12 python s1_intake.py \
      --src "..." --batch 批4 --note "五線補撈：ebike/tilesaw/stands/woodsaw/motor"
冪等：同一批重跑會被三號碼去重擋下，不會重複併入。
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
DISCARD = HERE / "../../data/signal corpus/廢棄資料.xlsx"

EXCEL_CELL_LIMIT = 32767

# 線內 CPC 圍欄（四位子類）。Y 段（Y02P/Y10T/Y02W）是氣候與跨領域「標籤」不是技術分類，
# 一律不列；E21B（油井鑽探）曾把 drillpress 判歪，也不列。
LINE_CPC = {
    "fitness":    ["A63B"],
    "garden":     ["A01D", "A01G"],
    "tilesaw":    ["B28D", "B26D"],
    "woodsaw":    ["B27B", "B27G", "B27C"],
    "motor":      ["H02K", "H02P"],
    "stands":     ["B25H", "F16M"],
    "ebike":      ["B62M", "B62J", "B62K", "B60L", "B60K"],
    "drone":      ["B64U", "G05D", "B64C"],
    "metalsaw":   ["B23D", "B23K"],
    "drillpress": ["B23B", "B23Q"],
}

# 線內關鍵字（CPC 沒中時的救回條件；比對標題+摘要，小寫）。
# stands 依力山官網定義＝搭配斜切鋸/台鋸的「腳架」，不是泛稱工作台 —— 舊的 workbench/weld
# 會把焊接工作台判成 stands，故移除。
LINE_KEYWORDS = {
    "fitness":    ["treadmill", "exercise machine", "fitness equipment", "elliptical",
                   "exercise bike", "rowing machine", "weight training"],
    "garden":     ["lawn mower", "mowing", "lawnmower", "hedge trimmer", "grass trimmer",
                   "chain saw", "chainsaw", "garden tool", "brush cutter"],
    "tilesaw":    ["ceramic tile", "tile cutter", "tile cutting", "stone cutting",
                   "scoring wheel", "tile saw"],
    "woodsaw":    ["circular saw", "table saw", "miter saw", "chop saw", "riving knife",
                   "kickback", "saw guard", "woodworking"],
    "motor":      ["brushless", "motor control", "stator", "rotor", "inverter",
                   "synchronous motor", "permanent magnet"],
    "stands":     ["saw stand", "miter saw stand", "table saw stand", "tool stand",
                   "power tool stand", "roller stand", "work stand", "sawhorse",
                   "saw horse", "outfeed table", "universal stand"],
    "ebike":      ["electric bicycle", "e-bike", "ebike", "bicycle", "tricycle",
                   "hub motor", "power assisted bicycle"],
    "drone":      ["unmanned aerial", "drone", "uav", "flight control", "propeller"],
    "metalsaw":   ["band saw", "metal cutting", "cut-off machine", "laser cutting",
                   "circular sawing machine"],
    "drillpress": ["drill press", "drilling machine", "bench drill", "drill bit",
                   "boring machine"],
}

ID_COLS = ["授权公告号", "未审查的公开号", "申请号"]


def norm_id(v) -> str:
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "") else s


def patent_key(row) -> str:
    """主鍵 = COALESCE(授权公告号, 未审查的公开号, 申请号)（PoC spec P1 的規則）。

    未授權的公開案沒有公告號，該欄是**空字串不是 NaN** —— 直接拿公告號當主鍵，
    這些筆會全部撞成同一個 key（407 檔就有 160 筆這種）。
    """
    for c in ID_COLS:
        k = norm_id(row[c])
        if k:
            return k
    return ""


def assign_line(cpc: str, text: str) -> tuple[str, str]:
    """回傳 (歸屬線, 歸屬依據)。CPC 優先，其次關鍵字，皆無則廢棄。"""
    sub = str(cpc).strip()[:4].upper()
    for line, codes in LINE_CPC.items():
        if sub in codes:
            return line, "CPC命中"
    low = text.lower()
    for line, kws in LINE_KEYWORDS.items():
        if any(k in low for k in kws):
            return line, "關鍵字救回"
    return "領域外", "領域外"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--batch", default=None, help="批次標記，例如 批4")
    ap.add_argument("--note", default="", help="寫進處理紀錄的備註（建議附檢索式）")
    ap.add_argument("--dry-run", action="store_true", help="只出報告，不寫任何檔案")
    ap.add_argument("--keep-src", action="store_true",
                    help="合併後保留 WIPS 原始檔（預設刪除：履歷已進歸屬清單，原始檔不再是事實來源）")
    a = ap.parse_args()

    new = pd.read_excel(a.src, sheet_name=0)
    dl = pd.read_excel(CORPUS, sheet_name="download")
    own = pd.read_excel(CORPUS, sheet_name="歸屬清單")
    print(f"新檔 {len(new)} 筆 / 既有 download {len(dl)} 筆 / 歸屬清單 {len(own)} 筆")

    # 欄位對齊：WIPS 不同時期匯出的欄位數不一樣（舊檔 148 欄、新檔 26 欄）。
    # 一律對齊既有 download 的欄位，多的丟、缺的留白 —— 否則 download 會長出一堆空欄。
    extra = [c for c in new.columns if c not in dl.columns]
    missing = [c for c in dl.columns if c not in new.columns]
    if extra or missing:
        print(f"[欄位對齊] 新檔多 {len(extra)} 欄（丟棄）、缺 {len(missing)} 欄（留白）")
        if missing:
            print(f"           缺: {missing[:6]}{' …' if len(missing) > 6 else ''}")
        new = new.reindex(columns=dl.columns)
    print()

    # ---- 去重：三號碼任一命中既有（download 或歸屬清單，含廢棄筆）即算重複
    seen = set()
    for df_ in (dl, own):
        for c in ID_COLS:
            if c in df_.columns:
                seen |= {norm_id(v) for v in df_[c].dropna()}
    seen.discard("")

    dup_mask = new[ID_COLS].apply(
        lambda r: any(norm_id(v) in seen for v in r), axis=1)
    fresh = new[~dup_mask].copy()
    print(f"[去重] 重複跳過 {int(dup_mask.sum())} 筆 → 新增候選 {len(fresh)} 筆")

    # ---- 歸屬判定
    text = (fresh["标题"].astype(str) + " " + fresh["摘要"].astype(str))
    res = [assign_line(c, t) for c, t in zip(fresh["Curr. CPC(Main)"], text)]
    fresh["歸屬線"] = [r[0] for r in res]
    fresh["歸屬依據"] = [r[1] for r in res]

    keep = fresh[fresh["歸屬線"] != "領域外"].copy()
    drop = fresh[fresh["歸屬線"] == "領域外"].copy()
    print(f"[歸屬] 保留 {len(keep)} 筆 | 廢棄（領域外）{len(drop)} 筆")
    print("       依據:", dict(Counter(keep["歸屬依據"])))
    print("       新增各線:", dict(Counter(keep["歸屬線"]).most_common()))

    # ---- 截斷檢查（Excel 單格 32,767 上限；被截的話 means 語料會無聲缺尾）
    print("\n[截斷檢查]")
    for col in ["独立项[KR,JP,US,CN,EP,IN]", "摘要", "效果 摘要[US,EP,PCT,JP,KR,CN,TW]"]:
        if col not in new.columns:
            continue
        lens = new[col].astype(str).str.len()
        at_limit = int((lens >= EXCEL_CELL_LIMIT - 1).sum())
        near = int(((lens >= 30000) & (lens < EXCEL_CELL_LIMIT - 1)).sum())
        flag = "  ← 疑似被截斷！" if at_limit else ""
        print(f"  {col[:22]:24s} 最長 {lens.max():6d} | 撞上限 {at_limit} 筆 | 3萬字以上 {near} 筆{flag}")

    # ---- 體檢
    print("\n[體檢]")
    for col in ["独立项[KR,JP,US,CN,EP,IN]", "效果 摘要[US,EP,PCT,JP,KR,CN,TW]"]:
        if col in keep.columns:
            empty = int(keep[col].isna().sum())
            print(f"  {col[:22]:24s} 空值 {empty} 筆 ({empty/max(len(keep),1)*100:.1f}%)")
    print("  國別:", dict(Counter(keep["国家代码"].dropna()).most_common(6)))
    fresh_keys = [patent_key(r) for _, r in fresh.iterrows()]
    print("  新檔內部重複（主鍵 COALESCE 三號碼）:",
          len(fresh_keys) - len(set(fresh_keys)), "筆")
    print("  無任何號碼可當主鍵:", sum(1 for k in fresh_keys if not k), "筆")

    # ---- 合併後各線總量（對照補撈目標）
    cur = Counter(own[own["歸屬線"] != "領域外"]["歸屬線"]) if "歸屬線" in own.columns else Counter()
    after = cur + Counter(keep["歸屬線"])
    print("\n[合併後各線總量]")
    for line, n in after.most_common():
        if line == "領域外":
            continue
        delta = Counter(keep["歸屬線"]).get(line, 0)
        print(f"  {line:11s} {n:5d} 筆" + (f"  (+{delta})" if delta else ""))

    if a.dry_run:
        print("\n[乾跑] 未寫入任何檔案。確認無誤後拿掉 --dry-run 並帶 --batch 執行合併。")
        return

    if not a.batch:
        sys.exit("ERROR: 正式合併需要 --batch（批次標記，例如 批4）")

    # ---- 寫回（先備份到系統暫存，不落在 data 目錄：語料夾只該有三個檔）
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp = pathlib.Path(tempfile.gettempdir()) / "signal-corpus-bak"
    tmp.mkdir(parents=True, exist_ok=True)
    for p in (CORPUS, DISCARD):
        bak = tmp / f"{p.stem}.bak_{stamp}.xlsx"
        shutil.copy2(p, bak)
    print(f"備份（系統暫存，可救援）: {tmp}")

    keep2 = keep.drop(columns=["歸屬線", "歸屬依據"])
    dl_new = pd.concat([dl, keep2], ignore_index=True)

    own_add = pd.DataFrame({
        "授权公告号": fresh["授权公告号"], "未审查的公开号": fresh["未审查的公开号"],
        "申请号": fresh["申请号"], "歸屬線": fresh["歸屬線"], "歸屬依據": fresh["歸屬依據"],
        "標題(前60字)": fresh["标题"].astype(str).str[:60], "批次": a.batch,
    })
    own_new = pd.concat([own, own_add], ignore_index=True)

    disc = pd.read_excel(DISCARD, sheet_name=0)
    disc_new = pd.concat([disc, drop.drop(columns=["歸屬線", "歸屬依據"])], ignore_index=True)

    rec = pd.read_excel(CORPUS, sheet_name="處理紀錄")
    rec_add = pd.DataFrame({
        rec.columns[0]: [f"{a.batch}（{datetime.now():%Y-%m-%d}）"],
        rec.columns[1]: [f"來源 {pathlib.Path(a.src).name}；新檔 {len(new)} 筆；"
                         f"重複跳過 {int(dup_mask.sum())}；新保留 {len(keep)}；新廢棄 {len(drop)}；"
                         f"各線 {dict(Counter(keep['歸屬線']).most_common())}。{a.note}"],
    })
    rec_new = pd.concat([rec, rec_add], ignore_index=True)

    # ---- 檢索條件工作表：metadata 規範要求「關鍵字字串+勾選的 CPC+國別年限+日期+筆數」
    # 上一批沒存，關鍵字才得從語料反推 —— 這裡建表存下來，一批一列。
    cpc_hit = Counter(str(c).strip()[:4] for c in new["Curr. CPC(Main)"].dropna() if str(c).strip())
    cond_add = pd.DataFrame([{
        "批次": a.batch,
        "日期": datetime.now().strftime("%Y-%m-%d"),
        "目標線": a.note or "（未填）",
        "關鍵字檢索式": "（待補：請貼上 WIPS 實際下的布林式，一線一列或合併記錄）",
        "勾選CPC": "（待補：WIPS 介面實際勾選的 CPC）",
        "檔案實測CPC分布(前8)": ", ".join(f"{k} {v}" for k, v in cpc_hit.most_common(8)),
        "國別": ", ".join(f"{k} {v}" for k, v in Counter(new["国家代码"].dropna()).most_common()),
        "年限": "（待補）",
        "來源檔": pathlib.Path(a.src).name,
        "匯出筆數": len(new),
        "重複跳過": int(dup_mask.sum()),
        "新保留": len(keep),
        "新廢棄": len(drop),
        "各線新增": str(dict(Counter(keep["歸屬線"]).most_common())),
    }])
    try:
        cond = pd.read_excel(CORPUS, sheet_name="檢索條件")
        cond_new = pd.concat([cond, cond_add], ignore_index=True)
    except ValueError:      # 工作表還不存在 → 建新表
        cond_new = cond_add

    with pd.ExcelWriter(CORPUS, engine="openpyxl") as w:
        dl_new.to_excel(w, sheet_name="download", index=False)
        rec_new.to_excel(w, sheet_name="處理紀錄", index=False)
        own_new.to_excel(w, sheet_name="歸屬清單", index=False)
        cond_new.to_excel(w, sheet_name="檢索條件", index=False)
    with pd.ExcelWriter(DISCARD, engine="openpyxl") as w:
        disc_new.to_excel(w, sheet_name="download", index=False)

    print(f"\n已合併: download {len(dl)} → {len(dl_new)}；"
          f"歸屬清單 {len(own)} → {len(own_new)}；廢棄 {len(disc)} → {len(disc_new)}")

    # 原始檔整理完即刪：每一筆都已在歸屬清單留下履歷（三號碼＋歸屬線＋依據＋批次），
    # 留著只會變成第二份事實來源。刪前先驗證履歷完整，缺一筆就不刪。
    src_keys = {patent_key(r) for _, r in new.iterrows()} - {""}
    own_keys = set()
    for c in ID_COLS:
        own_keys |= {norm_id(v) for v in own_new[c].dropna()}
    own_keys.discard("")
    missing = src_keys - own_keys
    if a.keep_src:
        print("保留原始檔（--keep-src）")
    elif missing:
        print(f"⚠ 原始檔保留：有 {len(missing)} 筆未在歸屬清單留下履歷，請先查明")
    else:
        pathlib.Path(a.src).unlink()
        print(f"原始檔已刪除（{len(src_keys)} 筆履歷全數在歸屬清單）: {pathlib.Path(a.src).name}")


if __name__ == "__main__":
    main()
