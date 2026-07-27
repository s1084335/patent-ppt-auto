"""workspace 分群應用服務：候選、標籤、incremental、階層建議與人工合併。"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import json
import math
from typing import Any

import numpy as np
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.db.connection import get_connection_kwargs

from .artifacts import (
    artifact_key,
    artifact_path,
    load_artifact,
    resolve_artifact_path,
    save_artifact,
)
from .model import EmbeddingMatrix, ReducedEmbeddingMatrix, partial_fit_bertopic
from .runner import (
    CANDIDATE_REFERENCE_PARAMETER_KEY,
    ClusteringCorpus,
    load_clustering_corpus,
)
from .preprocessing import clean_patent_text, sha256_text
from .sources import get_source_spec, source_fields


LLM_REPRESENTATIVE_DOC_LIMIT = 15

# DB 保留較多代表專利供追蹤；正式 topic 標籤/摘要階段才給 LLM 前 5 筆全文。
TOPIC_LABELING_DOC_LIMIT = 5

# LLM 產出的建議字數（寫進 instruction）與 apply 端硬上限（2 倍建議上限）。
# 超過硬上限視為 LLM 未遵循指示，直接 raise 讓呼叫端重生，不靜默截斷。
LABEL_SUGGESTED_RANGE = "4 到 8"
# summary 2026-07-27 由「20 到 40／上限 80」放寬到「40 到 50／上限 100」（使用者定）：
# 實測產出貼著建議上緣（技術平均 40.1 字、功效 36.3 字），40 字講不完主題重點。
# 硬上限維持「2 倍建議上限」的比例，留足餘裕不致頻繁拒收。
# 技術與功效共用這組常數（topic_labeling_payload 不分通道），改一處兩邊生效。
SUMMARY_SUGGESTED_RANGE = "40 到 50"
EXPLANATION_SUGGESTED_RANGE = "25 到 40"
LABEL_MAX_CHARS = 16
SUMMARY_MAX_CHARS = 100
EXPLANATION_MAX_CHARS = 80

# 候選說明的口徑，對齊 decisions.md「2026-07-17 分群主題數候選說明原則」：
# 分數只作排序輔助，不得當說服依據；要讓使用者理解各方案的「意義」
# （切分程度、穩定性、風險），提到分數低時一律翻成語意原因。
# ⚠ 不寫死候選組數——組數由 top_level_k_values() 依資料量決定
#   （100–199 筆只掃 k=(10,15)＝兩組），寫死「三組」會誤導 LLM。
CANDIDATE_EXPLANATION_INSTRUCTION = (
    "請依各組候選的 coherence、diversity、balance、score、k 與資料量，"
    "用一般使用者看得懂的方式說明各候選主題數的取捨。"
    "重點放在每組代表的意義：主題切分的粗細程度、結果的穩定性，"
    "以及選它可能有的風險。"
    "注意：不要把小數分數當主內容、不要用分數高低當說服依據；"
    "需要提到分數差異時，一律翻成語意原因，"
    "例如主題一致性下降、小主題比例偏高、切分過細、各群較籠統混雜。"
    f"每組 explanation 建議 {EXPLANATION_SUGGESTED_RANGE} 字，"
    f"不得超過 {EXPLANATION_MAX_CHARS} 字。"
    "不要要求或引用代表文檔，回傳 explanations 陣列，"
    "每筆包含 candidate_id 與 explanation，"
    "供系統寫回 topic_state_json->'candidates' 的 llm_explanation。"
)


# 本路徑只允許 AI 產出（llm）與程式後備（fallback）；manual 僅能由
# 前端 rename endpoint 寫入，避免 AI 通道把標籤自我升級成人工定案。
APPLY_LABEL_SOURCES = ("llm", "fallback")


def _workspace_patent_ids(cur: Any, workspace_id: int) -> list[int]:
    """讀 workspace 成員（0021：workspaces.patent_ids_json，workspace_patents 已刪）。"""
    cur.execute(
        "SELECT patent_ids_json FROM app_layer.workspaces WHERE workspace_id = %s",
        (workspace_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"workspace not found: {workspace_id}")
    value = row[0] if not isinstance(row, dict) else row["patent_ids_json"]
    return [int(item) for item in (value or [])]


def _latest_state_run(
    cur: Any, *, workspace_id: int, source_field: str, require_topics: bool = True
) -> dict[str, Any] | None:
    """取該 workspace/通道最新的 topic run（0021：歸屬經 workflow_runs JOIN）。

    require_topics=True 時只取 topic_state_json->'topics' 非空者，與
    PostgresTopicStateRepository 選 state run 的規則同源（incremental run 不帶 topics）。
    """
    topics_filter = (
        "AND jsonb_array_length(COALESCE(tr.topic_state_json->'topics', '[]'::jsonb)) > 0"
        if require_topics
        else ""
    )
    cur.execute(
        f"""
        SELECT tr.run_id, tr.workflow_run_id, tr.previous_run_id, tr.source_field,
               tr.topic_state_json, tr.artifact_key, wr.workspace_id, wr.status
        FROM derived_layer.topic_runs tr
        JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
        WHERE wr.workspace_id = %s AND tr.source_field = %s
          {topics_filter}
        ORDER BY tr.run_id DESC
        LIMIT 1
        """,
        (workspace_id, source_field),
    )
    row = cur.fetchone()
    return dict(row) if row is not None else None


def _require_latest_state_run(
    cur: Any, *, workspace_id: int, source_field: str
) -> dict[str, Any]:
    """同 _latest_state_run，但查無已定案主題時直接 raise，不回半套狀態。"""
    row = _latest_state_run(cur, workspace_id=workspace_id, source_field=source_field)
    if row is None:
        raise ValueError(
            f"workspace {workspace_id} / {source_field} has no finalized topic run")
    return row


def _state_topics(run_row: dict[str, Any]) -> list[dict[str, Any]]:
    """取 run 的正式主題清單（topic_state_json->'topics'）。"""
    return list((dict(run_row.get("topic_state_json") or {})).get("topics") or [])


def _active_topic_keys(topics: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """以 topic_code 索引 active 主題；topic_code 即 assignments 的 topic_key。"""
    return {t["topic_code"]: t for t in topics if t.get("status", "active") == "active"}


def _resolve_active_code(topics: list[dict[str, Any]], code: str) -> str:
    """沿 merged_into_topic_id 鏈找 active 目標的 topic_code（與 repository 同一解法）。"""
    by_id = {t.get("topic_id"): t for t in topics}
    by_code = {t.get("topic_code"): t for t in topics}
    seen: set[int] = set()
    topic = by_code.get(code)
    while topic is not None and topic.get("status") == "merged":
        target_id = topic.get("merged_into_topic_id")
        if target_id in seen or target_id not in by_id:
            break
        seen.add(target_id)
        topic = by_id[target_id]
    return topic.get("topic_code", code) if topic is not None else code


def _merge_topic_state(cur: Any, run_id: int, patch: dict[str, Any]) -> None:
    """就地合併 topic_state_json（jsonb ||），不覆蓋既有其他鍵（與 runner 同一寫法）。"""
    cur.execute(
        "UPDATE derived_layer.topic_runs SET topic_state_json = topic_state_json || %s "
        "WHERE run_id = %s",
        (Jsonb(patch), run_id),
    )


def _latest_assignments(
    cur: Any, *, workspace_id: int, source_field: str
) -> list[tuple[int, str, float | None]]:
    """取每個 patent 最新一筆指派（DISTINCT ON，與 repository 讀取語意同源）。"""
    cur.execute(
        """
        SELECT DISTINCT ON (ta.patent_id)
               ta.patent_id, ta.topic_key, ta.distance_to_centroid
        FROM derived_layer.topic_assignments ta
        JOIN derived_layer.topic_runs tr ON tr.run_id = ta.run_id
        JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
        WHERE wr.workspace_id = %s AND tr.source_field = %s
        ORDER BY ta.patent_id, ta.run_id DESC
        """,
        (workspace_id, source_field),
    )
    rows = cur.fetchall()
    if rows and isinstance(rows[0], dict):
        return [
            (int(r["patent_id"]), str(r["topic_key"]), r["distance_to_centroid"])
            for r in rows
        ]
    return [(int(r[0]), str(r[1]), r[2]) for r in rows]


@dataclass(frozen=True)
class IncrementalSummary:
    """回報單一 workspace 通道 incremental 更新結果。"""

    run_id: int | None
    workspace_id: int
    source_field: str
    new_document_count: int
    assignment_count: int
    artifact_version: int
    pca_updated: bool
    status: str


@dataclass(frozen=True)
class MergeSummary:
    """回報人工合併後的新永久主題與新版 artifact（0021：主題以 topic_code 識別）。"""

    run_id: int
    workspace_id: int
    source_field: str
    source_topic_keys: list[str]
    merged_topic_code: str
    artifact_version: int
    status: str


@dataclass(frozen=True)
class UnmergeSummary:
    """回報依 merge run 復原後的來源主題與新版 artifact（0021：以 topic_code 識別）。"""

    run_id: int
    workspace_id: int
    source_field: str
    target_merge_run_id: int
    restored_topic_keys: list[str]
    reverted_topic_code: str
    artifact_version: int
    status: str


def create_workspace(
    *,
    workspace_name: str,
    patent_ids: list[int],
    created_by: str,
    description: str | None = None,
) -> int:
    """建立 workspace 並加入明確專利集合；不複製或修改核心專利值。

    0021：app_layer.workspace_patents 已刪，成員直接寫 workspaces.patent_ids_json，
    一次 INSERT 完成，不再有成員關聯表；description／created_by／parameters_json
    等舊欄也已併入 settings_json（與 app_layer/workspace_create.py 同一寫法）。
    """
    unique_patent_ids = list(dict.fromkeys(int(value) for value in patent_ids))
    if not unique_patent_ids:
        raise ValueError("workspace requires at least one patent")
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_layer.workspaces (
                    workspace_name, patent_ids_json, settings_json
                ) VALUES (%s, %s, jsonb_strip_nulls(%s::jsonb))
                RETURNING workspace_id
                """,
                (
                    workspace_name.strip(),
                    Jsonb(unique_patent_ids),
                    Jsonb(
                        {
                            "description": description,
                            "created_by": created_by,
                            "clustering_sources": list(source_fields()),
                        }
                    ),
                ),
            )
            workspace_id = int(cur.fetchone()[0])
    return workspace_id


