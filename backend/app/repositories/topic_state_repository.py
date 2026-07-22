"""TopicStateRepository：讀取 0021 3+3 schema 的最新正式主題狀態。

資料流：derived_layer.topic_runs（topic_state_json 內含定案 topics）JOIN
app_layer.workflow_runs（workspace 歸屬）→ 取該 workspace/source_field 最新 run
→ 主題只回「合併／改名後」的 active 主題（含未分類 topic_kind='unclassified'），
不回候選方案；derived_layer.topic_assignments 的 topic_key 若指向已合併主題，
沿 merged_into_topic_id 鏈併回目標 active 主題。

限制：唯讀，不寫任何表；只支援 0021 定義的兩個 source_field。
"""
from __future__ import annotations

from typing import Any

ALLOWED_SOURCE_FIELDS = ("wips_independent_claims", "effect_summary")


class TopicStateNotFoundError(LookupError):
    """該 workspace/source_field 尚無任何 topic run。"""


class PostgresTopicStateRepository:
    """以 psycopg 直讀 0021 schema 的正式主題狀態（唯讀）。"""

    def __init__(self, connect_kwargs: dict[str, Any] | None = None):
        # 未指定時沿用專案統一連線設定（env PG* / DATABASE_URL）
        self._connect_kwargs = connect_kwargs

    def _connect(self):
        import psycopg

        from backend.app.db.connection import get_connection_kwargs

        return psycopg.connect(**(self._connect_kwargs or get_connection_kwargs()))

    def get_latest_topic_state(self, workspace_id: int, source_field: str) -> dict[str, Any]:
        """回傳最新正式主題狀態：{workspace_id, source_field, run_id, state_run_id, topics:[...]}。

        topics 每項含 topic_code/label/status/topic_kind/doc_count/patent_ids；
        僅 active 主題（含未分類），已合併主題的 assignments 併回目標主題。

        incremental run 的 topic_state_json 不帶 topics（topics 掛在 finalize run），
        且其 topic_assignments 只帶增量。因此：
        - topics 取「最新且 topics 非空」的 run（沿 run_id 由大到小 fallback）→ state_run_id；
        - assignments 取該 ws/field 全部 run 中每個 patent_id 的最新一筆（DISTINCT ON），
          再依合併鏈併回 active 主題；
        - run_id＝assignments 基準 run（該 ws/field 最新 run，含只帶增量者），與 state_run_id 分明。

        Raises:
            ValueError: source_field 非法。
            TopicStateNotFoundError: 該 workspace/source_field 無帶 topics 的 run。
        """
        if source_field not in ALLOWED_SOURCE_FIELDS:
            raise ValueError(f"unsupported source_field: {source_field!r}")

        with self._connect() as conn:
            # 主題來源：最新「topic_state_json->topics 非空」的 run（fallback 過濾掉 incremental）
            state_row = conn.execute(
                """
                SELECT tr.run_id, tr.topic_state_json
                FROM derived_layer.topic_runs tr
                JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
                WHERE wr.workspace_id = %s AND tr.source_field = %s
                  AND jsonb_array_length(COALESCE(tr.topic_state_json->'topics', '[]'::jsonb)) > 0
                ORDER BY tr.run_id DESC
                LIMIT 1
                """,
                (workspace_id, source_field),
            ).fetchone()
            if state_row is None:
                raise TopicStateNotFoundError(
                    f"no topic run for workspace {workspace_id} / {source_field}")
            state_run_id, state = state_row
            # assignments 基準 run：該 ws/field 最新 run（含只帶增量的 incremental run）
            base_row = conn.execute(
                """
                SELECT max(tr.run_id)
                FROM derived_layer.topic_runs tr
                JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
                WHERE wr.workspace_id = %s AND tr.source_field = %s
                """,
                (workspace_id, source_field),
            ).fetchone()
            run_id = base_row[0]
            # assignments：全部 run 中每個 patent 取 run_id 最大一筆（DISTINCT ON 語意）
            assignments = conn.execute(
                """
                SELECT DISTINCT ON (ta.patent_id) ta.patent_id, ta.topic_key
                FROM derived_layer.topic_assignments ta
                JOIN derived_layer.topic_runs tr ON tr.run_id = ta.run_id
                JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
                WHERE wr.workspace_id = %s AND tr.source_field = %s
                ORDER BY ta.patent_id, ta.run_id DESC
                """,
                (workspace_id, source_field),
            ).fetchall()

        topics = list((state or {}).get("topics") or [])
        by_id = {t.get("topic_id"): t for t in topics}
        by_code = {t.get("topic_code"): t for t in topics}

        def resolve_active_code(code: str) -> str:
            """沿 merged_into_topic_id 鏈找到 active 目標的 topic_code；斷鏈時保留原 code。"""
            seen: set[int] = set()
            topic = by_code.get(code)
            while topic is not None and topic.get("status") == "merged":
                target_id = topic.get("merged_into_topic_id")
                if target_id in seen or target_id not in by_id:
                    break
                seen.add(target_id)
                topic = by_id[target_id]
            return topic.get("topic_code", code) if topic is not None else code

        # 只輸出合併／改名後的 active 主題（含 topic_kind='unclassified' 的未分類）
        result_topics: dict[str, dict[str, Any]] = {}
        for t in topics:
            if t.get("status") != "active":
                continue
            result_topics[t["topic_code"]] = {
                "topic_code": t["topic_code"],
                "label": t.get("label"),
                "status": t["status"],
                "topic_kind": t.get("topic_kind", "model"),
                "doc_count": t.get("doc_count", 0),
                "patent_ids": [],
            }
        for patent_id, topic_key in assignments:
            code = resolve_active_code(topic_key)
            if code in result_topics:
                result_topics[code]["patent_ids"].append(patent_id)
        for t in result_topics.values():
            t["patent_ids"].sort()

        return {
            "workspace_id": workspace_id,
            "source_field": source_field,
            "run_id": run_id,              # assignments 基準 run（最新 run，含 incremental）
            "state_run_id": state_run_id,  # topics 來源 run（最新有 topics 者）
            "topics": sorted(result_topics.values(), key=lambda t: t["topic_code"]),
        }
