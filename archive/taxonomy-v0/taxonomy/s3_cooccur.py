"""站3 分維度共現統計：過 cutoff 的 AutoPhrase 片語 → 文件層級 P(w_i|w_j) 落盤

用途（design §2.1 路線 B）：共現 CE 的 λ 項需要獨立於骨架的發現訊號。
共現以「一件專利算一次」的文件層級 df 計算（不計詞頻），避免長 claim 灌票。

用法:
  uv run --no-project --python 3.12 python s3_cooccur.py \
      --model autophrase/output/means/means --corpus autophrase/input/corpus_means.txt \
      --cutoff 0.5 --out cooccur/means
  uv run --no-project --python 3.12 python s3_cooccur.py \
      --model autophrase/output/effect/effect --corpus autophrase/input/corpus_effect.txt \
      --cutoff 0.6 --out cooccur/effect

輸出（<out>/）:
  phrases.txt   過關片語（score \t phrase \t df），df=出現該片語的專利件數
  cooccur.tsv   w_j \t w_i \t co \t df_j \t p   其中 p = P(w_i|w_j) = co / df_j
  stats.json    片語數、文件數、非零對數、稀疏度等
  qa_report.txt 體檢：df 分布、鄰居數分布、高 P 配對抽樣、孤立片語率
冪等：重跑覆蓋同名輸出。
"""
import argparse
import json
import pathlib
import random
import re
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NON_WORD = re.compile(r"[^a-z0-9]+")
MAX_N = 8  # n-gram 掃描上限，超長片語（AutoPhrase 少數雜訊）直接不計

# 分數 cutoff 擋不掉的 claim 套語：AutoPhrase 照樣給它們 0.5+（統計上確實是「常見片語」），
# 但它們在每篇 claim 都出現，會直接主導共現。design §5「統計體檢」的套語/頻率兩道就是擋這個。
FUNCTION_WORDS = {
    "a", "an", "the", "it", "is", "are", "be", "of", "in", "on", "at", "to", "for", "with",
    "and", "or", "by", "as", "that", "which", "said", "wherein", "thereof", "therein",
    "whereby", "comprising", "comprises", "including", "configured", "adapted", "disposed",
    "provided", "according", "claim", "claims", "least", "one", "plurality", "respectively",
    "above", "mentioned", "such", "each", "same", "other", "another", "further", "also",
}
BOILERPLATE = [
    re.compile(r"^\d+(st|nd|rd|th)\b"),        # 1st / 2nd above mentioned…
    re.compile(r"\bclaim\s*\d*\b"),            # claim1、according to claim
    re.compile(r"\babove\s+mentioned\b"),
    re.compile(r"^\d+[a-z]?$"),                # 元件編號：14c、18c、2
    re.compile(r"^[a-z]\d+$"),                 # k2、b3
]


def is_boilerplate(phrase: str) -> bool:
    toks = phrase.split()
    if all(t in FUNCTION_WORDS for t in toks):          # 純功能詞組成
        return True
    if len(toks) == 1 and len(phrase) <= 2:             # 單字母/兩字元殘渣
        return True
    return any(rx.search(phrase) for rx in BOILERPLATE)


def is_nested(a: str, b: str) -> bool:
    """a 與 b 是否為巢狀片語（其一是另一的連續子序列）。

    子片語必然跟著母片語出現（P(tile cutting | ceramic tile cutting)=1.0），
    這是恆真關聯不是技術訊號，算共現時要排除。
    """
    ta, tb = a.split(), b.split()
    if len(ta) > len(tb):
        ta, tb = tb, ta
    n = len(ta)
    return any(tb[i:i + n] == ta for i in range(len(tb) - n + 1))


def norm_tokens(text: str) -> list[str]:
    """與 AutoPhrase 片語同一套正規化：小寫、非字母數字→空白。"""
    return NON_WORD.sub(" ", text.lower()).split()


def load_phrases(model: pathlib.Path, cutoff: float) -> dict[str, float]:
    phrases = {}
    for line in (model / "AutoPhrase.txt").read_text(encoding="utf-8").splitlines():
        try:
            score_s, phrase = line.split("\t", 1)
            score = float(score_s)
        except ValueError:
            continue
        if score < cutoff:
            continue
        key = " ".join(norm_tokens(phrase))
        if not key or len(key.split()) > MAX_N or is_boilerplate(key):
            continue
        # 同一正規化形態取最高分（AutoPhrase 可能給大小寫變體各一條）
        if score > phrases.get(key, -1.0):
            phrases[key] = score
    return phrases