def add_workspace_patents(
    *,
    workspace_id: int,
    patent_ids: list[int],
    added_by: str,
) -> dict[str, int]:
    """把新專利加入既有 workspace，後續由雙通道 incremental API 接手。

    0021：成員落 workspaces.patent_ids_json。以單一 UPDATE 併集後回寫（保留既有
    順序、去重），取代舊 workspace_patents 的 ON CONFLICT DO NOTHING；
    added_by 在併表後無欄位可存，僅保留參數相容不寫入。

    護欄（2026-07-23）：全庫 workspace 成員只由匯入自動同步，此手動路徑一律擋下。
    """
    from backend.app.app_layer import global_workspace

    global_workspace.assert_not_global(workspace_id, action="add patents to")
    unique_patent_ids = list(dict.fromkeys(int(value) for value in patent_ids))
    if not unique_patent_ids:
        raise ValueError("at least one patent_id is required")
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM core_layer.patents WHERE id = ANY(%s)",
                (unique_patent_ids,),
            )
            existing_count = int(cur.fetchone()[0])
            if existing_count != len(unique_patent_ids):
                raise ValueError("one or more patent_ids do not exist in core_layer.patents")
            # 既有成員在前、新成員照傳入順序接在後，重複者只留第一次出現
            cur.execute(
                """
                UPDATE app_layer.workspaces w
                SET patent_ids_json = (
                    SELECT COALESCE(jsonb_agg(pid ORDER BY ord), '[]'::jsonb)
                    FROM (
                        SELECT DISTINCT ON (pid) pid, ord
                        FROM (
                            SELECT (value)::bigint AS pid, ordinality AS ord
                            FROM jsonb_array_elements_text(w.patent_ids_json)
                                 WITH ORDINALITY AS existing(value, ordinality)
                            UNION ALL
                            SELECT incoming.pid,
                                   jsonb_array_length(w.patent_ids_json) + incoming.ord
                            FROM unnest(%s::bigint[]) WITH ORDINALITY AS incoming(pid, ord)
                        ) merged
                        ORDER BY pid, ord
                    ) deduped
                )
                WHERE w.workspace_id = %s
                RETURNING jsonb_array_length(w.patent_ids_json)
                """,
                (unique_patent_ids, workspace_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"workspace not found: {workspace_id}")
            workspace_count = int(row[0])
    return {
        "requested_count": len(unique_patent_ids),
        "workspace_patent_count": workspace_count,
    }


def demo_patent_ids(limit: int = 200) -> list[int]:
    """挑選技術、功效文本及兩種向量都齊全的專利，供臨時頁面驗證。"""
    if limit < 50:
        raise ValueError("demo workspace requires at least 50 patents")
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT p.id
                FROM core_layer.patents p
                JOIN core_layer.patent_technical_embeddings te ON te.patent_id = p.id
                JOIN core_layer.patent_effect_embeddings ee ON ee.patent_id = p.id
                WHERE NULLIF(BTRIM(p."獨立項[KR,JP,US,CN,EP,IN]"), '') IS NOT NULL
                  AND NULLIF(BTRIM(p."效果 摘要[US,EP,PCT,JP,KR,CN,TW]"), '') IS NOT NULL
                ORDER BY p.id
                LIMIT %s
                """,
                (limit,),
            )
            return [int(row[0]) for row in cur.fetchall()]


def candidate_review_payload(run_id: int) -> dict[str, Any]:
    """輸出候選主題數的指標解釋 payload，不展開代表文檔全文。

    主題數選擇階段以 coherence / diversity / balance / score 為主，
    Claude CLI 只協助說明三組候選的取捨；代表文檔保留到
    finalize 後的 topic_labeling_payload 使用。
    """
    # 0021：候選落 topic_state_json->'candidates'（derived_layer.topic_candidates 已刪）；
    # workspace_id 只能經 workflow_runs JOIN 取得（topic_runs 已無此欄）。
    from .runner import load_run_scope

    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        run = load_run_scope(run_id, connection=conn)
        state = dict(run.get("topic_state_json") or {})
        rows = sorted(
            (dict(item) for item in (state.get("candidates") or [])),
            key=lambda item: int(item["candidate_k"]),
        )
        spec = get_source_spec(str(run["source_field"]))

    candidate_payloads: list[dict[str, Any]] = []
    for row in rows:
        # 0021：候選參數改叫 parameters（JSON 內鍵名，與 _persist_calibration 同源）。
        parameters = dict(row.get("parameters") or {})
        # DB 仍保存 c-TF-IDF refs 供後續追蹤，但候選主題數說明不把 refs 或全文交給 LLM。
        parameters.pop(CANDIDATE_REFERENCE_PARAMETER_KEY, None)
        candidate_payloads.append(
            {
                "candidate_id": int(row["candidate_id"]),
                "candidate_type": row["candidate_type"],
                "k": int(row["candidate_k"]),
                "coherence": float(row["coherence"]),
                "diversity": float(row["diversity"]),
                "balance": float(row["balance"]),
                "score": float(row["score"]),
                "parameters": parameters,
                "existing_explanation": row.get("llm_explanation"),
            }
        )

    return {
        "run_id": int(run["run_id"]),
        "workspace_id": int(run["workspace_id"]) if run["workspace_id"] is not None else None,
        "source_field": str(run["source_field"]),
        "source_label": spec.label_zh,
        # 0021：input_doc_count 併入 topic_state_json，不再是 topic_runs 欄位
        "document_count": int(state.get("input_doc_count") or 0),
        "instruction": CANDIDATE_EXPLANATION_INSTRUCTION,
        "candidates": candidate_payloads,
    }


def apply_candidate_explanations(
    *,
    run_id: int,
    explanations: list[dict[str, Any]],
) -> dict[str, int]:
    """把 Claude Code 產生的候選方案說明寫回 topic_state_json->'candidates' 的 llm_explanation。

    只保存說明文字，不代使用者選定候選方案；候選定案仍由使用者透過
    finalize_top_level 指定 candidate_id。空白說明、缺 candidate_id 或超過
    硬上限一律 raise（與 API 端 pydantic 驗證同一口徑），不靜默跳過；
    回傳 requested_count 與 updated_count，兩者不一致代表有 candidate_id
    不屬於此 run。

    0021：候選在 JSON 陣列內，無法逐筆 UPDATE 列；改為讀出整份 candidates、
    在 Python 套用說明後一次寫回，避免 N 次往返。
    """
    if not explanations:
        raise ValueError("explanations must not be empty")

    explanation_by_id: dict[int, str] = {}
    for item in explanations:
        if item.get("candidate_id") is None:
            raise ValueError("each explanation requires candidate_id")
        candidate_id = int(item["candidate_id"])
        explanation = str(item.get("explanation") or item.get("llm_explanation") or "").strip()
        if not explanation:
            raise ValueError(f"candidate {candidate_id} explanation must not be empty")
        if len(explanation) > EXPLANATION_MAX_CHARS:
            raise ValueError(
                f"candidate {candidate_id} explanation exceeds {EXPLANATION_MAX_CHARS} chars"
            )
        explanation_by_id[candidate_id] = explanation

    updated_count = 0
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT topic_state_json FROM derived_layer.topic_runs WHERE run_id = %s",
                (run_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"topic run not found: {run_id}")
            state = dict(row[0] or {})
            candidates = [dict(item) for item in (state.get("candidates") or [])]
            for candidate in candidates:
                explanation = explanation_by_id.get(int(candidate["candidate_id"]))
                if explanation is not None:
                    candidate["llm_explanation"] = explanation
                    updated_count += 1
            _merge_topic_state(cur, run_id, {"candidates": candidates})
    return {"requested_count": len(explanation_by_id), "updated_count": updated_count}

def topic_labeling_payload(
    *,
    workspace_id: int,
    source_field: str,
    topic_keys: list[str] | None = None,
) -> dict[str, Any]:
    """輸出每個 topic 的代表文件，供 Claude Code 產生標籤與短摘要。

    Payload 刻意不輸出 keywords，避免 LLM 被停用詞、c-TF-IDF 或 ngram 切法帶偏。
    前端仍可另外讀 topic_state_json->'topics' 的 keywords，供人工掃描 topic 用。

    0021：主題落 topic_state_json->'topics'，識別碼改用 topic_code（＝assignments
    的 topic_key），run 內遞增的 topic_id 只在同一份 state 內有意義。
    """
    spec = get_source_spec(source_field)
    selected_codes = set(topic_keys or ())

    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            run = _require_latest_state_run(
                cur, workspace_id=workspace_id, source_field=source_field)
            topics = [
                topic
                for topic in _state_topics(run)
                if topic.get("status", "active") == "active"
                and topic.get("topic_kind", "model") == "model"
                and (not selected_codes or topic["topic_code"] in selected_codes)
            ]
            topics.sort(key=lambda t: t.get("display_order", 0))

            payload_topics: list[dict[str, Any]] = []
            for topic in topics:
                patent_ids = [
                    int(value)
                    for value in (topic.get("representative_patent_ids") or [])
                ][:TOPIC_LABELING_DOC_LIMIT]
                excerpts = _fetch_source_excerpts(cur, spec.source_column, patent_ids)
                payload_topics.append(
                    {
                        "topic_code": str(topic["topic_code"]),
                        "current_label_source": topic.get("label_source"),
                        "representative_patents": excerpts,
                    }
                )

    return {
        "workspace_id": workspace_id,
        "source_field": source_field,
        "source_label": spec.label_zh,
        "run_id": int(run["run_id"]),
        "instruction": (
            f"請只根據每個 topic 的前 {TOPIC_LABELING_DOC_LIMIT} 筆代表性專利文件產生 "
            "topic_code、label、summary；不要依賴 keywords。"
            f"label 建議 {LABEL_SUGGESTED_RANGE} 個中文字（硬上限 {LABEL_MAX_CHARS} 字），"
            f"summary 建議 {SUMMARY_SUGGESTED_RANGE} 個中文字（硬上限 {SUMMARY_MAX_CHARS} 字），"
            f"超過硬上限會被拒收。{spec.naming_hint}"
        ),
        "topics": payload_topics,
    }


def apply_topic_labels(
    *,
    workspace_id: int,
    source_field: str,
    labels: list[dict[str, Any]],
    updated_by: str = "claude-cli",
) -> dict[str, int]:
    """寫入 Claude CLI 或批次流程產出的 topic label/summary。

    source 只接受 llm/fallback（預設 llm，符合 0010 topics_label_source_check）；
    manual 只能走前端 rename endpoint。label/summary 超過硬上限直接 raise，
    要求 LLM 重生，不靜默截斷。

    0021：主題在 topic_state_json->'topics'，改以 topic_code 指定；讀出整份 topics、
    在 Python 套用後一次寫回最新 state run，label_source='manual' 的主題一律跳過
    （AI 通道不得把標籤自我升級成人工定案）。
    """
    if not labels:
        return {"updated_count": 0}

    patch_by_code: dict[str, dict[str, Any]] = {}
    for item in labels:
        topic_code = str(item["topic_code"])
        label = str(item["label"]).strip()
        summary = str(item.get("summary") or "").strip()
        source = str(item.get("source") or "llm")
        if not label:
            raise ValueError(f"topic {topic_code} label must not be empty")
        if len(label) > LABEL_MAX_CHARS:
            raise ValueError(
                f"topic {topic_code} label exceeds {LABEL_MAX_CHARS} chars"
            )
        if len(summary) > SUMMARY_MAX_CHARS:
            raise ValueError(
                f"topic {topic_code} summary exceeds {SUMMARY_MAX_CHARS} chars"
            )
        if source not in APPLY_LABEL_SOURCES:
            raise ValueError(
                f"topic {topic_code} source must be one of {APPLY_LABEL_SOURCES}"
            )
        patch_by_code[topic_code] = {
            "label": label,
            "summary": summary,
            "label_source": source,
        }

    updated_count = 0
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            run = _require_latest_state_run(
                cur, workspace_id=workspace_id, source_field=source_field)
            topics = [dict(topic) for topic in _state_topics(run)]
            for topic in topics:
                patch = patch_by_code.get(str(topic.get("topic_code")))
                if patch is None:
                    continue
                # guard：只改 active model 主題，且不覆蓋人工定案的標籤
                if topic.get("status", "active") != "active":
                    continue
                if topic.get("topic_kind", "model") != "model":
                    continue
                if topic.get("label_source") == "manual":
                    continue
                topic.update(patch)
                metadata = dict(topic.get("label_metadata") or {})
                metadata["updated_by"] = updated_by
                topic["label_metadata"] = metadata
                updated_count += 1
            if updated_count:
                _merge_topic_state(cur, int(run["run_id"]), {"topics": topics})
    return {"updated_count": int(updated_count)}


def backfill_representative_patents(
    *,
    workspace_id: int,
    source_field: str,
) -> dict[str, int]:
    """把舊 run 產生、少於目前上限的代表專利清單補到 15 筆。

    舊 finalize 只存 5 筆代表專利，與新 instruction 的「前 15 筆」不一致。
    這裡依最新 assignment 的 distance_to_centroid 由小到大重取前
    LLM_REPRESENTATIVE_DOC_LIMIT 筆（合併鏈解析到 active topic_code），只更新
    active model topics 的 representative_patent_ids，不動 assignment
    與 label；已達上限的 topic 不重寫。

    0021：主題與代表專利都在 topic_state_json->'topics'，指派在
    derived_layer.topic_assignments（topic_key＝topic_code）。
    """
    get_source_spec(source_field)
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            run = _require_latest_state_run(
                cur, workspace_id=workspace_id, source_field=source_field)
            topics = [dict(topic) for topic in _state_topics(run)]
            assignment_rows = _latest_assignments(
                cur, workspace_id=workspace_id, source_field=source_field)

            # 依 active topic_code 聚集 (distance, patent_id)，distance 缺值排最後。
            ranked_by_code: dict[str, list[tuple[float, int]]] = {}
            for patent_id, topic_key, distance_value in assignment_rows:
                code = _resolve_active_code(topics, topic_key)
                distance = float(distance_value) if distance_value is not None else math.inf
                ranked_by_code.setdefault(code, []).append((distance, patent_id))

            topic_count = 0
            updated_count = 0
            for topic in topics:
                if topic.get("status", "active") != "active":
                    continue
                if topic.get("topic_kind", "model") != "model":
                    continue
                topic_count += 1
                existing = [
                    int(value) for value in (topic.get("representative_patent_ids") or [])
                ]
                if len(existing) >= LLM_REPRESENTATIVE_DOC_LIMIT:
                    continue
                ranked = sorted(ranked_by_code.get(str(topic["topic_code"]), []))
                selected = [
                    patent_id for _, patent_id in ranked[:LLM_REPRESENTATIVE_DOC_LIMIT]
                ]
                if not selected or selected == existing:
                    continue
                topic["representative_patent_ids"] = selected
                updated_count += 1

            if updated_count:
                _merge_topic_state(cur, int(run["run_id"]), {"topics": topics})
    return {"topic_count": topic_count, "updated_count": updated_count}


def incremental_workspace(
    *,
    workspace_id: int,
    source_field: str,
) -> IncrementalSummary:
    """只處理尚無 assignment 的 workspace 新專利，更新既有 online artifact。"""
    latest = _latest_completed_run(workspace_id=workspace_id, source_field=source_field)
    artifact = load_artifact(
        resolve_artifact_path(str(latest["model_artifact_path"])),
        expected_hash=str(latest["model_artifact_hash"]),
    )
    with psycopg.connect(**get_connection_kwargs()) as conn:
        corpus = load_clustering_corpus(conn, workspace_id=workspace_id, source_field=source_field)
        with conn.cursor() as cur:
            # 0021：已指派的專利由 topic_assignments 經 run JOIN workflow_runs 取得
            assigned = {
                patent_id
                for patent_id, _key, _distance in _latest_assignments(
                    cur, workspace_id=workspace_id, source_field=source_field)
            }
    indexes = [index for index, patent_id in enumerate(corpus.patent_ids) if patent_id not in assigned]
    if not indexes:
        return IncrementalSummary(
            run_id=None,
            workspace_id=workspace_id,
            source_field=source_field,
            new_document_count=0,
            assignment_count=0,
            artifact_version=int(latest["artifact_version"]),
            pca_updated=False,
            status="no_new_documents",
        )

    batch = _subset_corpus(corpus, indexes)
    run_id = _create_incremental_run(latest=latest, new_document_count=len(indexes))
    values = np.asarray(batch.matrix.vectors, dtype=float)
    pca_updated = len(values) >= int(getattr(artifact.reducer, "n_components_", 100))
    if pca_updated:
        artifact.reducer.partial_fit(values)
    reduced_values = artifact.reducer.transform(values)
    reduced = ReducedEmbeddingMatrix(
        row_numbers=batch.matrix.row_numbers,
        patent_numbers=batch.matrix.patent_numbers,
        vectors=reduced_values.tolist(),
        reducer="IncrementalPCA",
        n_components=int(reduced_values.shape[1]),
    )

    try:
        predicted_topics = partial_fit_bertopic(artifact.topic_model, batch.documents, reduced)
        assignment_count = _persist_incremental_assignments(
            run_id=run_id,
            workspace_id=workspace_id,
            source_field=source_field,
            corpus=batch,
            reduced=reduced,
            predicted_topics=predicted_topics,
        )
        artifact.run_id = run_id
        artifact.artifact_version = int(latest["artifact_version"]) + 1
        next_key = artifact_key(
            workspace_id=workspace_id,
            source_field=source_field,
            run_id=run_id,
        )
        next_path = artifact_path(
            workspace_id=workspace_id,
            source_field=source_field,
            run_id=run_id,
        )
        next_hash = save_artifact(artifact, next_path)
        _complete_incremental_run(
            run_id=run_id,
            artifact_key_value=next_key,
            artifact_hash=next_hash,
            artifact_version=artifact.artifact_version,
            pca_updated=pca_updated,
        )
        refresh_topic_counts(workspace_id=workspace_id, source_field=source_field)
    except Exception as exc:
        _fail_run(run_id, exc)
        raise

    return IncrementalSummary(
        run_id=run_id,
        workspace_id=workspace_id,
        source_field=source_field,
        new_document_count=len(indexes),
        assignment_count=assignment_count,
        artifact_version=artifact.artifact_version,
        pca_updated=pca_updated,
        status="completed",
    )


def hierarchy_merge_suggestions(
    *,
    workspace_id: int,
    source_field: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """使用 BERTopic 官方 hierarchical_topics，僅回傳可由使用者判斷的相近主題組。"""
    latest = _latest_completed_run(workspace_id=workspace_id, source_field=source_field)
    artifact = load_artifact(
        resolve_artifact_path(str(latest["model_artifact_path"])),
        expected_hash=str(latest["model_artifact_hash"]),
    )
    with psycopg.connect(**get_connection_kwargs()) as conn:
        corpus = load_clustering_corpus(conn, workspace_id=workspace_id, source_field=source_field)
    reduced = artifact.reducer.transform(np.asarray(corpus.matrix.vectors, dtype=float))
    predictions, _ = artifact.topic_model.transform(corpus.documents, embeddings=reduced)
    artifact.topic_model.topics_ = [int(value) for value in predictions]
    hierarchy = artifact.topic_model.hierarchical_topics(corpus.documents)

    active = _active_model_topics(workspace_id=workspace_id, source_field=source_field)
    model_to_db = {
        int(model_topic_id): row
        for row in active
        for model_topic_id in (row.get("model_topic_ids") or [])
    }
    suggestions: list[dict[str, Any]] = []
    for row in hierarchy.sort_values("Distance").to_dict(orient="records"):
        model_ids = _hierarchy_model_ids(row.get("Topics"))
        # 0021：主題以 topic_code 去重與識別
        db_topics = {
            str(model_to_db[value]["topic_code"]): model_to_db[value]
            for value in model_ids
            if value in model_to_db
        }
        if len(db_topics) != 2:
            continue
        pair = list(db_topics.values())
        suggestions.append(
            {
                "topic_keys": [str(item["topic_code"]) for item in pair],
                "labels": [str(item["label"] or item["topic_code"]) for item in pair],
                "distance": float(row["Distance"]),
            }
        )
        if len(suggestions) >= limit:
            break
    return suggestions


def merge_workspace_topics(
    *,
    workspace_id: int,
    source_field: str,
    topic_keys: list[str],
    merged_by: str,
    label: str | None = None,
) -> MergeSummary:
    """純結構 JSON 操作完成主題合併：目標吸收來源指派、來源標 merged、產新 run。

    0021：主題以 topic_code 識別（＝assignments 的 topic_key）；合併結果寫新 run，
    不覆蓋前一版。無需重新載入或執行 BERTopic 模型。

    topic_keys 順序即語意：**第一個是目標（吸收方，維持 active）、第二個是來源
    （被合併方，標 status='merged' 並記 merged_into_topic_id）**。
    """
    selected_codes = list(dict.fromkeys(str(value) for value in topic_keys))
    if len(selected_codes) != 2:
        raise ValueError("exactly two active topics are required for a merge")
    target_code, source_code = selected_codes
    _load_merge_topics(
        workspace_id=workspace_id,
        source_field=source_field,
        topic_keys=selected_codes,
    )
    latest = _latest_completed_run(workspace_id=workspace_id, source_field=source_field)
    artifact_version = int(latest["artifact_version"]) + 1

    run_id = _create_merge_run(
        latest=latest,
        target_topic_key=target_code,
        source_topic_keys=[source_code],
        merged_by=merged_by,
    )
    try:
        _persist_topic_merge(
            run_id=run_id,
            workspace_id=workspace_id,
            source_field=source_field,
            previous_run_id=int(latest["run_id"]),
            target_code=target_code,
            source_codes=[source_code],
            merged_by=merged_by,
            label=label,
            artifact_version=artifact_version,
        )
        refresh_topic_counts(workspace_id=workspace_id, source_field=source_field)
    except Exception as exc:
        _fail_run(run_id, exc)
        raise

    return MergeSummary(
        run_id=run_id,
        workspace_id=workspace_id,
        source_field=source_field,
        source_topic_keys=[source_code],
        merged_topic_code=target_code,
        artifact_version=artifact_version,
        status="completed",
    )


def merge_history(*, workspace_id: int, source_field: str) -> list[dict[str, Any]]:
    """列出每筆完成的 merge、來源 topics、結果 topic 與目前可否獨立復原。

    0021：不另建 merge 紀錄表；歷史由 run 鏈（previous_run_id）＋各 run 的
    topic_state_json（run_mode／parameters／status）組出。
    """
    get_source_spec(source_field)
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT tr.run_id, tr.previous_run_id, tr.topic_state_json
            FROM derived_layer.topic_runs tr
            JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
            WHERE wr.workspace_id = %s AND tr.source_field = %s
            ORDER BY tr.run_id
            """,
            (workspace_id, source_field),
        ).fetchall()

    completed_runs = [
        {"run_id": int(row["run_id"]), **dict(row["topic_state_json"] or {})}
        for row in rows
        if dict(row["topic_state_json"] or {}).get("status") == "completed"
    ]
    history: list[dict[str, Any]] = []
    for run in completed_runs:
        if run.get("run_mode") != "merge":
            continue
        parameters = dict(run.get("parameters") or {})
        result_code = str(run.get("merged_topic_code") or f"M{run['run_id']:05d}")
        blocked_reason = _unmerge_blocked_reason(
            merge_run=run,
            result_topic_code=result_code,
            completed_runs=completed_runs,
        )
        history.append(
            {
                "merge_run_id": int(run["run_id"]),
                "artifact_version": int(run.get("artifact_version") or 0),
                "merged_by": parameters.get("merged_by"),
                "source_topics": list(parameters.get("source_topic_keys") or []),
                "result_topic": result_code,
                "is_reverted": run.get("reverted_at") is not None,
                "reverted_at": run.get("reverted_at"),
                "reverted_by": run.get("reverted_by"),
                "can_unmerge": blocked_reason is None,
                "blocked_reason": blocked_reason,
            }
        )
    history.sort(key=lambda item: item["merge_run_id"], reverse=True)
    return history


