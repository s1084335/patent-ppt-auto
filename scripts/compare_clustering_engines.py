"""掃 lambda 區間，用四項判準找出 DP-Means 可採用的區間（tasks 3.1）。

## 判準（2026-08-09 使用者定案，事前定死，量完不改）

lambda 決定主題數，所以調參的方向是「**掃 lambda、用指標挑區間**」，
不是「找一個公式剛好給出想要的群數」——後者是倒果為因，換批資料就不成立。

一個 lambda 要可採用，四項全過：

| 判準 | 門檻 | 為什麼 |
|---|---|---|
| ① 中位群大小 | ≥ 3 件 | ⚠ **不預設群數**——指定 k 是舊引擎的思維，DP-Means 的群數由資料決定。這條直接問「典型主題夠不夠厚」：少於 3 件講不出趨勢（談不了年份、申請人分布），隨資料量自動調整 |
| ② 單點群**文件佔比** | ≤ 15% | ⚠ 2026-08-09 修正：原本用「單點群佔群數比例」，那會讓小資料誤判——35 件裡有 1 個單件主題只佔 3% 文件，是可容許的；碎成 20 個單點群才是問題。改用文件佔比後隨資料量自動調整 |
| ③ 最近兩群中心距離 | ≥ 0.30 | 低於此表示兩個「主題」在講同一件事，簡報會出現兩頁重複 |
| ④ 穩定度 | 換順序 5 次，群數變動 ≤ 1 | DP-Means 順序敏感；變動大代表這個 lambda 落在分界上 |

四項是**及格制**，任一不過即淘汰。合格區間內再用**主題一致性（c_v coherence）**
與**主題多樣性（diversity）**排序挑最佳——這兩個是既有指標，只需要每群的關鍵詞，
不綁 BERTopic（見 clustering/keywords.py）。

⚠ 用指標挑，不用「取區間中位數」——後者是沒有依據的折衷。
⚠ 刻意**不用** silhouette：高維 cosine 下數值普遍偏低又難解讀，而它想回答的事
已被 ②③ 拆成兩個看得懂的量。

## 用法

    PYTHONPATH=. uv run python scripts/compare_clustering_engines.py \
        --workspace 3 --source-field wips_independent_claims

⚠ **唯讀**：只讀 corpus，不寫任何資料、不建 run、不動 artifact。
"""
from __future__ import annotations

import argparse
import json
import random
import time
from typing import Any

import psycopg

from backend.app.clustering import dpmeans, keywords
from backend.app.db.connection import get_connection_kwargs

#: 每群取幾個關鍵詞算 coherence／diversity（與既有 finalize 同口徑）。
TOP_TERMS_PER_TOPIC = 10

# 判準門檻（事前定死）
MIN_MEDIAN_TOPIC_SIZE = 3
MAX_SINGLETON_DOC_SHARE = 0.15
MIN_BETWEEN_DISTANCE = 0.30
MAX_STABILITY_SPREAD = 1
STABILITY_ROUNDS = 5


def _load_corpus(workspace_id: int, source_field: str):
    """讀該 workspace 通道的 corpus 並降維，回傳 (向量, patent_ids, 原文)。"""
    from backend.app.clustering.model import fit_incremental_pca
    from backend.app.clustering.runner import PCA_COMPONENTS, load_clustering_corpus

    with psycopg.connect(**get_connection_kwargs()) as conn:
        corpus = load_clustering_corpus(
            conn, workspace_id=workspace_id, source_field=source_field)
    reduced, _ = fit_incremental_pca(
        corpus.matrix, n_components=PCA_COMPONENTS,
        batch_size=min(128, len(corpus.documents)))
    return reduced.vectors, corpus.patent_ids, corpus.documents


def _centroid(points: list[list[float]]) -> list[float]:
    dim = len(points[0])
    return dpmeans.l2_normalize(
        [sum(p[d] for p in points) / len(points) for d in range(dim)])


