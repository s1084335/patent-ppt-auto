"""站2-⑤ AutoPhrase 產出 QA：分數分布 + 各分數帶抽樣 + 體檢指標

用法:
  uv run --no-project --python 3.12 python s2_qa_report.py --model autophrase/output/means/means
輸出: <model>/qa_report.txt（分布、樣本、套語率、長度分布）
"""
import argparse
import pathlib
import random
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STOPWORDS = {"said", "wherein", "thereof", "therein", "whereby", "comprising", "comprises",
             "the", "of", "and", "is", "are", "with", "for", "least", "one", "plurality",
             "configured", "adapted", "disposed", "provided", "according", "claim"}
BANDS = [(0.9, 1.01), (0.8, 0.9), (0.7, 0.8), (0.6, 0.7), (0.5, 0.6), (0.4, 0.5), (0.0, 0.4)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--sample", type=int, default=20)
    a = ap.parse_args()
    random.seed(42)

    path = pathlib.Path(a.model) / "AutoPhrase.txt"
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            s, p = line.split("\t", 1)
            items.append((float(s), p.strip()))
        except ValueError:
            continue

    L = [f"model: {a.model}", f"總片語數: {len(items)}", ""]
    L.append("[分數分布 / 套語率 / 平均詞數]")
    for lo, hi in BANDS:
        band = [(s, p) for s, p in items if lo <= s < hi]
        if not band:
            L.append(f"  {lo:.1f}~{hi if hi<=1 else 1.0:<4} : 0 條")
            continue
        stop_rate = sum(1 for _, p in band if any(w in STOPWORDS for w in p.split())) / len(band)
        avg_words = sum(len(p.split()) for _, p in band) / len(band)
        L.append(f"  {lo:.1f}~{min(hi,1.0):<4} : {len(band):6d} 條 | 含套語 {stop_rate*100:4.1f}% | 平均 {avg_words:.1f} 詞")

    L.append("\n[各分數帶抽樣]")
    for lo, hi in BANDS:
        band = [p for s, p in items if lo <= s < hi]
        if not band:
            continue
        L.append(f"\n--- {lo:.1f}~{min(hi,1.0)} ---")
        for p in random.sample(band, min(a.sample, len(band))):
            L.append(f"    {p}")

    out = pathlib.Path(a.model) / "qa_report.txt"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"written: {out}")
    print("\n".join(L[:14]))


if __name__ == "__main__":
    main()