def unmerge_workspace_topics(
    *,
    workspace_id: int,
    source_field: str,
    merge_run_id: int,
    reverted_by: str,
) -> UnmergeSummary:
    """獨立復原指定 merge 記錄：從前一版重播其餘 merge 鏈（純 JSON 轉換）。"""
    history = merge_history(workspace_id=workspace_id, source_field=source_field)
    target = next((item for item in history if item["merge_run_id"] == merge_run_id), None)
    if target is None:
        raise ValueError(f"completed merge run not found: {merge_run_id}")
    if not target["can_unmerge"]:
        raise ValueError(str(target["blocked_reason"] or "merge run cannot be restored"))

    restored_topic_keys = [str(code) for code in target["source_topics"]]
    reverted_topic_code = str(target["result_topic"])
    latest = _latest_completed_run(workspace_id=workspace_id, source_field=source_field)
    artifact_version = int(latest["artifact_version"]) + 1

    run_id = _create_unmerge_run(
        latest=latest,
        target_merge_run_id=merge_run_id,
        restored_topic_keys=restored_topic_keys,
        reverted_topic_code=reverted_topic_code,
        reverted_by=reverted_by,
    )
    try:
        _persist_unmerge(
            run_id=run_id,
            workspace_id=workspace_id,
            source_field=source_field,
            previous_run_id=int(latest["run_id"]),
            target_merge_run_id=merge_run_id,
            restored_topic_keys=restored_topic_keys,
            reverted_topic_code=reverted_topic_code,
            reverted_by=reverted_by,
            artifact_version=artifact_version,
        )
        refresh_topic_counts(workspace_id=workspace_id, source_field=source_field)
    except Exception as exc:
        _fail_run(run_id, exc)
        raise

    return UnmergeSummary(
        run_id=run_id,
        workspace_id=workspace_id,
        source_field=source_field,
        target_merge_run_id=merge_run_id,
        restored_topic_keys=restored_topic_keys,
        reverted_topic_code=reverted_topic_code,
        artifact_version=artifact_version,
        status="completed",
    )


