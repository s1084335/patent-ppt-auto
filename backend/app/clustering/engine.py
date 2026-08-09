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

from dataclasses import dataclass, field
from typing import Any

from . import artifacts, dpmeans
from .artifacts import ALGORITHM_DPMEANS, ALGORITHM_KMEANS

#: 讀 feature flag 的環境變數名。⚠ 預設不設＝走舊引擎。
ALGORITHM_ENV = "CLUSTERING_ALGORITHM"

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
    requested_algorithm: str | None = None,  # noqa: ARG001 — 見下方說明
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
# 全量定案（finalize）
# --------------------------------------------------------------------------


def plan_finalize_topics(
    *,
    state: Any,
    vectors: list[list[float]],
    patent_ids: list[int],
    source_field: str,
    run_id: int,
    representative_limit: int,
) -> list[dict[str, Any]]:
    """DP-Means 全量分群結果 → 正式 topics 清單。

    ⚠ 與 BERTopic finalize 的兩點差異：

    - **不產關鍵詞**。DP-Means 沒有 c-TF-IDF。這不影響命名——`ai_topic_label_runner`
      有紅線黑名單明文禁止 keywords 進 CLI payload（給了關鍵字，LLM 會覆述關鍵詞
      而不是讀專利內容）。命名靠的是代表文檔。
    - **代表文檔改用「離中心最近的 N 篇」**。向量直接算得出來，語意上就是「最能
      代表這群的文件」，不需要 c-TF-IDF。
    """
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
            "keywords": [],
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
