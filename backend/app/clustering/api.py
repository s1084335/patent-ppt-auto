"""分群臨時 FastAPI：提供 workspace 操作 API 與 WIPS 標籤式前端。"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.app.db.connection import get_connection_kwargs

from .runner import calibrate_top_level, finalize_top_level
from .sources import get_source_spec, source_fields
from .workspace_service import (
    add_workspace_patents,
    apply_candidate_explanations,
    apply_topic_labels,
    candidate_review_payload,
    create_workspace,
    demo_patent_ids,
    hierarchy_merge_suggestions,
    incremental_workspace,
    merge_history,
    merge_workspace_topics,
    topic_labeling_payload,
    unmerge_workspace_topics,
    workspace_dashboard,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
WEB_ROOT = Path(__file__).resolve().parent / "web"
load_dotenv(PROJECT_ROOT / ".env", override=False)

app = FastAPI(title="Patent Workspace Clustering", version="0.1.0")
app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="clustering-assets")


class DemoWorkspaceRequest(BaseModel):
    """建立臨時展示 workspace 所需參數。"""

    workspace_name: str = Field(min_length=1, max_length=120)
    document_count: int = Field(default=200, ge=50, le=2000)
    created_by: str = Field(default="demo-user", min_length=1, max_length=120)


class FinalizeRequest(BaseModel):
    """保存使用者選定候選。"""

    candidate_id: int
    selected_by: str = Field(default="demo-user", min_length=1, max_length=120)


class CandidateExplanationItem(BaseModel):
    """單一候選主題數方案的 AI 說明。"""

    candidate_id: int
    explanation: str = Field(min_length=1)


class ApplyCandidateExplanationsRequest(BaseModel):
    """Claude Code 回寫候選主題數說明的 payload。"""

    explanations: list[CandidateExplanationItem] = Field(min_length=1)


class MergeRequest(BaseModel):
    """人工合併兩個 active model topics。

    0021 後主題以 topic_code（字串）為唯一識別，故用 topic_keys 而非 int topic_ids；
    順序即語意：第一個是目標（吸收方），第二個是來源（被合併方）。
    """

    topic_keys: list[str] = Field(min_length=2, max_length=2)
    merged_by: str = Field(default="demo-user", min_length=1, max_length=120)
    label: str | None = Field(default=None, max_length=120)


class UnmergeRequest(BaseModel):
    """依指定 merge run 復原來源 topics。"""

    reverted_by: str = Field(default="demo-user", min_length=1, max_length=120)


class RenameTopicRequest(BaseModel):
    """人工修改主題顯示名稱。"""

    label: str = Field(min_length=1, max_length=120)
    updated_by: str = Field(default="demo-user", min_length=1, max_length=120)


class ReorderTopicsRequest(BaseModel):
    """保存同一通道 active topics 的顯示順序。"""

    topic_ids: list[int] = Field(min_length=1)


class AddWorkspacePatentsRequest(BaseModel):
    """加入既有 workspace 的核心專利 ID。"""

    patent_ids: list[int] = Field(min_length=1)
    added_by: str = Field(default="api-user", min_length=1, max_length=120)


class TopicLabelItem(BaseModel):
    """Claude CLI 或批次流程回寫的 topic label/summary。

    source 預設 llm（0010 constraint 只允許 llm/manual/fallback），
    字數硬上限由 workspace_service 統一驗證。
    """

    topic_id: int
    label: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=1000)
    source: str = Field(default="llm", max_length=60)


class ApplyTopicLabelsRequest(BaseModel):
    """批次套用 topic label/summary，不覆蓋 manual label。"""

    labels: list[TopicLabelItem] = Field(min_length=1)
    updated_by: str = Field(default="claude-cli", min_length=1, max_length=120)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """回傳臨時分群操作頁面。"""
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    """確認 API 與資料庫可連線。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/workspaces")
def list_workspaces() -> list[dict[str, Any]]:
    """列出 active workspaces 與專利件數。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT w.workspace_id, w.workspace_name, w.description, w.status,
                   count(wp.patent_id)::integer AS patent_count,
                   w.created_at, w.updated_at
            FROM app_layer.workspaces w
            LEFT JOIN app_layer.workspace_patents wp ON wp.workspace_id = w.workspace_id
            WHERE w.status = 'active'
            GROUP BY w.workspace_id
            ORDER BY w.updated_at DESC, w.workspace_id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/workspaces/demo")
def create_demo_workspace(request: DemoWorkspaceRequest) -> dict[str, Any]:
    """以雙向量齊全的既有專利建立展示 workspace。"""
    try:
        patent_ids = demo_patent_ids(request.document_count)
        if len(patent_ids) < request.document_count:
            raise ValueError(
                f"only {len(patent_ids)} patents have both technical and effect embeddings"
            )
        workspace_id = create_workspace(
            workspace_name=request.workspace_name,
            patent_ids=patent_ids,
            created_by=request.created_by,
            description="Temporary clustering review workspace",
        )
        return workspace_dashboard(workspace_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/workspaces/{workspace_id}")
def get_workspace(workspace_id: int) -> dict[str, Any]:
    """取得雙通道 topics 與可篩選專利列表。"""
    try:
        return workspace_dashboard(workspace_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/workspaces/{workspace_id}/patents")
def add_patents(workspace_id: int, request: AddWorkspacePatentsRequest) -> dict[str, int]:
    """加入新專利；呼叫端再分別執行技術與功效 incremental。"""
    try:
        return add_workspace_patents(
            workspace_id=workspace_id,
            patent_ids=request.patent_ids,
            added_by=request.added_by,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/workspaces/{workspace_id}/runs")
def workspace_runs(workspace_id: int) -> list[dict[str, Any]]:
    """取得兩通道最新 run 與候選，供前端顯示待選方案。

    0021：run 屬性與候選都在 topic_state_json，一次查詢取回，不再對每個 run 另查候選。
    """
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (tr.source_field)
                   tr.run_id, tr.workflow_run_id, tr.previous_run_id, tr.source_field,
                   tr.topic_state_json, tr.artifact_key, wr.workspace_id
            FROM derived_layer.topic_runs tr
            JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
            WHERE wr.workspace_id = %s
              AND tr.topic_state_json->>'status' IN ('needs_review', 'completed')
            ORDER BY tr.source_field, tr.run_id DESC
            """,
            (workspace_id,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        state = dict(row.get("topic_state_json") or {})
        item = {**row, **state}
        item["candidates"] = sorted(state.get("candidates") or [],
                                    key=lambda c: c["candidate_k"])
        result.append(item)
    return result


@app.post("/api/workspaces/{workspace_id}/calibrate/{source_field}")
def calibrate_workspace(workspace_id: int, source_field: str) -> dict[str, Any]:
    """掃描該資料量允許的 k 並產生二或三組候選。"""
    try:
        get_source_spec(source_field)
        summary = calibrate_top_level(workspace_id=workspace_id, source_field=source_field)
        return summary.to_dict()
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/runs/{run_id}/finalize")
def finalize_workspace_run(run_id: int, request: FinalizeRequest) -> dict[str, Any]:
    """依使用者候選建立永久 topics、assignments、artifact 及 LLM 標籤。"""
    try:
        summary = finalize_top_level(
            run_id=run_id,
            candidate_id=request.candidate_id,
            selected_by=request.selected_by,
        )
        # 0021：workspace_id 已移到 app_layer.workflow_runs，經 workflow_run_id join 取得
        with psycopg.connect(**get_connection_kwargs()) as conn:
            scope = conn.execute(
                "SELECT wr.workspace_id, tr.source_field "
                "FROM derived_layer.topic_runs tr "
                "JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id "
                "WHERE tr.run_id = %s",
                (run_id,),
            ).fetchone()
        return {"result": summary.to_dict(), "dashboard": workspace_dashboard(int(scope[0]))}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/runs/{run_id}/candidate-review-payload")
def get_candidate_review_payload(run_id: int) -> dict[str, Any]:
    """輸出 Claude CLI 可用的候選方案說明資料。"""
    try:
        return candidate_review_payload(run_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/runs/{run_id}/candidate-explanations")
def post_candidate_explanations(
    run_id: int,
    request: ApplyCandidateExplanationsRequest,
) -> dict[str, int]:
    """保存 Claude Code 對候選主題數方案的說明。"""
    try:
        return apply_candidate_explanations(
            run_id=run_id,
            explanations=[item.model_dump() for item in request.explanations],
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/workspaces/{workspace_id}/labeling-payload/{source_field}")
def get_topic_labeling_payload(
    workspace_id: int,
    source_field: str,
    topic_id: list[int] | None = None,
) -> dict[str, Any]:
    """輸出 Claude CLI 可用的 topic 標籤/摘要資料。"""
    try:
        get_source_spec(source_field)
        return topic_labeling_payload(
            workspace_id=workspace_id,
            source_field=source_field,
            topic_ids=topic_id,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/workspaces/{workspace_id}/labels/{source_field}")
def post_topic_labels(
    workspace_id: int,
    source_field: str,
    request: ApplyTopicLabelsRequest,
) -> dict[str, int]:
    """回寫 Claude CLI 產生的 topic label/summary。"""
    try:
        get_source_spec(source_field)
        return apply_topic_labels(
            workspace_id=workspace_id,
            source_field=source_field,
            labels=[item.model_dump() for item in request.labels],
            updated_by=request.updated_by,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/workspaces/{workspace_id}/incremental/{source_field}")
def incremental(workspace_id: int, source_field: str) -> dict[str, Any]:
    """更新指定 workspace 通道的新專利，不重跑全量。"""
    try:
        get_source_spec(source_field)
        return asdict(incremental_workspace(workspace_id=workspace_id, source_field=source_field))
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/workspaces/{workspace_id}/merge-suggestions/{source_field}")
def merge_suggestions(workspace_id: int, source_field: str) -> list[dict[str, Any]]:
    """回傳 BERTopic 官方階層計算出的相近主題候選。"""
    try:
        get_source_spec(source_field)
        return hierarchy_merge_suggestions(workspace_id=workspace_id, source_field=source_field)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/workspaces/{workspace_id}/merge/{source_field}")
def merge_topics(workspace_id: int, source_field: str, request: MergeRequest) -> dict[str, Any]:
    """由使用者確認後合併兩個主題，模型與 DB 同步版本化。"""
    try:
        result = merge_workspace_topics(
            workspace_id=workspace_id,
            source_field=source_field,
            topic_keys=request.topic_keys,
            merged_by=request.merged_by,
            label=request.label,
        )
        return {"result": asdict(result), "dashboard": workspace_dashboard(workspace_id)}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/workspaces/{workspace_id}/merge-history/{source_field}")
def get_merge_history(workspace_id: int, source_field: str) -> list[dict[str, Any]]:
    """回傳可稽核的 merge 紀錄與獨立復原資格。"""
    try:
        return merge_history(workspace_id=workspace_id, source_field=source_field)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/workspaces/{workspace_id}/unmerge/{source_field}/{merge_run_id}")
def unmerge_topics(
    workspace_id: int,
    source_field: str,
    merge_run_id: int,
    request: UnmergeRequest,
) -> dict[str, Any]:
    """依 merge run 重播其他模型版本並復原該筆來源 topics。"""
    try:
        result = unmerge_workspace_topics(
            workspace_id=workspace_id,
            source_field=source_field,
            merge_run_id=merge_run_id,
            reverted_by=request.reverted_by,
        )
        return {"result": asdict(result), "dashboard": workspace_dashboard(workspace_id)}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.patch("/api/topics/{topic_id}/label")
def rename_topic(topic_id: int, request: RenameTopicRequest) -> dict[str, Any]:
    """保存人工主題名稱，後續 LLM 不得覆寫（0021：主題在 topic_state_json）。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        # 主題散在各 run 的 state，先找含此 topic_id 且為 active 的最新 run
        run = conn.execute(
            """
            SELECT run_id, topic_state_json
            FROM derived_layer.topic_runs
            WHERE jsonb_path_exists(topic_state_json,
                '$.topics[*] ? (@.topic_id == $tid && @.status == "active")',
                jsonb_build_object('tid', %s::int))
            ORDER BY run_id DESC LIMIT 1
            """,
            (topic_id,),
        ).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="active topic not found")
        topics = list(dict(run["topic_state_json"] or {}).get("topics") or [])
        updated: dict[str, Any] | None = None
        for topic in topics:
            if int(topic.get("topic_id") or 0) == topic_id and topic.get("status") == "active":
                topic.update({"label": request.label.strip(), "label_source": "manual"})
                topic["label_metadata"] = {**(topic.get("label_metadata") or {}),
                                           "updated_by": request.updated_by}
                updated = topic
        if updated is None:
            raise HTTPException(status_code=404, detail="active topic not found")
        conn.execute(
            "UPDATE derived_layer.topic_runs "
            "SET topic_state_json = jsonb_set(topic_state_json, '{topics}', %s) WHERE run_id = %s",
            (Jsonb(topics), run["run_id"]),
        )
    return {"topic_id": topic_id, "label": updated["label"],
            "label_source": updated["label_source"]}


@app.patch("/api/workspaces/{workspace_id}/topics/{source_field}/order")
def reorder_topics(
    workspace_id: int,
    source_field: str,
    request: ReorderTopicsRequest,
) -> dict[str, str]:
    """以完整 active topic ID 順序更新前端排列（0021：主題在 topic_state_json）。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        run = conn.execute(
            """
            SELECT tr.run_id, tr.topic_state_json
            FROM derived_layer.topic_runs tr
            JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
            WHERE wr.workspace_id = %s AND tr.source_field = %s
              AND jsonb_array_length(COALESCE(tr.topic_state_json->'topics', '[]'::jsonb)) > 0
            ORDER BY tr.run_id DESC LIMIT 1
            """,
            (workspace_id, source_field),
        ).fetchone()
        topics = list(dict((run or {}).get("topic_state_json") or {}).get("topics") or [])
        actual = {int(t["topic_id"]) for t in topics if t.get("status") == "active"}
        requested = [int(value) for value in request.topic_ids]
        if actual != set(requested) or len(actual) != len(requested):
            raise HTTPException(status_code=409, detail="topic order must contain every active topic exactly once")
        order_by_id = {topic_id: index for index, topic_id in enumerate(requested, start=1)}
        for topic in topics:
            new_order = order_by_id.get(int(topic.get("topic_id") or 0))
            if new_order is not None:
                topic["display_order"] = new_order
        conn.execute(
            "UPDATE derived_layer.topic_runs "
            "SET topic_state_json = jsonb_set(topic_state_json, '{topics}', %s) WHERE run_id = %s",
            (Jsonb(topics), run["run_id"]),
        )
    return {"status": "updated"}


def _http_error(error: Exception) -> HTTPException:
    """把可預期服務錯誤轉為前端可讀訊息，未知錯誤仍保留 500。"""
    if isinstance(error, (ValueError, FileNotFoundError)):
        return HTTPException(status_code=409, detail=str(error))
    return HTTPException(status_code=500, detail=str(error))