def workspace_dashboard(workspace_id: int) -> dict[str, Any]:
    """輸出臨時前端所需的 workspace、雙通道 chips 與專利列表。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM app_layer.workspaces WHERE workspace_id = %s", (workspace_id,))
            workspace = cur.fetchone()
            if workspace is None:
                raise ValueError(f"workspace not found: {workspace_id}")
            topic_rows = _dashboard_topics(cur, workspace_id)
            # 0021：成員直接來自 workspaces.patent_ids_json，無 workspace_patents 可 JOIN
            member_ids = [int(value) for value in (workspace["patent_ids_json"] or [])]
            cur.execute(
                """
                SELECT
                    p.id AS patent_id,
                    COALESCE(
                        NULLIF(BTRIM(p."授權公告號"), ''),
                        NULLIF(BTRIM(p."審查的公告號"), ''),
                        NULLIF(BTRIM(p."未審查的公開號(轉換後)"), ''),
                        NULLIF(BTRIM(p."未審查的公開號"), ''),
                        NULLIF(BTRIM(p."申請號(轉換後)"), ''),
                        NULLIF(BTRIM(p."申請號"), '')
                    ) AS patent_number,
                    p.title,
                    p.country_code
                FROM core_layer.patents p
                WHERE p.id = ANY(%s)
                ORDER BY p.id
                """,
                (member_ids,),
            )
            patents = [dict(row) for row in cur.fetchall()]

    assignments = {
        source_field: _resolved_topic_by_patent(workspace_id=workspace_id, source_field=source_field)
        for source_field in source_fields()
    }
    # 0021：主題以 topic_code 識別，label 由各通道最新 state 提供
    labels = {
        (str(row["source_field"]), str(row["topic_code"])): row["label"]
        for row in topic_rows
    }
    for patent in patents:
        patent_id = int(patent["patent_id"])
        technical_code = assignments["wips_independent_claims"].get(patent_id)
        effect_code = assignments["effect_summary"].get(patent_id)
        patent["technical_topic_code"] = technical_code
        patent["technical_topic"] = labels.get(
            ("wips_independent_claims", technical_code), "未分類")
        patent["effect_topic_code"] = effect_code
        patent["effect_topic"] = labels.get(("effect_summary", effect_code), "未分類")

    return {
        "workspace": dict(workspace),
        "sources": [
            {
                "source_field": source_field,
                "label": get_source_spec(source_field).label_zh,
                "topics": [row for row in topic_rows if row["source_field"] == source_field],
            }
            for source_field in source_fields()
        ],
        "patents": patents,
    }


def refresh_topic_counts(*, workspace_id: int, source_field: str) -> None:
    """依 assignment 的最終合併 root 重算 active topic 件數，不改原始 assignment。

    0021：件數是 topic_state_json->'topics' 內的 doc_count，一次寫回整份 topics。
    """
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            run = _latest_state_run(
                cur, workspace_id=workspace_id, source_field=source_field)
            if run is None:
                return
            topics = [dict(topic) for topic in _state_topics(run)]
            counts: dict[str, int] = {}
            for _, topic_key, _distance in _latest_assignments(
                cur, workspace_id=workspace_id, source_field=source_field
            ):
                code = _resolve_active_code(topics, topic_key)
                counts[code] = counts.get(code, 0) + 1
            for topic in topics:
                if topic.get("status", "active") != "active":
                    continue
                topic["doc_count"] = counts.get(str(topic["topic_code"]), 0)
            _merge_topic_state(cur, int(run["run_id"]), {"topics": topics})


def _fetch_source_excerpts(cur: Any, source_column: str, patent_ids: list[int]) -> list[str]:
    """讀取代表性文本全文；正式標籤/摘要階段不截斷獨立項內容。"""
    if not patent_ids:
        return []
    cur.execute(
        sql.SQL(
            "SELECT id, {column} AS source_text "
            "FROM core_layer.patents WHERE id = ANY(%s) ORDER BY id"
        ).format(
            column=sql.Identifier(source_column)
        ),
        (patent_ids,),
    )
    return [
        str(row["source_text"])
        for row in cur.fetchall()
        if row["source_text"]
    ]


def _latest_completed_run(*, workspace_id: int, source_field: str) -> dict[str, Any]:
    """取得具有效 artifact 的最新完成 run。

    0021：run 歸屬經 workflow_runs JOIN；status／artifact_version 等分群自身狀態在
    topic_state_json，artifact 位置改用 topic_runs.artifact_key。回傳值把 state 內
    的鍵攤平到頂層，讓既有呼叫端（latest["input_doc_count"] 等）維持原寫法。
    """
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT tr.run_id, tr.workflow_run_id, tr.previous_run_id, tr.source_field,
                   tr.topic_state_json, tr.artifact_key, wr.workspace_id
            FROM derived_layer.topic_runs tr
            JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
            WHERE wr.workspace_id = %s
              AND tr.source_field = %s
              AND tr.topic_state_json->>'status' = 'completed'
              AND tr.artifact_key IS NOT NULL
            ORDER BY (tr.topic_state_json->>'artifact_version')::int DESC NULLS LAST,
                     tr.run_id DESC
            LIMIT 1
            """,
            (workspace_id, source_field),
        ).fetchone()
    if row is None:
        raise ValueError("workspace source has no completed clustering artifact")
    latest = dict(row)
    state = dict(latest.pop("topic_state_json") or {})
    return {**state, **latest, "model_artifact_path": latest.get("artifact_key")}


