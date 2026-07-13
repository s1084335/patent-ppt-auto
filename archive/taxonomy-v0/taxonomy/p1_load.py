"""P1 資料載入與欄位抽取（PoC spec §P1）

輸入  data/raw/*.xlsx（正式語料＝力山自有 525 筆；訊號語料不在此，也不該放進 data/raw）
處理  patent_no = COALESCE(授权公告号, 未审查的公开号, 申请号)
      異常標記：独立项数量<=0、切分數≠数量 → flag，**不剔除**
輸出  out/patents.jsonl（patent_no, means_text, effect_text, counts, flags）
驗收  筆數 = 來源筆數；flag 清單與既知異常吻合

用法:
  uv run --no-project --with pandas --with openpyxl --python 3.12 python p1_load.py
冪等：重跑覆蓋 out/patents.jsonl。
"""
import argparse
import json
import pathlib
import re
import sys
from collections import Counter

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).parent
DEF_RAW = HERE / "../../data/raw"
DEF_OUT = HERE / "out"

C_MEANS = "独立项[KR,JP,US,CN,EP,IN]"
C_EFFECT = "效果 摘要[US,EP,PCT,JP,KR,CN,TW]"
C_INDEP_N = "独立项数量[KR,JP,US,CN,EP,IN]"
C_CLAIM_N = "权利要求的项数"
ID_COLS = ["授权公告号", "未审查的公开号", "申请号"]

CLAIM_NO = re.compile(r"^\s*\d{1,3}\s*\.\s*")


def norm(v) -> str:
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "") else s


def patent_key(row) -> str:
    """COALESCE 三號碼。未授權的公開案沒有公告號（空字串非 NaN），只用公告號會全撞在一起。"""
    for c in ID_COLS:
        k = norm(row.get(c))
        if k:
            return k
    return ""


def split_claims(means: str) -> list[str]:
    """独立项 以「|」分隔多個獨立項；此處只切、不解析（解析在 P2）。"""
    if not means:
        return []
    return [CLAIM_NO.sub("", p).strip() for p in means.split("|") if p.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(DEF_RAW))
    ap.add_argument("--out", default=str(DEF_OUT))
    a = ap.parse_args()

    raw = pathlib.Path(a.raw)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    files = sorted(raw.glob("*.xlsx"))
    if not files:
        sys.exit(f"ERROR: {raw} 沒有 xlsx")
    print("來源檔:", ", ".join(f.name for f in files))

    rows, flags_all = [], Counter()
    src_total = 0
    for f in files:
        df = pd.read_excel(f, sheet_name=0)
        src_total += len(df)
        for idx, r in df.iterrows():
            pno = patent_key(r)
            means = norm(r.get(C_MEANS))
            effect = norm(r.get(C_EFFECT))
            claims = split_claims(means)

            try:
                n_indep = int(r.get(C_INDEP_N)) if pd.notna(r.get(C_INDEP_N)) else 0
            except (TypeError, ValueError):
                n_indep = 0
            try:
                n_claim = int(r.get(C_CLAIM_N)) if pd.notna(r.get(C_CLAIM_N)) else 0
            except (TypeError, ValueError):
                n_claim = 0

            flags = []
            if not pno:
                flags.append("no_patent_no")
            if not means:
                flags.append("empty_means")
            if not effect:
                flags.append("empty_effect")
            if n_indep <= 0:
                flags.append("indep_count_le0")
            if means and n_indep > 0 and len(claims) != n_indep:
                flags.append("split_count_mismatch")
            flags_all.update(flags)

            rows.append({
                "patent_no": pno,
                "src_file": f.name,
                "src_idx": int(idx),
                "means_text": means,
                "effect_text": effect,
                "counts": {"indep_declared": n_indep, "indep_split": len(claims),
                           "claims_total": n_claim},
                "flags": flags,
            })

    p = out / "patents.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    keys = [r["patent_no"] for r in rows if r["patent_no"]]
    print(f"\n寫出 {len(rows)} 筆 → {p}")
    print(f"[驗收] 來源筆數 {src_total} == 輸出筆數 {len(rows)}: "
          f"{'✅' if src_total == len(rows) else '❌'}")
    print(f"[驗收] patent_no 唯一: {len(set(keys))}/{len(keys)} "
          f"{'✅' if len(set(keys)) == len(keys) else '❌ 有重複'}")
    print("\n[flag 分布]")
    for k, v in flags_all.most_common():
        print(f"  {k:22s} {v:4d} 筆")
    clean = sum(1 for r in rows if not r["flags"])
    print(f"  {'(無 flag)':22s} {clean:4d} 筆")

    if flags_all.get("split_count_mismatch"):
        print("\n[split_count_mismatch 明細（前 10）]")
        for r in [x for x in rows if "split_count_mismatch" in x["flags"]][:10]:
            c = r["counts"]
            print(f"  idx {r['src_idx']:3d} {r['patent_no']:>14s} "
                  f"宣告 {c['indep_declared']} vs 切出 {c['indep_split']}")


if __name__ == "__main__":
    main()
