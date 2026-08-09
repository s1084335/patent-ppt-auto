"""比較 MiniBatchKMeans 基準與 Cosine Online DP-Means（tasks 3.1）。

## 為什麼不只看一個分數

分群沒有單一「正確答案」的分數。⚠ 只報 silhouette 之類的單一指標，等於拿一個
恰好對自己有利的角度宣稱成功——本檔一律同時報四項，且**不做加總排名**：

| 指標 | 回答的問題 | 為什麼重要 |
|---|---|---|
| 群數 | 分成幾群 | DP-Means 的群數由資料決定，這正是換引擎的目的 |
| singleton 比例 | 幾群只有一件 | ⚠ lambda 太小會碎成一堆單點群，這是最主要的失敗模式 |
| 穩定度 | 換餵入順序後群數是否一致 | DP-Means 順序敏感，要量出界線而不是假裝沒有 |
| 執行時間 | 跑多久 | 增量流程在 worker 內，時間直接變成使用者等待 |

## 用法

    uv run python scripts/compare_clustering_engines.py --workspace 3 \
        --source-field wips_independent_claims

⚠ **唯讀**：只讀 corpus 與既有 run，不寫任何資料、不建 run、不動 artifact。
"""
from __future__ import annotations

import argparse
import json
import random
import time
from typing import Any

import psycopg

from backend.app.clustering import dpmeans
from backend.app.db.connection import get_connection_kwargs


def _load_vectors(workspace_id: int, source_field: str) -> tuple[list[list[float]], list[int]]:
    """讀該 workspace 通道的 corpus 並降維，回傳 (向量, patent_ids)。"""
    from backend.app.clustering.model import fit_incremental_pca
    from backend.app.clustering.runner import PCA_COMPONENTS, load_clustering_corpus

    with psycopg.connect(**get_connection_kwargs()) as conn:
        corpus = load_clustering_corpus(
            conn, workspace_id=workspace_id, source_field=source_field)
    reduced, _ = fit_incremental_pca(
        corpus.matrix, n_components=PCA_COMPONENTS,
        batch_size=min(128, len(corpus.documents)))
    return reduced.vectors, corpus.patent_ids


def _profile(labels: list[int]) -> dict[str, Any]:
    """群數、各群件數、singleton 比例。"""
    counts: dict[int, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    sizes = sorted(counts.values(), reverse=True)
    singletons = sum(1 for size in sizes if size == 1)
    return {
        "topic_count": len(sizes),
        "sizes": sizes,
        "singleton_count": singletons,
        "singleton_ratio": round(singletons / len(sizes), 4) if sizes else 0.0,
        "largest_share": round(sizes[0] / len(labels), 4) if sizes else 0.0,
    }


def _run_dpmeans(vectors: list[list[float]]) -> dict[str, Any]:
    started = time.perf_counter()
    lambda_result = dpmeans.derive_lambda(vectors)
    state = dpmeans.fit(vectors, lambda_=lambda_result.value)
    elapsed = time.perf_counter() - started
    profile = _profile(state.labels)
    profile.update({
        "engine": "dpmeans",
        "lambda": round(lambda_result.value, 6),
        "lambda_method": lambda_result.method,
        "elapsed_seconds": round(elapsed, 3),
    })
    return profile


def _run_kmeans(vectors: list[list[float]], k: int) -> dict[str, Any]:
    """基準：MiniBatchKMeans（與現行 finalize 同一組參數）。"""
    from sklearn.cluster import MiniBatchKMeans

    started = time.perf_counter()
    model = MiniBatchKMeans(n_clusters=k, random_state=42, batch_size=128, n_init=3)
    labels = [int(v) for v in model.fit_predict(vectors)]
    elapsed = time.perf_counter() - started
    profile = _profile(labels)
    profile.update({"engine": "minibatch_kmeans", "k": k,
                    "elapsed_seconds": round(elapsed, 3)})
    return profile


def _stability(vectors: list[list[float]], *, rounds: int = 5) -> dict[str, Any]:
    """換餵入順序重跑，看群數是否穩定。

    ⚠ DP-Means **是**順序敏感的演算法，這裡不假裝它不是——量的是「群數會不會
    因為順序而變動」。正式流程一律以固定順序餵入，但資料本身若脆弱到換個順序
    就多出兩群，那個 lambda 就不該採用。
    """
    lambda_result = dpmeans.derive_lambda(vectors)
    counts = []
    rng = random.Random(20260809)
    for _ in range(rounds):
        shuffled = list(vectors)
        rng.shuffle(shuffled)
        counts.append(len(dpmeans.fit(shuffled, lambda_=lambda_result.value).centers))
    return {
        "rounds": rounds,
        "topic_counts": counts,
        "min": min(counts),
        "max": max(counts),
        "spread": max(counts) - min(counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=int, required=True)
    parser.add_argument("--source-field", required=True)
    parser.add_argument("--baseline-k", type=int, default=None,
                        help="基準 k；不給則用 DP-Means 得出的群數，便於同群數對照")
    args = parser.parse_args()

    vectors, patent_ids = _load_vectors(args.workspace, args.source_field)
    dp = _run_dpmeans(vectors)
    k = args.baseline_k or dp["topic_count"]
    report = {
        "workspace_id": args.workspace,
        "source_field": args.source_field,
        "document_count": len(patent_ids),
        "dpmeans": dp,
        "baseline": _run_kmeans(vectors, k),
        "dpmeans_stability": _stability(vectors),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