def _subset_corpus(corpus: ClusteringCorpus, indexes: list[int]) -> ClusteringCorpus:
    """依相同索引切出文本、專利與向量，保持三者對齊。"""
    return ClusteringCorpus(
        patent_ids=[corpus.patent_ids[index] for index in indexes],
        documents=[corpus.documents[index] for index in indexes],
        matrix=EmbeddingMatrix(
            row_numbers=[corpus.matrix.row_numbers[index] for index in indexes],
            patent_numbers=[corpus.matrix.patent_numbers[index] for index in indexes],
            vectors=[corpus.matrix.vectors[index] for index in indexes],
        ),
        embedding_model=corpus.embedding_model,
        model_version=corpus.model_version,
        preprocessing_version=corpus.preprocessing_version,
    )


def _new_topic_run(
    *,
    latest: dict[str, Any],
    run_type: str,
    state: dict[str, Any],
) -> int:
    """建立指向前一版的新 topic_run（0021 append-only 版本化的共用入口）。

    0021 主題狀態在 JSON，無列可 FOR UPDATE 鎖；併發安全改由「不就地改舊 run，
    一律建新版本」達成：新 run 的 previous_run_id 指前一版，讀取端一律取最新
    run，落敗的併發寫入只會多出一個未被採用的版本，不會互相覆蓋。
    """
    from .runner import _ensure_workflow_run, create_topic_run

    with psycopg.connect(**get_connection_kwargs()) as conn:
        workflow_run_id = _ensure_workflow_run(
            conn,
            workspace_id=latest["workspace_id"],
            source_field=latest["source_field"],
            run_type=run_type,
        )
        return create_topic_run(
            workflow_run_id=workflow_run_id,
            source_field=latest["source_field"],
            state=state,
            previous_run_id=int(latest["run_id"]),
            connection=conn,
        )