def phrases_in_doc(tokens: list[str], vocab: dict[str, float], max_n: int) -> set[str]:
    """回傳這篇文件出現過的片語集合（文件層級，不計次數）。"""
    found = set()
    n_tok = len(tokens)
    for i in range(n_tok):
        for n in range(1, min(max_n, n_tok - i) + 1):
            gram = " ".join(tokens[i:i + n])
            if gram in vocab:
                found.add(gram)
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="AutoPhrase 模型目錄（含 AutoPhrase.txt）")
    ap.add_argument("--corpus", required=True, help="訓練文本（一件專利一行）")
    ap.add_argument("--cutoff", type=float, required=True, help="片語分數門檻（means 0.5 / effect 0.6）")
    ap.add_argument("--min-df", type=int, default=2, help="片語至少出現在幾件專利才納入")
    ap.add_argument("--max-df-ratio", type=float, default=0.25,
                    help="df 佔語料比例超過此值即剔除（到處都有的詞沒有區辨力）")
    ap.add_argument("--min-co", type=int, default=2, help="配對至少共現幾件才落盤")
    ap.add_argument("--min-p", type=float, default=0.05, help="P(w_i|w_j) 低於此值不落盤")
    ap.add_argument("--topk", type=int, default=75, help="每個 w_j 最多保留幾個鄰居")
    ap.add_argument("--out", required=True)
    ap.add_argument("--sample", type=int, default=25)
    a = ap.parse_args()
    random.seed(42)

    model = pathlib.Path(a.model)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    vocab = load_phrases(model, a.cutoff)
    if not vocab:
        sys.exit(f"ERROR: {model}/AutoPhrase.txt 在 cutoff {a.cutoff} 之上沒有任何片語")
    max_n = max(len(p.split()) for p in vocab)
    print(f"片語（cutoff {a.cutoff}）: {len(vocab)} 條，最長 {max_n} 詞")

    docs = [ln for ln in pathlib.Path(a.corpus).read_text(encoding="utf-8").splitlines() if ln.strip()]
    print(f"語料: {len(docs)} 件")

    # 一趟掃完：每件專利的片語集合
    doc_sets: list[set[str]] = []
    df = Counter()
    for line in docs:
        found = phrases_in_doc(norm_tokens(line), vocab, max_n)
        doc_sets.append(found)
        df.update(found)

    max_df = int(a.max_df_ratio * len(docs))
    kept = {p for p, c in df.items() if a.min_df <= c <= max_df}
    too_common = sorted((p for p, c in df.items() if c > max_df), key=lambda p: -df[p])
    print(f"保留片語: {len(kept)} 條（過 cutoff 且非套語 {len(vocab)}；"
          f"df<{a.min_df} 剔 {sum(1 for p, c in df.items() if c < a.min_df)}、"
          f"df>{max_df}（{a.max_df_ratio:.0%} 語料）剔 {len(too_common)}）")

    # 文件層級共現計數（無方向，機率才有方向）
    co: dict[str, Counter] = defaultdict(Counter)
    for found in doc_sets:
        present = sorted(found & kept)
        for idx, wj in enumerate(present):
            for wi in present[idx + 1:]:
                co[wj][wi] += 1
                co[wi][wj] += 1

    rows = []
    neighbor_counts = []
    nested_skipped = 0
    truncated_by_topk = 0
    for wj in sorted(kept):
        df_j = df[wj]
        cand = []
        for wi, c in co[wj].items():
            if c < a.min_co or c / df_j < a.min_p:
                continue
            if is_nested(wi, wj):       # 巢狀片語的恆真共現，不是技術訊號
                nested_skipped += 1
                continue
            cand.append((wi, c, c / df_j))
        cand.sort(key=lambda t: (-t[2], -t[1], t[0]))
        if len(cand) > a.topk:
            truncated_by_topk += len(cand) - a.topk
            cand = cand[:a.topk]
        neighbor_counts.append(len(cand))
        for wi, c, p in cand:
            rows.append((wj, wi, c, df_j, p))

    (out / "phrases.txt").write_text(
        "\n".join(f"{vocab[p]:.6f}\t{p}\t{df[p]}" for p in sorted(kept, key=lambda x: -df[x])),
        encoding="utf-8")
    with (out / "cooccur.tsv").open("w", encoding="utf-8") as fh:
        fh.write("w_j\tw_i\tco\tdf_j\tp\n")
        for wj, wi, c, df_j, p in rows:
            fh.write(f"{wj}\t{wi}\t{c}\t{df_j}\t{p:.6f}\n")

    isolated = sum(1 for n in neighbor_counts if n == 0)
    possible_pairs = len(kept) * (len(kept) - 1)
    stats = {
        "model": str(model), "corpus": str(a.corpus), "cutoff": a.cutoff,
        "params": {"min_df": a.min_df, "max_df_ratio": a.max_df_ratio, "min_co": a.min_co,
                   "min_p": a.min_p, "topk": a.topk},
        "docs": len(docs),
        "phrases_over_cutoff_nonboiler": len(vocab),
        "phrases_kept": len(kept),
        "dropped_too_common": len(too_common),
        "pairs_written": len(rows),
        "pairs_dropped_nested": nested_skipped,
        "pairs_dropped_by_topk": truncated_by_topk,
        "density_vs_all_pairs": round(len(rows) / possible_pairs, 6) if possible_pairs else 0.0,
        "avg_neighbors": round(sum(neighbor_counts) / max(len(kept), 1), 2),
        "isolated_phrases": isolated,
        "isolated_rate": round(isolated / max(len(kept), 1), 4),
    }
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")

    # QA
    L = [f"model: {model} | cutoff: {a.cutoff} | docs: {len(docs)}",
         f"片語: 過 cutoff 且非套語 {len(vocab)} → df {a.min_df}~{max_df} 保留 {len(kept)}"
         f"（df 過高剔除 {len(too_common)} 條）",
         f"配對: {len(rows)} 條（min_co={a.min_co}, min_p={a.min_p}, topk={a.topk}；"
         f"巢狀對剔除 {nested_skipped}、topk 截掉 {truncated_by_topk}）",
         f"平均鄰居數: {stats['avg_neighbors']} | 孤立片語: {isolated} ({stats['isolated_rate']*100:.1f}%)",
         ""]
    if too_common:
        L.append("[df 過高被剔除的片語（前 20，確認沒誤殺技術詞）]")
        for p in too_common[:20]:
            L.append(f"    {p}  (df={df[p]}, {df[p]/len(docs)*100:.0f}% 語料)")
        L.append("")
    L.append("[df 分布]")
    for lo, hi in [(2, 3), (3, 5), (5, 10), (10, 30), (30, 10 ** 9)]:
        n = sum(1 for p in kept if lo <= df[p] < hi)
        label = f"{lo}~{hi-1}" if hi < 10 ** 9 else f"{lo}+"
        L.append(f"  df {label:>6} : {n:6d} 條")
    L.append("\n[鄰居數分布]")
    for lo, hi in [(0, 1), (1, 5), (5, 20), (20, 50), (50, 10 ** 9)]:
        n = sum(1 for c in neighbor_counts if lo <= c < hi)
        label = "0（孤立）" if hi == 1 else (f"{lo}~{hi-1}" if hi < 10 ** 9 else f"{lo}+")
        L.append(f"  鄰居 {label:>8} : {n:6d} 條")

    L.append("\n[高 P 配對抽樣（P(w_i|w_j) 由高到低取樣）]")
    strong = [r for r in rows if r[3] >= 5]  # 只看 df_j>=5 的，避免 2/2=1.0 這種假強關聯
    strong.sort(key=lambda t: -t[4])
    for wj, wi, c, df_j, p in strong[:a.sample]:
        L.append(f"    P({wi} | {wj}) = {p:.2f}   (co={c}, df_j={df_j})")

    L.append("\n[隨機配對抽樣]")
    for wj, wi, c, df_j, p in random.sample(rows, min(a.sample, len(rows))):
        L.append(f"    P({wi} | {wj}) = {p:.2f}   (co={c}, df_j={df_j})")

    (out / "qa_report.txt").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:8]))
    print(f"written: {out}/phrases.txt, cooccur.tsv, stats.json, qa_report.txt")


if __name__ == "__main__":
    main()