def _measure(labels: list[int], points: list[list[float]],
             documents: list[str]) -> dict[str, Any]:
    """四項判準需要的量，外加主題一致性與多樣性（供合格區間內排序）。"""
    groups: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        groups.setdefault(label, []).append(index)
    sizes = sorted((len(m) for m in groups.values()), reverse=True)
    singletons = sum(1 for size in sizes if size == 1)

    centers = [_centroid([points[i] for i in members]) for members in groups.values()]
    between = [dpmeans.cosine_distance(centers[a], centers[b])
               for a in range(len(centers)) for b in range(a + 1, len(centers))]

    return {
        "topic_count": len(sizes),
        "sizes": sizes,
        "median_size": sizes[len(sizes) // 2] if sizes else 0,
        # ⚠ 文件佔比而非群數佔比——小資料裡一兩個單件主題是可容許的。
        "singleton_doc_share": round(singletons / len(labels), 4) if labels else 0.0,
        "singleton_count": singletons,
        "between_min": round(min(between), 4) if between else None,
        **_topic_quality(labels, documents),
    }


def _topic_quality(labels: list[int], documents: list[str]) -> dict[str, Any]:
    """主題一致性（c_v）與多樣性——既有指標，只需要每群關鍵詞。

    ⚠ 這兩個指標**不綁 BERTopic**：coherence 由 gensim 依文件本身計算，
    diversity 只看關鍵詞重疊度。DP-Means 自行抽出 top terms 即可沿用。
    """
    from backend.app.clustering.model import topic_diversity

    top_terms = keywords.extract_top_terms(
        documents, labels=labels, limit=TOP_TERMS_PER_TOPIC)
    if not top_terms:
        return {"coherence": None, "diversity": None}
    diversity = round(topic_diversity(top_terms), 4)
    try:
        from backend.app.clustering.model import topic_cv_coherence_per_topic

        per_topic = topic_cv_coherence_per_topic(
            documents, topics=labels, top_terms=top_terms)
        coherence = (round(sum(per_topic.values()) / len(per_topic), 4)
                     if per_topic else None)
    except Exception:  # noqa: BLE001 - 指標算不出來不得中斷整輪掃描
        coherence = None
    return {"coherence": coherence, "diversity": diversity}


def _stability(points: list[list[float]], *, lambda_: float) -> dict[str, Any]:
    """換餵入順序重跑，看群數是否穩定。

    ⚠ DP-Means **是**順序敏感的演算法，這裡不假裝它不是——量的是「群數會不會
    因為順序而變動」。正式流程一律固定順序，但資料若脆弱到換個順序就多出兩群，
    那個 lambda 換一批資料也會忽多忽少。
    """
    rng = random.Random(20260809)
    counts = []
    for _ in range(STABILITY_ROUNDS):
        shuffled = list(points)
        rng.shuffle(shuffled)
        counts.append(len(dpmeans.fit(shuffled, lambda_=lambda_).centers))
    return {"topic_counts": counts, "spread": max(counts) - min(counts)}


def _verdict(row: dict[str, Any]) -> dict[str, Any]:
    """逐項判定並回傳未過的項目名稱——不做加總分數。

    ⚠ 加總會讓「主題全是單點但分離度很好」這種組合看起來還行。四項是**及格制**，
    任一不過就是不可採用。
    """
    failed = []
    if row["median_size"] < MIN_MEDIAN_TOPIC_SIZE:
        failed.append("median_size")
    if row["singleton_doc_share"] > MAX_SINGLETON_DOC_SHARE:
        failed.append("singleton_doc_share")
    if row["between_min"] is not None and row["between_min"] < MIN_BETWEEN_DISTANCE:
        failed.append("between_min")
    if row["stability"]["spread"] > MAX_STABILITY_SPREAD:
        failed.append("stability")
    return {"passed": not failed, "failed": failed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=int, required=True)
    parser.add_argument("--source-field", required=True)
    parser.add_argument("--sweep", default="0.5:1.10:0.05",
                        help="lambda 掃描區間 start:stop:step")
    args = parser.parse_args()

    vectors, patent_ids, documents = _load_corpus(args.workspace, args.source_field)
    points = [dpmeans.l2_normalize(v) for v in vectors]

    start, stop, step = (float(x) for x in args.sweep.split(":"))
    rows: list[dict[str, Any]] = []
    value = start
    while value <= stop + 1e-9:
        started = time.perf_counter()
        state = dpmeans.fit(vectors, lambda_=value)
        row = {"lambda": round(value, 4)}
        row.update(_measure(state.labels, points, documents))
        row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        row["stability"] = _stability(points, lambda_=value)
        row["verdict"] = _verdict(row)
        rows.append(row)
        value += step

    passed_rows = [r for r in rows if r["verdict"]["passed"]]
    passed = [r["lambda"] for r in passed_rows]
    # 合格區間內用主題一致性排序挑最佳；一致性算不出來時退回多樣性。
    best = max(passed_rows,
               key=lambda r: (r["coherence"] if r["coherence"] is not None else -1,
                              r["diversity"] if r["diversity"] is not None else -1),
               default=None)
    print(json.dumps({
        "workspace_id": args.workspace,
        "source_field": args.source_field,
        "document_count": len(patent_ids),
        "current_formula_lambda": round(dpmeans.derive_lambda(vectors).value, 4),
        "passed_lambdas": passed,
        "recommended_lambda": best["lambda"] if best else None,
        "recommended_reason": {"coherence": best["coherence"],
                               "diversity": best["diversity"],
                               "topic_count": best["topic_count"]} if best else None,
        "sweep": rows,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
