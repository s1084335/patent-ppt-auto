"""Cosine Online DP-Means 分群核心（openspec replace-clustering-with-dpmeans）。

## 為什麼取代 MiniBatchKMeans

現行增量流程固定 `n_clusters`，`partial_fit` 只會**移動既有中心**——新增資料
即使形成明顯的新群，也只能被硬塞進最近的舊主題，長不出新主題。DP-Means 的
規則相反：與所有既有中心的距離都超過 lambda 時，就**開一個新群**。

## 契約（tasks 1.2；可執行版見 tests/test_dpmeans_core.py）

- 距離：cosine distance = 1 − cos(u, v)，範圍 [0, 2]
- 建群門檻：距離 **嚴格大於** lambda 才開新群（等於時併入既有群）
- 中心更新：online 平均後重新 L2 normalize
- lambda：由校準資料推導（`derive_lambda`），不要求使用者輸入（CLU-008）
- 空／小樣本：0 筆回空結果不 raise；1 筆自成一群

⚠ **本模組是純函式**：不碰 DB、artifact、job 與 sklearn。這讓演算法本身可以
用合成向量完全決定性地驗證——分群是「結果對不對很難一眼看出」的那種程式，
把它跟 I/O 綁在一起就再也驗不動了。

⚠ **順序敏感是 DP-Means 的性質，不是 bug**：先看到的點決定中心的起點。本模組
不假裝它不存在，而是要求呼叫端以固定順序餵入；測試也明確測出這個界線。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

#: lambda 推導公式的版本。⚠ 改公式一定要跟著改：run metadata 存的是這個字串，
#: 舊 run 的 lambda 為何是那個值，日後只能靠它追。
LAMBDA_METHOD_VERSION = "v1"

#: 校準樣本不足時的預設 lambda。
#: ⚠ 取 0.35 的依據：PatentSBERTa 的專利文本向量在 PCA 後，同主題成對距離多在
#: 0.2 以下、跨主題多在 0.5 以上（滑雪機 44 件實測）。0.35 落在兩者之間。
#: 這是**回退值**不是主要路徑——正常情況一律走 derive_lambda 的資料推導。
FALLBACK_LAMBDA = 0.35

#: 推導 lambda 時取的分位數。
#: ⚠ 取「每點到最近鄰距離」的 P75：讓四分之三的點能併入既有群，其餘較孤立的
#: 才有機會開新群。取中位數會太少群、取 P90 會碎成一堆單點群。
NEAREST_NEIGHBOR_QUANTILE = 0.75

Vector = list[float]


@dataclass(frozen=True)
class LambdaResult:
    """lambda 推導結果——值、方法與版本一起回，metadata 才記得住怎麼來的。"""

    value: float
    method: str
    version: str = LAMBDA_METHOD_VERSION
    sample_size: int = 0


@dataclass
class ClusterState:
    """分群狀態：中心、每群點數，以及本批的指派結果。

    `new_center_indexes` 是**本批新開的**中心編號——增量時只有這些主題需要
    排 `ai:topic_label`，既有主題的人工命名不該被重跑覆蓋。
    """

    centers: list[Vector] = field(default_factory=list)
    counts: list[int] = field(default_factory=list)
    labels: list[int] = field(default_factory=list)
    new_center_indexes: list[int] = field(default_factory=list)


def l2_normalize(vector: Vector) -> Vector:
    """L2 正規化（CLU-009：PCA 降維後必須重做，cosine 的前提才成立）。

    ⚠ 零向量原樣回傳而不是除以 0：嵌入失敗或空文本會產生它。
    """
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        return list(vector)
    return [x / norm for x in vector]


def cosine_distance(a: Vector, b: Vector) -> float:
    """cosine distance = 1 − cos(a, b)，範圍 [0, 2]。

    ⚠ 任一為零向量時回 1.0（正交）而不是 raise：它代表「無方向資訊」，
    當成離所有群都遠即可，不該讓整批分群失敗。
    """
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 1.0
    return 1.0 - dot / (na * nb)


def derive_lambda(sample: list[Vector]) -> LambdaResult:
    """由校準樣本推導建群門檻（CLU-008）。

    做法：算每個點到**最近鄰**的 cosine distance，取其 P75。

    ⚠ 為什麼用最近鄰而不是全體兩兩距離：後者被離群點主導，一個孤立文件就能
    把門檻拉高到整批併成一群。最近鄰距離反映的是「同類文件有多近」，那才是
    這個門檻該回答的問題。

    ⚠ 與順序無關——它是資料的統計量，不是流程的產物（測試釘著）。
    """
    points = [l2_normalize(v) for v in sample]
    if len(points) < 2:
        return LambdaResult(value=FALLBACK_LAMBDA,
                            method=f"fallback:constant:{FALLBACK_LAMBDA}",
                            sample_size=len(points))

    nearest: list[float] = []
    for i, point in enumerate(points):
        others = (cosine_distance(point, other)
                  for j, other in enumerate(points) if j != i)
        nearest.append(min(others))
    nearest.sort()

    index = min(len(nearest) - 1,
                max(0, int(round(NEAREST_NEIGHBOR_QUANTILE * (len(nearest) - 1)))))
    value = nearest[index]
    if value <= 0.0:
        # 全部重複文件時最近鄰距離會是 0——那不是有效門檻。
        return LambdaResult(value=FALLBACK_LAMBDA,
                            method=f"fallback:degenerate:{FALLBACK_LAMBDA}",
                            sample_size=len(points))
    return LambdaResult(
        value=value,
        method=f"nearest_neighbor_quantile:{NEAREST_NEIGHBOR_QUANTILE}",
        sample_size=len(points),
    )


def _assign_one(state: ClusterState, point: Vector, lambda_: float) -> int:
    """把一個點指派到最近的中心；都太遠就開新群。回傳中心編號。"""
    best_index, best_distance = -1, math.inf
    for index, center in enumerate(state.centers):
        distance = cosine_distance(point, center)
        if distance < best_distance:
            best_index, best_distance = index, distance

    # ⚠ 嚴格大於才開新群：距離**等於** lambda 時併入既有群。浮點邊界上兩種
    # 都「合理」，但不定死就會出現「同一份資料兩次跑出不同群數」。
    if best_index < 0 or best_distance > lambda_:
        state.centers.append(list(point))
        state.counts.append(1)
        return len(state.centers) - 1

    count = state.counts[best_index]
    center = state.centers[best_index]
    # online 平均：新點以 1/(n+1) 的權重把中心拉過去，再重新正規化。
    moved = [(c * count + p) / (count + 1) for c, p in zip(center, point)]
    state.centers[best_index] = l2_normalize(moved)
    state.counts[best_index] = count + 1
    return best_index


def fit(vectors: list[Vector], *, lambda_: float) -> ClusterState:
    """對一批向量做 DP-Means。⚠ 呼叫端負責固定順序（見模組說明）。"""
    state = ClusterState()
    for vector in vectors:
        point = l2_normalize(vector)
        index = _assign_one(state, point, lambda_)
        state.labels.append(index)
        if index == len(state.centers) - 1 and state.counts[index] == 1:
            state.new_center_indexes.append(index)
    return state


def partial_fit(state: ClusterState, vectors: list[Vector], *,
                lambda_: float) -> ClusterState:
    """以既有中心對新文件增量分群（CLU-004）。

    ⚠ 回傳**新的** state，且既有中心在沒有新成員時不會被動到——舊主題的
    topic_key 要對得上，否則報表與人工命名會整批錯位。
    """
    updated = ClusterState(
        centers=[list(c) for c in state.centers],
        counts=list(state.counts),
        labels=[],
        new_center_indexes=[],
    )
    before = len(updated.centers)
    for vector in vectors:
        point = l2_normalize(vector)
        index = _assign_one(updated, point, lambda_)
        updated.labels.append(index)
        if index >= before and index not in updated.new_center_indexes:
            updated.new_center_indexes.append(index)
    return updated