def _create_incremental_run(*, latest: dict[str, Any], new_document_count: int) -> int:
    """建立指向上一 artifact 的 incremental run。"""
    return _new_topic_run(
        latest=latest,
        run_type="clustering_incremental",
        state={
            "run_mode": "incremental",
            "status": "running",
            "input_doc_count": int(latest["input_doc_count"]) + new_document_count,
            "new_doc_count": new_document_count,
            "topic_count": latest["topic_count"],
            "parameters": {"method": "BERTopic.partial_fit"},
            "artifact_version": int(latest["artifact_version"]) + 1,
        },
    )


def _persist_incremental_assignments(
    *,
    run_id: int,
    workspace_id: int,
    source_field: str,
    corpus: ClusteringCorpus,
    reduced: ReducedEmbeddingMatrix,
    predicted_topics: list[int],
) -> int:
    """把本批模型 topic 映射到永久 topic；未知 ID 進未分類系統桶。

    0021：指派落 derived_layer.topic_assignments，topic_key＝topic_code；
    模型 ID 與 topic_code 的對應來自最新 state 的 topics.model_topic_ids。
    """
    active = _active_model_topics(workspace_id=workspace_id, source_field=source_field)
    model_to_code = {
        int(model_id): str(row["topic_code"])
        for row in active
        for model_id in (row.get("model_topic_ids") or [])
    }
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            run = _require_latest_state_run(
                cur, workspace_id=workspace_id, source_field=source_field)
            # 未知模型 ID 一律進未分類桶；沒有未分類主題時直接 raise，不塞假 topic_key
            fallback_code = next(
                (
                    str(topic["topic_code"])
                    for topic in _state_topics(run)
                    if topic.get("topic_kind") in {"unclassified", "other"}
                    and topic.get("status", "active") == "active"
                ),
                None,
            )
            if fallback_code is None:
                raise ValueError(
                    "workspace source has no active unclassified topic for new documents")
            vectors = np.asarray(reduced.vectors, dtype=float)
            centers = {
                topic_id: vectors[[i for i, value in enumerate(predicted_topics) if value == topic_id]].mean(axis=0)
                for topic_id in set(predicted_topics)
            }
            rows = []
            for index, model_topic_id in enumerate(predicted_topics):
                topic_key = model_to_code.get(model_topic_id, fallback_code)
                distance = float(np.linalg.norm(vectors[index] - centers[model_topic_id]))
                rows.append((run_id, corpus.patent_ids[index], topic_key, distance))
            cur.executemany(
                """
                INSERT INTO derived_layer.topic_assignments (
                    run_id, patent_id, topic_key, distance_to_centroid
                ) VALUES (%s, %s, %s, %s)
                """,
                rows,
            )
    return len(rows)


def _complete_incremental_run(
    *,
    run_id: int,
    artifact_key_value: str,
    artifact_hash: str,
    artifact_version: int,
    pca_updated: bool,
) -> None:
    """完成 incremental run 並保存 artifact 位置與 PCA 更新狀態。

    0021：分群自身狀態併入 topic_state_json，artifact 位置落 topic_runs.artifact_key；
    job 狀態另寫 app_layer.workflow_runs（_set_workflow_status）。
    """
    from .runner import _set_workflow_status

    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            _merge_topic_state(
                cur,
                run_id,
                {
                    "status": "completed",
                    "model_artifact_hash": artifact_hash,
                    "artifact_version": artifact_version,
                    "metrics": {"pca_updated": pca_updated},
                },
            )
            cur.execute(
                "UPDATE derived_layer.topic_runs SET artifact_key = %s WHERE run_id = %s",
                (artifact_key_value, run_id),
            )
            _set_workflow_status(cur, run_id, "succeeded")


