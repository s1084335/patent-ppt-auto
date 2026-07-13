"""站2-③ 抽訓練文本：共現語料資料.xlsx → AutoPhrase 輸入 txt（一件專利一行）

用法:
  uv run --no-project --with pandas --with openpyxl --python 3.12 python s2_extract_corpus.py \
      [--src "../../data/signal corpus/共現語料資料.xlsx"] [--out autophrase/input]

輸出:
  corpus_means.txt   独立项全文（| 分隔換空白、去 claim 編號）
  corpus_effect.txt  效果 摘要（剝模板框）
  extract_report.json
冪等：重跑覆蓋同名輸出。
"""
import argparse
import json
import pathlib
import re
import sys

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).parent
DEF_SRC = HERE / "../../data/signal corpus/共現語料資料.xlsx"

C_MEANS = "独立项[KR,JP,US,CN,EP,IN]"
C_EFFECT = "效果 摘要[US,EP,PCT,JP,KR,CN,TW]"

CLAIM_NO = re.compile(r"(?:^|(?<=\|))\s*\d{1,3}\.\s*")      # 各獨立項開頭編號
EFFECT_FRAME = re.compile(r"^\s*the\s+invention\s+(?:thereby\s+)?", re.I)  # 模板框
WS = re.compile(r"\s+")


def clean_means(v: str) -> str:
    t = CLAIM_NO.sub(" ", str(v))
    t = t.replace("|", " ")
    return WS.sub(" ", t).strip()


def clean_effect(v: str) -> str:
    t = EFFECT_FRAME.sub("", str(v))
    return WS.sub(" ", t).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEF_SRC))
    ap.add_argument("--sheet", default="download")
    ap.add_argument("--out", default=str(HERE / "autophrase/input"))
    a = ap.parse_args()

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    df = pd.read_excel(a.src, sheet_name=a.sheet)

    stats = {"src": str(a.src), "rows": len(df)}
    for col, fname, cleaner in [(C_MEANS, "corpus_means.txt", clean_means),
                                (C_EFFECT, "corpus_effect.txt", clean_effect)]:
        lines = [cleaner(v) for v in df[col].dropna() if str(v).strip()]
        lines = [x for x in lines if len(x) > 20]
        (out / fname).write_text("\n".join(lines), encoding="utf-8")
        stats[fname] = {"docs": len(lines),
                        "avg_len": int(sum(map(len, lines)) / max(len(lines), 1))}
        print(f"{fname}: {len(lines)} docs, avg {stats[fname]['avg_len']} chars")

    (out / "extract_report.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
