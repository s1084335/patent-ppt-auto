"""增量分群的演算法分流（openspec replace-clustering-with-dpmeans 2.4）。

## 兩條規則，方向相反

- **新 run（finalize）看 feature flag**（`resolve_algorithm`）：2026-08-09 使用者
  定案「並存，驗收後再切」，所以要能用旗標決定這次正式分群用哪個引擎。
- **增量跟隨 artifact 記錄的演算法**（`predict_incremental`），**不看 flag**。
  ⚠ 中途換引擎會讓中心格式對不上——KMeans 的 artifact 沒有 `dpmeans_state`，
  DP-Means 的沒有 sklearn 模型。硬換的結果不是報錯，是**分群結果莫名其妙**，
  那是最難查的一種。

## 為什麼要有這層接縫

原本 `workspace_service.incremental_workspace` 直接呼叫 `partial_fit_bertopic`，
夾在 DB 交易、artifact 存檔與 run 狀態之間，完全驗不到。抽成純函式之後，
分流規則與新主題識別都能用合成向量決定性地測。
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

from . import artifacts, dpmeans, keywords
from .artifacts import ALGORITHM_DPMEANS, ALGORITHM_KMEANS

LOGGER = logging.getLogger(__name__)

#: 讀 feature flag 的環境變數名。⚠ 預設不設＝走舊引擎。
ALGORITHM_ENV = "CLUSTERING_ALGORITHM"

#: 每群取幾個關鍵詞算 coherence／diversity（與既有 finalize 同口徑）。
TOP_TERMS_PER_TOPIC = 10

_SUPPORTED = (ALGORITHM_KMEANS, ALGORITHM_DPMEANS)


@dataclass
class IncrementalPrediction:
    """一批新文件的增量分群結果。

    `new_topic_indexes` 是**本批新開的**主題編號——只有這些需要排
    `ai:topic_label`，既有主題的人工命名不該被重跑覆蓋（CLU-004）。
    `updated_state` 只有 DP-Means 會有，要存回 artifact 下一批才接得上。
    """

    topics: list[int]
    algorithm: str
    new_topic_indexes: list[int] = field(default_factory=list)
    updated_state: dict[str, Any] | None = None


def resolve_algorithm(requested: str | None) -> str:
    """把 feature flag 的值解析成演算法識別；只在**新 run** 使用。

    ⚠ 打錯字不得靜默退回預設——那會讓人以為切換成功了，然後對著 KMeans 的
    結果找 DP-Means 的 bug。
    """
    if requested is None or requested == "":
        return ALGORITHM_KMEANS
    value = requested.strip().lower()
    if value not in _SUPPORTED:
        raise ValueError(
            f"unknown clustering algorithm: {requested!r}; expected one of {_SUPPORTED}"
        )
    return value


def predict_incremental(
    artifact: Any,
    *,
    documents: list[str],
    vectors: list[list[float]],
    # ⚠ requested_algorithm 收下但**故意不使用**——見 docstring。
    requested_algorithm: str | None = None,
) -> IncrementalPrediction:
    """對一批新文件做增量分群，依 artifact 記錄的演算法分流。

    ⚠ `requested_algorithm` 收下但**故意不使用**：呼叫端（job 層）拿得到 flag，
    這個參數的存在是為了讓「增量不看 flag」這條規則在介面上就看得見，並由測試
    釘住。拿掉它反而會讓下一個人以為「這裡忘了接 flag」而補上。
    """
    algorithm = getattr(artifact, "algorithm", ALGORITHM_KMEANS)
    if algorithm == ALGORITHM_DPMEANS:
        return _predict_dpmeans(artifact, vectors)
    return _predict_kmeans(artifact, documents, vectors)


def _predict_kmeans(artifact: Any, documents: list[str],
                    vectors: list[list[float]]) -> IncrementalPrediction:
    """舊路徑：BERTopic/MiniBatchKMeans 的 partial_fit。固定 k，長不出新主題。"""
    if not documents:
        return IncrementalPrediction(topics=[], algorithm=ALGORITHM_KMEANS)
    model = artifact.topic_model
    model.partial_fit(documents, embeddings=vectors)
    return IncrementalPrediction(
        topics=[int(t) for t in model.topics_],
        algorithm=ALGORITHM_KMEANS,
    )


def _predict_dpmeans(artifact: Any, vectors: list[list[float]]) -> IncrementalPrediction:
    """新路徑：Cosine Online DP-Means（CLU-004）。

    ⚠ 標成 DP-Means 卻沒有狀態＝artifact 壞了，當場說。默默改走 KMeans 會產出
    看起來正常、實際上錯的分群。
    """
    payload = getattr(artifact, "dpmeans_state", None)
    if not payload:
        raise ValueError(
            "artifact 標示為 dpmeans 但沒有 dpmeans_state；artifact 已損壞或版本不符"
        )
    state, lambda_ = artifacts.deserialize_dpmeans_state(payload)
    if not vectors:
        return IncrementalPrediction(
            topics=[], algorithm=ALGORITHM_DPMEANS,
            updated_state=artifacts.serialize_dpmeans_state(state, lambda_=lambda_),
        )
    # CLU-009：PCA 降維後的向量在距離計算前重做 L2 normalize（dpmeans 內部處理）。
    updated = dpmeans.partial_fit(state, vectors, lambda_=lambda_)
    return IncrementalPrediction(
        topics=list(updated.labels),
        algorithm=ALGORITHM_DPMEANS,
        new_topic_indexes=list(updated.new_center_indexes),
        updated_state=artifacts.serialize_dpmeans_state(updated, lambda_=lambda_),
    )


# --------------------------------------------------------------------------
# 新主題落地
# --------------------------------------------------------------------------

#: topic_code 的唯一格式定義。⚠ 原本寫死在 runner.py 的 f-string 裡；DP-Means
#: 也要產 code，複製第二份就會各自演進，所以改由此處定義、runner 消費。
TOPIC_CODE_PREFIX = "T"
TOPIC_CODE_DIGITS = 3


def format_topic_code(position: int) -> str:
    """依位置產生 topic_code（T001、T002…）。唯一定義處。"""
    return f"{TOPIC_CODE_PREFIX}{position:0{TOPIC_CODE_DIGITS}d}"


@dataclass(frozen=True)
class NewTopic:
    """本批要新建的主題：模型編號與配到的 topic_code。"""

    model_topic_id: int
    topic_code: str


@dataclass
class TopicKeyPlan:
    """一批增量文件的 topic_key 指派計畫。

    `topic_keys` 中的 `None` 代表**未知舊 ID**——交給呼叫端做 centroid fallback
    （使用者合併／停用主題後才會出現）。⚠ 新主題絕不會是 None，它們已配到新
    code；否則就會被 fallback 併進舊主題，本 change 等於白做。
    """

    topic_keys: list[str | None]
    new_topics: list[NewTopic] = field(default_factory=list)

    @property
    def topic_codes_needing_label(self) -> list[str]:
        """需要排 ai:topic_label 的主題（CLU-004：只有新的，不碰既有人工命名）。"""
        return [topic.topic_code for topic in self.new_topics]


def plan_topic_keys(
    *,
    predicted_topics: list[int],
    new_topic_indexes: list[int],
    model_to_code: dict[int, str],
    existing_codes: list[str],
) -> TopicKeyPlan:
    """把本批的模型 topic 編號映射成 topic_key，並分出要新建的主題。

    ⚠ 這裡的核心是把兩種「不在 model_to_code 裡的編號」分開：

    - **新主題**（出現在 `new_topic_indexes`）→ 配新 code，建新主題。
    - **未知舊 ID**（使用者合併／停用主題後模型仍吐舊編號）→ 回 None，由呼叫端
      改派 centroid 最近的 active 主題（2026-07-27 既有行為，不得改變）。

    兩者長得一模一樣，分不開就會二選一地錯：全走 fallback 則新主題消失，全建新
    主題則被合併掉的舊主題會復活。
    """
    next_position = _next_topic_position(existing_codes)
    new_by_model_id: dict[int, NewTopic] = {}
    keys: list[str | None] = []
    for model_topic_id in predicted_topics:
        code = model_to_code.get(model_topic_id)
        if code is not None:
            keys.append(code)
            continue
        if model_topic_id in new_topic_indexes:
            topic = new_by_model_id.get(model_topic_id)
            if topic is None:
                topic = NewTopic(model_topic_id=model_topic_id,
                                 topic_code=format_topic_code(next_position))
                new_by_model_id[model_topic_id] = topic
                next_position += 1
            keys.append(topic.topic_code)
            continue
        keys.append(None)
    return TopicKeyPlan(topic_keys=keys, new_topics=list(new_by_model_id.values()))


def build_topic_entries(
    *,
    existing_topics: list[dict[str, Any]],
    new_topics: list[NewTopic],
    source_field: str,
    run_id: int,
    doc_counts: dict[str, int],
) -> list[dict[str, Any]]:
    """把新主題接在既有主題清單後，回傳要寫進 topic_state_json 的完整清單。

    ⚠ 增量 run 原本不帶 topics（讀取端只認 topics 非空的 run），所以一旦有新
    主題，這個 run 就必須寫出**完整**清單——只寫新的會讓既有主題整批消失。

    ⚠ 既有主題**原樣保留**：人工命名與 `label_source` 不得被增量覆寫。使用者
    改過的名字被機器蓋掉，是那種一次就失去信任的錯。
    """
    if not new_topics:
        return existing_topics
    entries = list(existing_topics)
    next_id = max((int(t.get("topic_id") or 0) for t in existing_topics), default=0) + 1
    next_order = max((int(t.get("display_order") or 0) for t in existing_topics), default=0) + 1
    for topic in new_topics:
        entries.append({
            "topic_id": next_id,
            "topic_code": topic.topic_code,
            "source_field": source_field,
            "created_run_id": run_id,
            "model_topic_ids": [topic.model_topic_id],
            "topic_kind": "model",
            "doc_count": int(doc_counts.get(topic.topic_code, 0)),
            # ⚠ DP-Means 增量沒有 c-TF-IDF 關鍵詞（那是 BERTopic 全量擬合的產物），
            # 所以名字只能是佔位。label_source 必須留 fallback，否則 ai:topic_label
            # 會略過它，這個主題就永遠叫「新主題 Tnnn」。
            "keywords": [],
            "label": f"新主題 {topic.topic_code}",
            "label_source": "fallback",
            "representative_patent_ids": [],
            "display_order": next_order,
            "status": "active",
        })
        next_id += 1
        next_order += 1
    return entries


# --------------------------------------------------------------------------
# lambda 自動選擇
# --------------------------------------------------------------------------

#: 選 lambda 的四項判準（2026-08-09 使用者定案，事前定死）。
#:
#: ⚠ 為什麼要掃描而不是用一個固定分位數：固定分位等於假設「**所有批次**的
#: 專利，第 N 百分位就是對的群半徑」。那個假設只在一批資料上驗過——換一批
#: 專利（不同技術領域、撰寫風格、件數），距離分布的形狀就變了。
#: 掃描讓每批資料自己決定，這才是 CLU-008「由資料推導」的意思。
MIN_MEDIAN_TOPIC_SIZE = 3          # 典型主題要夠厚，少於 3 件講不出趨勢
MAX_SINGLETON_DOC_SHARE = 0.15     # ⚠ 用**文件**佔比：小資料裡一兩個單件主題可容許
MIN_BETWEEN_DISTANCE = 0.30        # 低於此表示兩個主題在講同一件事
MAX_STABILITY_SPREAD = 1           # 換順序群數變動；大代表 lambda 落在分界上

#: 掃描的分位範圍與點數。⚠ 上下界用**該批資料的距離分位**而不是絕對值——
#: 寫死 0.5–1.1 這種區間，換一批分布不同的資料就整段落在區間外。
#: 掃描區間的分位下界／上界。
#:
#: ⚠ 2026-08-09 下界由 P10 改為 P1——**P10 會切掉整個有效區**。
#: 群數越多，同群距離佔全體成對距離的比例越小（≈ 1/群數）。12 群時同群距離
#: 只佔 8.3%，全部落在 P8 以下，而群半徑（λ 該取的量）就在那裡。
#:
#: 實測（合成資料 n=300、12 個真實群、每群 25 篇）：
#: - 正確的 λ 落在 0.30–0.80
#: - P10–P60 的掃描區間是 0.88–1.01 —— **完全錯過**，結果只分出 2 群
#:
#: 滑雪機資料只有 5–7 群，同群距離佔約 17% > 10%，所以 P10 剛好還涵蓋得到
#: ——⚠ 這就是「小資料對、大資料爆」的真正原因，而且完全不會報錯。
#:
#: 下界取 P1 而非 0：λ 太小只會產生全單點群，會被判準①②刷掉不致誤選，
#: 但掃在那裡純屬浪費點數。
SWEEP_QUANTILE_LOW = 0.01
SWEEP_QUANTILE_HIGH = 0.60

#: 掃描的起始點數與上限。⚠ **點數不是固定值**——由收斂判準決定掃多細。
#:
#: 先前寫死 18（憑感覺）實測取樣不足：功效通道 18 點給 5 群、24 點給 6 群。
#: 改成 24 只是換一個猜的數字——那與「固定 λ 分位數」是**同一個錯誤**：把該由
#: 資料決定的東西寫成常數。
#:
#: 現在的做法：從 `SWEEP_START_POINTS` 開始，每輪在相鄰兩點間插中點
#: （8→15→29→57），直到**連續兩輪選出的群數相同**。
#: ⚠ 插中點而非等分重取，是為了讓舊點全部保留——掃描具決定性，重算純屬浪費。
SWEEP_START_POINTS = 8


#: 穩定度重跑次數。⚠ 只對通過前三項的 lambda 才跑——每次都跑會讓校準變慢
#: 一個數量級，而大部分 lambda 在前三項就被刷掉了。
STABILITY_ROUNDS = 5
STABILITY_SEED = 20260809


@dataclass
class LambdaSelection:
    """選出的 lambda 與整張掃描表。

    ⚠ 掃描表要留給使用者看：選了哪個、其他被哪一項判準刷掉。只給一個數字，
    使用者無從判斷這次分群可不可信。
    """

    value: float
    method: str
    version: str
    #: 實際掃了幾點，以及是否收斂。⚠ 沒收斂要說——假裝收斂會讓使用者以為
    #: 結果比實際可靠。
    sweep_points: int = 0
    converged: bool = False
    #: 實際用來推導的樣本數。⚠ 欄位名與 `dpmeans.LambdaResult` 一致——
    #: `artifacts.build_run_metadata` 兩種都要收得下，缺一個欄位就 AttributeError，
    #: 而且是跑到 finalize 才炸（2026-08-09 實機驗收抓到）。
    sample_size: int = 0
    sweep: list[dict[str, Any]] = field(default_factory=list)


def resolution_limit(distances: list[float], *, low: float, high: float) -> int:
    """掃描區間內**實際存在的相異距離值**數量——掃描的自然上限。

    ⚠ λ 只在跨過某個實際距離值時才改變分群結果：兩個 λ 之間若沒有任何距離落在
    其中，它們產生完全相同的分群。所以細分到步進細於這些值的間隔時，再細分只是
    在掃空白。

    ⚠ 重複的距離值只算一次——它們是同一個切點，不增加解析度。
    """
    return len({d for d in distances if low <= d <= high})


def select_lambda(vectors: list[list[float]], *, documents: list[str],
                  max_points: int | None = None) -> LambdaSelection:
    """掃 lambda 區間並用判準挑選，掃描密度**由收斂判準決定**（CLU-008）。

    每輪在相鄰兩點間插中點加密，直到連續兩輪選出的群數相同為止。
    ⚠ 舊點的結果直接重用：掃描具決定性，同一個 λ 在同一批資料上必得同一結果。

    ⚠ 全軍覆沒時回退到分位數公式並在 method 標明。會發生在資料本身沒有結構時
    （全部很像、或全部互相遠離）——那是資料的性質不是錯誤，仍要產出可用的 run
    讓使用者看到結果再判斷。
    """
    points = [dpmeans.l2_normalize(v) for v in vectors]
    distances = _pairwise_sorted(points)
    if not distances:
        return _fallback_selection(vectors, reason="no_pairwise_distance")

    grid = _initial_grid(distances)
    if not grid:
        return _fallback_selection(vectors, reason="degenerate_range")

    # ⚠ 上限由**資料的自然解析度**決定，不是固定點數（見 resolution_limit）。
    if max_points is None:
        # ⚠ 下限保底 SWEEP_START_POINTS：區間內沒有距離值時仍要能掃完第一輪。
        max_points = max(resolution_limit(distances, low=grid[0], high=grid[-1]),
                         SWEEP_START_POINTS)

    evaluated: dict[float, dict[str, Any]] = {}
    previous_count: int | None = None
    converged = False
    previous_counts: set[int] | None = None
    while True:
        for value in grid:
            if value not in evaluated:
                evaluated[value] = _evaluate(
                    value, dpmeans.fit(vectors, lambda_=value), points, documents)
        rows = [evaluated[value] for value in sorted(evaluated)]
        candidates = build_candidates(rows)
        best = pick_best_candidate(candidates)
        count = best["topic_count"] if best else None
        counts = {c["topic_count"] for c in candidates}
        # ⚠ 收斂＝**候選集合**穩定，不只是最佳候選的群數相同。
        # 實測（12 個真實群的合成資料）：只比最佳群數時，n=300／600 在第二輪
        # 碰巧兩次都選到 2 群就停了，標成「已收斂」卻只找到 2 個主題——假收斂。
        # 只要還在發現新的群數選項，就代表還沒掃夠。
        if (count is not None and count == previous_count
                and previous_counts is not None and counts == previous_counts):
            converged = True
            break
        previous_count = count
        previous_counts = counts
        if len(evaluated) >= max_points:
            break
        grid = _refine(sorted(evaluated))

    rows = [evaluated[value] for value in sorted(evaluated)]
    best = _pick_best(rows)
    if best is None:
        selection = _fallback_selection(vectors, reason="no_lambda_passed")
        selection.sweep = rows
        selection.sweep_points = len(rows)
        return selection
    suffix = "converged" if converged else "not_converged"
    return LambdaSelection(
        value=best["lambda"],
        method=f"sweep:{SWEEP_QUANTILE_LOW}-{SWEEP_QUANTILE_HIGH}:weighted_quality:{suffix}",
        version=dpmeans.LAMBDA_METHOD_VERSION,
        sample_size=len(points),
        sweep_points=len(rows),
        converged=converged,
        sweep=rows,
    )


def build_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把通過四項判準的掃描點**依群數去重**成候選清單。

    ⚠ 這一步同時解掉兩個問題：

    1. **使用者失去調整主題數的能力**：DP-Means 只產一個候選時，介面上是
       「請選擇方案」但實際沒得選。去重後每種群數一個候選，選了真的會不同。
    2. **評分對掃描密度敏感**：`rank_candidates` 是跨候選正規化，候選集合一變
       所有分數就變。實測固定 24／36／60 點給技術 7 群，逐步細分到 29 點卻給
       6 群——同一批資料、不同掃法、不同答案。去重後候選集合＝群數種類（有限
       且穩定），掃描再密只會讓代表更準，不改變候選集合。

    代表取**該群數區間的中位 λ**——⚠ 不取分數最高或邊緣：邊緣值換一批資料就
    掉到隔壁群數去了，中點最耐得住資料微變。
    """
    from .model import rank_candidates

    passed = [row for row in rows if not row["failed"]]
    if not passed:
        return []
    by_count: dict[int, list[dict[str, Any]]] = {}
    for row in passed:
        by_count.setdefault(row["topic_count"], []).append(row)

    representatives = []
    for count in sorted(by_count):
        group = sorted(by_count[count], key=lambda r: r["lambda"])
        representatives.append(dict(group[len(group) // 2]))

    scores = rank_candidates([
        {"coherence": r["coherence"], "diversity": r["diversity"],
         "balance": r["balance"], "small_topic_ratio": r["small_topic_ratio"]}
        for r in representatives
    ])
    for row, score in zip(representatives, scores):
        row["score"] = round(score, 6)
    return representatives


def pick_best_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """挑分數最高的候選。

    ⚠ 分數相同時取較大的 lambda（主題較少、較保守）——並列必須有固定解，
    否則同一批資料兩次校準會選到不同的方案。
    """
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c["score"], c["lambda"]))


def _pick_best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """掃描收斂判斷用：先去重再挑最佳，與正式候選同一條路徑。"""
    return pick_best_candidate(build_candidates(rows))


def _quantile_at(distances: list[float], quantile: float) -> float:
    """取排序後距離的第 q 分位值。"""
    n = len(distances)
    return distances[min(n - 1, max(0, round(quantile * (n - 1))))]


def _initial_grid(distances: list[float]) -> list[float]:
    """起始掃描點：在**分位空間**等分，不是數值空間。

    ⚠ 為什麼是分位而不是等距：λ 的有效變化只發生在距離值密集的地方——兩個 λ
    之間若沒有距離值，它們產出完全相同的分群。等距取樣會把大量點浪費在稀疏區，
    而在密集區（正是群結構所在）太疏。

    實測：下界由 P10 放寬到 P1 後區間寬了 3 倍，8 個等距點讓功效通道從能找到
    5–6 群退步到只剩 3–4 群——點都掃在空曠處了。改分位等分後回復。
    """
    quantiles = _initial_quantiles()
    if not quantiles:
        return []
    values = sorted({round(_quantile_at(distances, q), 6) for q in quantiles})
    return [v for v in values if v > 0]


def _initial_quantiles() -> list[float]:
    step = (SWEEP_QUANTILE_HIGH - SWEEP_QUANTILE_LOW) / (SWEEP_START_POINTS - 1)
    return [SWEEP_QUANTILE_LOW + step * i for i in range(SWEEP_START_POINTS)]


def _refine(existing: list[float]) -> list[float]:
    """在相鄰兩點間插中點。⚠ 舊點全部保留——掃描具決定性，重算純屬浪費。"""
    return [round((existing[i] + existing[i + 1]) / 2, 6)
            for i in range(len(existing) - 1)]


def _fallback_selection(vectors: list[list[float]], *, reason: str) -> LambdaSelection:
    """掃描產不出結果時退回分位數公式，並在 method 標明原因。"""
    result = dpmeans.derive_lambda(vectors)
    return LambdaSelection(
        value=result.value, method=f"fallback:{reason}:{result.method}",
        version=result.version, sample_size=result.sample_size)




#: 精簡掃描表時，非選中列保留的欄位。
#: ⚠ `failed` 不能省——沒有它就看不出選中點是不是剛好卡在通過區間的邊緣。
COMPACT_SWEEP_FIELDS = ("lambda", "topic_count", "failed")


def compact_sweep(sweep: list[dict[str, Any]], *,
                  chosen_lambda: float | None) -> list[dict[str, Any]]:
    """把掃描表精簡成適合長期保存的形式（2026-08-09 使用者定案）。

    實測：18 列完整掃描表 4,410 bytes，與同一個 run 的 topics（4,220 bytes）
    同量級——等於讓 `topic_state_json` 翻倍。而被刷掉的列，品質指標本來就是
    `None`（三層漏斗根本沒算它們）。

    ⚠ **掃描是決定性的**：同一批資料重跑必得同一張表（n=44 約 6 秒）。所以
    沒必要全存，需要完整指標時重算即可。

    保留原則：選中那列完整（回答「為什麼是這個 λ」），其餘只留形狀與淘汰原因
    （回答「其他點為什麼不行」）。⚠ 列數與順序不變——精簡的是每列的欄位，
    不是丟掉整列，否則看不出掃描範圍與密度。
    """
    return [
        dict(row) if chosen_lambda is not None and row.get("lambda") == chosen_lambda
        else {key: row.get(key) for key in COMPACT_SWEEP_FIELDS}
        for row in sweep
    ]


def _pairwise_sorted(points: list[list[float]]) -> list[float]:
    """全體兩兩距離（已排序）。⚠ 超過上限時抽樣，固定 seed 保可重現。"""
    if len(points) < 2:
        return []
    sample = points
    if len(sample) > dpmeans.PAIRWISE_SAMPLE_LIMIT:
        sample = random.Random(dpmeans.PAIRWISE_SAMPLE_SEED).sample(
            sample, dpmeans.PAIRWISE_SAMPLE_LIMIT)
    return sorted(
        dpmeans.cosine_distance(sample[i], sample[j])
        for i in range(len(sample)) for j in range(i + 1, len(sample))
    )


def _sweep_values(distances: list[float]) -> list[float]:
    """掃描點：由該批資料的距離分位決定，不寫死絕對值。"""
    n = len(distances)

    def at(quantile: float) -> float:
        return distances[min(n - 1, max(0, round(quantile * (n - 1))))]

    low, high = at(SWEEP_QUANTILE_LOW), at(SWEEP_QUANTILE_HIGH)
    if high <= low:
        return [low] if low > 0 else []
    step = (high - low) / (SWEEP_STEPS - 1)
    return [round(low + step * i, 6) for i in range(SWEEP_STEPS)]


#: 前幾項判準：(名稱, 判定函式)。⚠ 寫成資料而不是一串 if——加判準時只需多
#: 一列，不會有人漏掉某項的判定。穩定度另外處理，因為它要重跑分群，只在其餘
#: 都過時才值得花那個時間。
#:
#: ⚠ `single_cluster` 是 2026-08-09 補上的：全部併成一群是**退化解**。原本
#: 判準③在只有 1 群時算不出群間距離、回 None，而 None 被當成「通過」——實測
#: 5 個明顯分開的群、40 篇文件，最後選出的竟是「1 群 40 篇」，而且分數最高
#: （diversity 對單一群回 1.0，因為沒有第二組可比）。
#: 這種錯不會報錯，使用者看到的是「這批專利只有一個主題」。
_CRITERIA = (
    ("single_cluster", lambda r: r["topic_count"] >= 2),
    ("median_size", lambda r: r["median_size"] >= MIN_MEDIAN_TOPIC_SIZE),
    ("singleton_doc_share",
     lambda r: r["singleton_doc_share"] <= MAX_SINGLETON_DOC_SHARE),
    ("between_min",
     lambda r: r["between_min"] is None or r["between_min"] >= MIN_BETWEEN_DISTANCE),
)


def _evaluate(value: float, state: Any, points: list[list[float]],
              documents: list[str]) -> dict[str, Any]:
    """對單一 lambda 逐項判定。⚠ 及格制，不做加總分數。

    加總會讓「主題全是單點但分離度很好」這種組合看起來還行。
    """
    row = _shape_metrics(value, state)
    row["failed"] = [name for name, check in _CRITERIA if not check(row)]
    # ⚠ 穩定度與品質指標只對通過前三項的 lambda 才算——每個都算會讓校準慢
    # 一個數量級，而大部分 lambda 在前三項就被刷掉了。
    if row["failed"]:
        return row
    spread = _stability_spread(points, value)
    row["stability_spread"] = spread
    if spread > MAX_STABILITY_SPREAD:
        row["failed"].append("stability")
        return row
    row.update(_quality(state.labels, documents))
    row.update(_size_metrics(state.labels))
    return row


def _shape_metrics(value: float, state: Any) -> dict[str, Any]:
    """群數與件數分布的基本量（判準前三項用）。"""
    sizes = sorted(state.counts, reverse=True)
    singletons = sum(1 for size in sizes if size == 1)
    between = _min_center_distance(state.centers)
    return {
        "lambda": value,
        "topic_count": len(state.centers),
        "median_size": sizes[len(sizes) // 2] if sizes else 0,
        # ⚠ 文件佔比而非群數佔比——小資料裡一兩個單件主題是可容許的。
        "singleton_doc_share": round(
            singletons / len(state.labels), 4) if state.labels else 0.0,
        "between_min": round(between, 4) if between is not None else None,
        "coherence": None,
        "diversity": None,
        "balance": None,
        "small_topic_ratio": None,
        "score": None,
    }


def _size_metrics(labels: list[int]) -> dict[str, float]:
    """件數分布指標——沿用既有定義，不另寫一份。

    ⚠ balance（normalized entropy）與 small_topic_ratio 本來就只看件數分布，
    與 c-TF-IDF 無關，所以對 DP-Means 直接適用。
    """
    from .model import small_topic_ratio, topic_balance

    return {
        "balance": round(topic_balance(labels), 4),
        "small_topic_ratio": round(small_topic_ratio(labels, min_topic_docs=5), 4),
    }


def _min_center_distance(centers: list[list[float]]) -> float | None:
    if len(centers) < 2:
        return None
    return min(dpmeans.cosine_distance(centers[a], centers[b])
               for a in range(len(centers)) for b in range(a + 1, len(centers)))


def _stability_spread(points: list[list[float]], lambda_: float) -> int:
    """換餵入順序重跑，回傳群數的變動範圍。

    ⚠ DP-Means **是**順序敏感的演算法。正式流程一律固定順序，但資料若脆弱到
    換個順序就多出兩群，那個 lambda 換一批資料也會忽多忽少。
    """
    rng = random.Random(STABILITY_SEED)
    counts = []
    for _ in range(STABILITY_ROUNDS):
        shuffled = list(points)
        rng.shuffle(shuffled)
        counts.append(len(dpmeans.fit(shuffled, lambda_=lambda_).centers))
    return max(counts) - min(counts)


def _quality(labels: list[int], documents: list[str]) -> dict[str, Any]:
    """主題一致性（c_v）與多樣性。

    ⚠ 這兩個既有指標**不綁 BERTopic**：coherence 由 gensim 依文件本身計算，
    diversity 只看關鍵詞重疊度。補上關鍵詞抽取後對 DP-Means 一樣適用。
    """
    from .model import topic_diversity

    if not documents or len(documents) != len(labels):
        return {"coherence": None, "diversity": None}
    top_terms = keywords.extract_top_terms(
        documents, labels=labels, limit=TOP_TERMS_PER_TOPIC)
    if not top_terms:
        return {"coherence": None, "diversity": None}
    quality: dict[str, Any] = {"diversity": round(topic_diversity(top_terms), 4),
                               "coherence": None}
    try:
        from .model import topic_cv_coherence_per_topic

        per_topic = topic_cv_coherence_per_topic(
            documents, topics=labels, top_terms=top_terms)
        if per_topic:
            quality["coherence"] = round(sum(per_topic.values()) / len(per_topic), 4)
    except Exception:
        # ⚠ 不 raise：coherence 只是排序依據，算不出來時其餘三項指標仍可用。
        # 但要留下痕跡，否則「為什麼這次沒有一致性分數」查不出來。
        LOGGER.warning("topic coherence unavailable; ranking falls back to other metrics",
                       exc_info=True)
    return quality


# --------------------------------------------------------------------------
# 校準（calibrate）
# --------------------------------------------------------------------------

#: DP-Means 候選的類型標記。既有三種是 conservative／balanced／granular
#: （保守／平衡／細分），那是「選 k」的三個方向；DP-Means 只有一個。
CANDIDATE_TYPE_DPMEANS = "dpmeans"


def plan_dpmeans_calibration(
    vectors: list[list[float]], *, documents: list[str], elapsed_seconds: float,
) -> list[dict[str, Any]]:
    """DP-Means 的校準：掃 λ 到收斂，**每種群數產一個候選**。

    ⚠ 掃 k 那條路徑的存在理由是「k 要由人決定」。DP-Means 的群數由資料與 λ 決定，
    掃固定的七組 k 沒有意義。但**使用者仍該有得選**——所以改成把通過判準的掃描點
    依群數去重，每種群數一個候選（見 `build_candidates`）。選了會真的不同。

    ⚠ 每個候選帶**自己的 λ**：共用一個的話選哪個都一樣。finalize 依使用者選定的
    candidate_id 取回對應的 λ，不重新掃描。
    """
    selection = select_lambda(vectors, documents=documents)
    candidates = build_candidates(selection.sweep)
    if not candidates:
        # 全軍覆沒：仍要產出一個可用的候選，讓使用者看到結果再判斷。
        state = dpmeans.fit(vectors, lambda_=selection.value)
        candidates = [{
            "lambda": selection.value, "topic_count": len(state.centers),
            "coherence": None, "diversity": None, "balance": None,
            "small_topic_ratio": None, "score": None, "failed": ["fallback"],
        }]
    best = pick_best_candidate(candidates)
    compact = compact_sweep(selection.sweep,
                            chosen_lambda=best["lambda"] if best else None)
    return [
        {
            "candidate_type": CANDIDATE_TYPE_DPMEANS,
            # k 沿用既有 schema 欄位，值＝該候選的群數。⚠ 不是使用者選的 k，
            # 是這個 λ 產出的群數。
            "k": candidate["topic_count"],
            "topic_count": candidate["topic_count"],
            "coherence": candidate["coherence"],
            "diversity": candidate["diversity"],
            "balance": candidate["balance"],
            "small_topic_ratio": candidate["small_topic_ratio"],
            "score": candidate["score"],
            "elapsed_seconds": elapsed_seconds,
            "is_recommended": best is not None and candidate["lambda"] == best["lambda"],
            "parameters": {
                "lambda": candidate["lambda"],
                "lambda_method": selection.method,
                "lambda_version": selection.version,
                "lambda_sample_size": selection.sample_size,
                "sweep_points": selection.sweep_points,
                "sweep_converged": selection.converged,
                # ⚠ 掃描表只掛在推薦的候選上，不逐一複製——它描述的是整輪掃描，
                # 不是單一候選，複製 N 份純粹浪費空間。
                "lambda_sweep": compact if (
                    best is not None and candidate["lambda"] == best["lambda"]) else None,
            },
        }
        for candidate in candidates
    ]


# --------------------------------------------------------------------------
# 全量定案（finalize）
# --------------------------------------------------------------------------


def plan_finalize_topics(
    *,
    state: Any,
    vectors: list[list[float]],
    patent_ids: list[int],
    documents: list[str],
    source_field: str,
    run_id: int,
    representative_limit: int,
) -> list[dict[str, Any]]:
    """DP-Means 全量分群結果 → 正式 topics 清單。

    ⚠ 與 BERTopic finalize 的兩點差異：

    - **關鍵詞自行抽取**（class-TF-IDF，見 keywords 模組）。⚠ 關鍵詞仍不得進
      CLI payload——`ai_topic_label_runner` 有紅線黑名單擋著（給了關鍵字，LLM 會
      覆述關鍵詞而不是讀專利內容）。命名靠的是代表文檔。
    - **代表文檔改用「離中心最近的 N 篇」**。向量直接算得出來，語意上就是「最能
      代表這群的文件」，不需要 c-TF-IDF。
    """
    top_terms = keywords.extract_top_terms(
        documents, labels=state.labels, limit=TOP_TERMS_PER_TOPIC) if documents else {}
    topics: list[dict[str, Any]] = []
    for index, center in enumerate(state.centers):
        members = [i for i, label in enumerate(state.labels) if label == index]
        # 離中心最近的排前面——AI 命名只讀前幾篇，順序就是重要性。
        members.sort(key=lambda i: dpmeans.cosine_distance(
            dpmeans.l2_normalize(vectors[i]), center))
        position = index + 1
        topics.append({
            "topic_id": position,
            "topic_code": format_topic_code(position),
            "source_field": source_field,
            "created_run_id": run_id,
            "model_topic_ids": [index],
            "topic_kind": "model",
            "doc_count": len(members),
            "keywords": [{"term": term, "weight": 1.0}
                         for term in top_terms.get(index, [])],
            "representative_patent_ids": [patent_ids[i] for i in members[:representative_limit]],
            "label": f"主題 {format_topic_code(position)}",
            "label_source": "fallback",
            "display_order": position,
            "status": "active",
        })
    return topics


def plan_finalize_assignments(
    *,
    state: Any,
    vectors: list[list[float]],
    patent_ids: list[int],
) -> list[tuple[int, str, float]]:
    """DP-Means 分群結果 → assignment 三元組 (patent_id, topic_code, distance)。

    ⚠ 距離用 cosine，與分群同一把尺。混用歐氏距離會讓「離中心多遠」這個數字在
    報表與分群之間互相矛盾，而且看不出來。
    """
    rows: list[tuple[int, str, float]] = []
    for index, label in enumerate(state.labels):
        distance = dpmeans.cosine_distance(
            dpmeans.l2_normalize(vectors[index]), state.centers[label])
        rows.append((patent_ids[index], format_topic_code(label + 1), float(distance)))
    return rows


def _next_topic_position(existing_codes: list[str]) -> int:
    """下一個可用位置＝既有最大號 + 1。

    ⚠ 不填補空號：T002 被刪過就是被刪過。重用號碼會讓舊報表、舊簡報裡的 T002
    指到一個完全不同的主題，而且是事後才發現的那種錯。
    """
    positions = [
        int(code[len(TOPIC_CODE_PREFIX):])
        for code in existing_codes
        if code.startswith(TOPIC_CODE_PREFIX) and code[len(TOPIC_CODE_PREFIX):].isdigit()
    ]
    return (max(positions) + 1) if positions else 1