def _active_model_topics(*, workspace_id: int, source_field: str) -> list[dict[str, Any]]:
    """取得目前可顯示且對應模型 ID 的 active topics（0021：來自 topic_state_json）。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            run = _latest_state_run(
                cur, workspace_id=workspace_id, source_field=source_field)
            if run is None:
                return []
            topics = [
                dict(topic)
                for topic in _state_topics(run)
                if topic.get("status", "active") == "active"
                and topic.get("topic_kind", "model") == "model"
            ]
    topics.sort(key=lambda t: t.get("display_order", 0))
    return topics


def _hierarchy_model_ids(value: Any) -> list[int]:
    """解析 BERTopic hierarchy DataFrame 的 Topics 欄。"""
    if isinstance(value, (list, tuple)):
        return [int(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return []
        if isinstance(parsed, (list, tuple)):
            return [int(item) for item in parsed]
    return []


def _load_merge_topics(
    *,
    workspace_id: int,
    source_field: str,
    topic_keys: list[str],
) -> list[dict[str, Any]]:
    """取合併用的兩個 active topics（第一個為目標、第二個為來源），拒絕跨 workspace。

    目標（吸收方）必須是 model 主題，避免系統桶（未分類等）反過來吞掉正式主題；
    來源（被合併方）只要目前是 active 即可，系統桶可以被併進 model 主題。

    0021：主題在 JSON 內無列可 FOR UPDATE 鎖；併發安全改由 append-only 新版本
    達成（見 _new_topic_run），此處只負責驗證選定主題目前確實 active。
    """
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            run = _require_latest_state_run(
                cur, workspace_id=workspace_id, source_field=source_field)
    by_code = _active_topic_keys(_state_topics(run))
    rows = [dict(by_code[code]) for code in topic_keys if code in by_code]
    if len(rows) != len(topic_keys):
        raise ValueError("merge topics must be active topics in the same workspace source")
    if rows[0].get("topic_kind", "model") != "model":
        raise ValueError("merge target topic must be a model topic")
    return rows


def _create_merge_run(
    *,
    latest: dict[str, Any],
    target_topic_key: str,
    source_topic_keys: list[str],
    merged_by: str,
) -> int:
    """建立人工 merge run，保留目標／來源 topics 與操作者。

    target_topic_key 另存一欄，讓 _unmerge_blocked_reason 能判斷「後續 merge 是否
    動到本次的合併結果」（目標被當成後續合併的任一邊都算下游依賴）。
    """
    return _new_topic_run(
        latest=latest,
        run_type="topic_merge",
        state={
            "run_mode": "merge",
            "status": "running",
            "input_doc_count": latest["input_doc_count"],
            "topic_count": max(0, int(latest["topic_count"]) - len(source_topic_keys)),
            "parameters": {
                "target_topic_key": target_topic_key,
                "source_topic_keys": source_topic_keys,
                "merged_by": merged_by,
            },
            "artifact_version": int(latest["artifact_version"]) + 1,
        },
    )


def _persist_topic_merge(
    *,
    run_id: int,
    workspace_id: int,
    source_field: str,
    previous_run_id: int,
    target_code: str,
    source_codes: list[str],
    merged_by: str,
    label: str | None,
    artifact_version: int,
) -> None:
    """在新 run 寫入合併後的完整主題快照與指派。

    0021：不就地改舊 run（舊版保留可追溯），而是把前一版 topics 複製過來後套用
    合併結果，整份寫進新 run 的 topic_state_json->'topics'；指派同樣寫成新 run 的
    完整快照，讀取端一律取最新 run。不再依賴 BERTopic 模型或從表。

    語意：目標主題（target_code）維持 active 並吸收來源指派；來源主題保留但標
    status='merged' 並記 merged_into_topic_id。label 只寫目標主題，來源主題不動
    label／label_source（避免對已 merged 的主題誤標 manual）。
    """
    from .runner import _set_workflow_status

    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT topic_state_json FROM derived_layer.topic_runs WHERE run_id = %s",
                (previous_run_id,),
            )
            previous_topics = [
                dict(topic)
                for topic in (dict(cur.fetchone()["topic_state_json"] or {}).get("topics") or [])
            ]
            target_topic = next(
                (t for t in previous_topics if str(t.get("topic_code")) == target_code), None)
            if target_topic is None:
                raise ValueError(f"merge target topic not found in previous run: {target_code}")
            target_topic_id = target_topic.get("topic_id")

            sources = set(source_codes)
            topics: list[dict[str, Any]] = []
            for topic in previous_topics:
                code = str(topic.get("topic_code"))
                if code in sources and topic.get("status", "active") == "active":
                    # 來源主題保留但標 merged 並指向目標，供讀取端沿鏈併回
                    topic = {
                        **topic,
                        "status": "merged",
                        "merged_into_topic_id": target_topic_id,
                        "merged_by": merged_by,
                    }
                elif code == target_code and label is not None:
                    # 帶 label 才改名；不帶則沿用目標主題現有名稱與 label_source
                    topic = {**topic, "label": label, "label_source": "manual"}
                topics.append(topic)

            # merged_topic_code 一併記進 state，供 merge_history 穩定辨識合併結果
            _merge_topic_state(
                cur,
                run_id,
                {
                    "topics": topics,
                    "merged_topic_code": target_code,
                    "status": "completed",
                    "artifact_version": artifact_version,
                },
            )
            # 純結構合併不動模型，沿用前一版 artifact_key，讓 _latest_completed_run
            # 能把本次 merge run 視為最新完成版（否則會退回合併前的 run）
            cur.execute(
                """
                UPDATE derived_layer.topic_runs SET artifact_key = (
                    SELECT artifact_key FROM derived_layer.topic_runs WHERE run_id = %s
                ) WHERE run_id = %s
                """,
                (previous_run_id, run_id),
            )

            # 指派：把前一版最新指派整份帶到新 run，來源主題的專利改指向目標主題
            assignments = _latest_assignments(
                cur, workspace_id=workspace_id, source_field=source_field)
            cur.executemany(
                """
                INSERT INTO derived_layer.topic_assignments (
                    run_id, patent_id, topic_key, distance_to_centroid
                ) VALUES (%s, %s, %s, %s)
                """,
                [
                    (
                        run_id,
                        patent_id,
                        target_code if topic_key in sources else topic_key,
                        distance,
                    )
                    for patent_id, topic_key, distance in assignments
                ],
            )
            _set_workflow_status(cur, run_id, "succeeded")


def _unmerge_blocked_reason(
    *,
    merge_run: dict[str, Any],
    result_topic_code: str,
    completed_runs: list[dict[str, Any]],
) -> str | None:
    """判斷指定 merge 是否可獨立復原，避免破壞後續依賴。

    0021：判斷資料改讀各 run 攤平後的 topic_state_json（run_mode／parameters／reverted_at）。
    """
    if merge_run.get("reverted_at") is not None:
        return "此合併紀錄已復原"
    merge_run_id = int(merge_run["run_id"])
    for row in completed_runs:
        if int(row["run_id"]) <= merge_run_id:
            continue
        run_mode = row.get("run_mode")
        if run_mode in {"full", "incremental"}:
            return "合併後已有 full 或 incremental 更新，需先建立重建策略"
        if run_mode != "merge" or row.get("reverted_at") is not None:
            continue
        # 後續 merge 只要動到本次的合併結果（不論當來源或當吸收方的目標），
        # 本次就不能單獨復原，必須先處理下游紀錄
        parameters = dict(row.get("parameters") or {})
        involved_codes = {
            str(value) for value in parameters.get("source_topic_keys", [])
        }
        target_code = parameters.get("target_topic_key")
        if target_code is not None:
            involved_codes.add(str(target_code))
        if result_topic_code in involved_codes:
            return "此合併結果已被後續合併使用，需先復原下游紀錄"
    return None


def _create_unmerge_run(
    *,
    latest: dict[str, Any],
    target_merge_run_id: int,
    restored_topic_keys: list[str],
    reverted_topic_code: str,
    reverted_by: str,
) -> int:
    """建立 unmerge run，先保存目標 merge 與預計恢復的 topic_code。"""
    return _new_topic_run(
        latest=latest,
        run_type="topic_unmerge",
        state={
            "run_mode": "unmerge",
            "status": "running",
            "input_doc_count": latest["input_doc_count"],
            "topic_count": int(latest["topic_count"]) + 1,
            "parameters": {
                "target_merge_run_id": target_merge_run_id,
                "restored_topic_keys": restored_topic_keys,
                "reverted_topic_code": reverted_topic_code,
                "reverted_by": reverted_by,
            },
            "artifact_version": int(latest["artifact_version"]) + 1,
        },
    )


def _persist_unmerge(
    *,
    run_id: int,
    workspace_id: int,
    source_field: str,
    previous_run_id: int,
    target_merge_run_id: int,
    restored_topic_keys: list[str],
    reverted_topic_code: str,
    reverted_by: str,
    artifact_version: int,
) -> None:
    """在新 run 寫入還原後的主題快照與指派，並把目標 merge run 標記為已復原。

    0021：主題在 JSON 內沒有列可 FOR UPDATE 鎖；併發安全改由 append-only 新版本
    達成——還原結果寫進新 run（previous_run_id 指前一版），舊版原樣保留可追溯；
    目標 merge run 的 reverted_at 以「僅在尚未復原時才寫入」的條件式 UPDATE 保護，
    確保同一 merge 不會被重複復原。

    語意：來源主題（restored_topic_keys）復原成 active 並取回原本的指派；
    合併目標（reverted_topic_code）本來就是既有主題，維持 active，只是不再持有
    來源的專利，因此不做封存。
    """
    from .runner import _set_workflow_status

    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # 條件式 UPDATE 取代列鎖：只有尚未復原的 merge run 會被標記，落敗者 rowcount=0
            cur.execute(
                """
                UPDATE derived_layer.topic_runs
                SET topic_state_json = topic_state_json || %s
                WHERE run_id = %s
                  AND topic_state_json->>'run_mode' = 'merge'
                  AND topic_state_json->>'status' = 'completed'
                  AND topic_state_json->'reverted_at' IS NULL
                """,
                (
                    Jsonb(
                        {
                            "reverted_at": "now",
                            "reverted_by": reverted_by,
                            "reverted_by_run_id": run_id,
                        }
                    ),
                    target_merge_run_id,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("merge run changed concurrently or was already restored")

            cur.execute(
                "SELECT topic_state_json FROM derived_layer.topic_runs WHERE run_id = %s",
                (previous_run_id,),
            )
            previous_topics = [
                dict(topic)
                for topic in (dict(cur.fetchone()["topic_state_json"] or {}).get("topics") or [])
            ]
            restored = set(restored_topic_keys)
            topics: list[dict[str, Any]] = []
            for topic in previous_topics:
                code = str(topic.get("topic_code"))
                if code in restored:
                    # 來源主題復原成 active，清掉合併鏈欄位
                    topic = {
                        key: value
                        for key, value in topic.items()
                        if key not in {"merged_into_topic_id", "merged_by"}
                    }
                    topic["status"] = "active"
                topics.append(topic)

            _merge_topic_state(
                cur,
                run_id,
                {
                    "topics": topics,
                    "status": "completed",
                    "artifact_version": artifact_version,
                },
            )
            # 純結構還原不動模型，沿用前一版 artifact_key（同 _persist_topic_merge）
            cur.execute(
                """
                UPDATE derived_layer.topic_runs SET artifact_key = (
                    SELECT artifact_key FROM derived_layer.topic_runs WHERE run_id = %s
                ) WHERE run_id = %s
                """,
                (previous_run_id, run_id),
            )

            # 指派：把合併結果的專利依原始指派還原回各來源主題
            assignments = _latest_assignments(
                cur, workspace_id=workspace_id, source_field=source_field)
            original = _original_assignment_before_merge(
                cur,
                workspace_id=workspace_id,
                source_field=source_field,
                merge_run_id=target_merge_run_id,
            )
            cur.executemany(
                """
                INSERT INTO derived_layer.topic_assignments (
                    run_id, patent_id, topic_key, distance_to_centroid
                ) VALUES (%s, %s, %s, %s)
                """,
                [
                    (
                        run_id,
                        patent_id,
                        original.get(patent_id, topic_key)
                        if topic_key == reverted_topic_code
                        else topic_key,
                        distance,
                    )
                    for patent_id, topic_key, distance in assignments
                ],
            )
            _set_workflow_status(cur, run_id, "succeeded")


def _original_assignment_before_merge(
    cur: Any, *, workspace_id: int, source_field: str, merge_run_id: int
) -> dict[int, str]:
    """取合併發生前一版的指派（patent_id → topic_key），供 unmerge 還原。"""
    cur.execute(
        """
        SELECT DISTINCT ON (ta.patent_id) ta.patent_id, ta.topic_key
        FROM derived_layer.topic_assignments ta
        JOIN derived_layer.topic_runs tr ON tr.run_id = ta.run_id
        JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
        WHERE wr.workspace_id = %s AND tr.source_field = %s AND ta.run_id < %s
        ORDER BY ta.patent_id, ta.run_id DESC
        """,
        (workspace_id, source_field, merge_run_id),
    )
    rows = cur.fetchall()
    if rows and isinstance(rows[0], dict):
        return {int(r["patent_id"]): str(r["topic_key"]) for r in rows}
    return {int(r[0]): str(r[1]) for r in rows}


def _resolved_topic_by_patent(*, workspace_id: int, source_field: str) -> dict[int, str]:
    """在 Python 解析 merged_into 鏈，保留 assignment 的原始模型判斷。

    0021：回傳 patent_id → active topic_code（合併鏈解析後），與
    PostgresTopicStateRepository.resolve_active_code 同一解法；查無主題時回空 dict。
    """
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            run = _latest_state_run(
                cur, workspace_id=workspace_id, source_field=source_field)
            if run is None:
                return {}
            topics = _state_topics(run)
            assignment_rows = _latest_assignments(
                cur, workspace_id=workspace_id, source_field=source_field)
    return {
        patent_id: _resolve_active_code(topics, topic_key)
        for patent_id, topic_key, _distance in assignment_rows
    }


def _dashboard_topics(cur: Any, workspace_id: int) -> list[dict[str, Any]]:
    """取得前端 chips，摘要仍存 DB 但不輸出到第一版頁面。

    0021：兩個通道各取自己最新一筆帶 topics 的 run，主題來自 topic_state_json->'topics'。
    """
    rows: list[dict[str, Any]] = []
    for source_field in source_fields():
        run = _latest_state_run(cur, workspace_id=workspace_id, source_field=source_field)
        if run is None:
            continue
        for topic in _state_topics(run):
            if topic.get("status", "active") != "active":
                continue
            rows.append(
                {
                    "source_field": source_field,
                    "topic_code": topic["topic_code"],
                    "topic_kind": topic.get("topic_kind", "model"),
                    "doc_count": topic.get("doc_count", 0),
                    "label": topic.get("label"),
                    "display_order": topic.get("display_order", 0),
                    "status": topic.get("status", "active"),
                }
            )
    rows.sort(key=lambda r: (r["source_field"], r["display_order"], r["topic_code"]))
    return rows


def _fail_run(run_id: int, error: Exception) -> None:
    """保留失敗 run 與錯誤，禁止半套狀態被當成完成。

    0021：分群狀態寫 topic_state_json，job 狀態與錯誤另寫 workflow_runs。
    """
    from .runner import _set_workflow_status

    message = str(error)[:4000]
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            _merge_topic_state(
                cur, run_id, {"status": "failed", "error_message": message})
            _set_workflow_status(cur, run_id, "failed", error=message)
