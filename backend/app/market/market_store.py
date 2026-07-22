"""市場資料證據庫存取（derived_layer.market_evidence，接 0023 schema）。

只存使用者已接受的證據（accepted_at 落款，本表無 pending 狀態）；同 scope 同 source_url 拒重
（唯一約束 uq_market_evidence_scope_url 衝突轉 DuplicateEvidenceError）。不可 UPDATE 值：
supersede 於舊列 payload_json 疊加 superseded 標記＋append 新列，舊列數值原封不動保留供稽核。
彙總不重寫邏輯，轉呼 aggregate.py 既有確定性彙總（min–max／single_source／divergent）。
SQL 只留在本模組。
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg import errors as pg_errors
from psycopg.types.json import Jsonb

from backend.app.market import aggregate
from backend.app.market.evidence_model import KINDS, validate_evidence

# 對外欄位順序（8 欄定版，對應 0023 schema）
_COLUMNS = ("id", "kind", "scope", "target", "payload_json", "source_url", "summary", "accepted_at")
_MARKET_KINDS = {"market_size", "region_trend", "key_player"}
_SUBJECT_KINDS = {"customer", "pain_point"}


class DuplicateEvidenceError(ValueError):
    """同 scope 同 source_url 已收錄（唯一約束衝突）；防重複收錄。"""


class MarketStore:
    """market_evidence 證據庫存取層（只存已接受、append-only、彙總轉 aggregate.py）。"""

    def __init__(self, connect_kwargs: dict[str, Any] | None = None):
        # 未指定時沿用專案統一連線設定（env PG* / DATABASE_URL）
        self._connect_kwargs = connect_kwargs

    def _connect(self):
        from backend.app.db.connection import get_connection_kwargs

        return psycopg.connect(**(self._connect_kwargs or get_connection_kwargs()))

    def save_evidence(
        self,
        kind: str,
        scope: str,
        target: str | None,
        payload_json: dict[str, Any],
        source_url: str,
        summary: str,
        accepted_at: str | None = None,
    ) -> int:
        """寫入一筆已接受證據；回傳 id。

        kind 非白名單、必填欄為空或 payload_json 非 dict 一律拒寫；同 scope 同 source_url
        已存在則轉 DuplicateEvidenceError（唯一約束衝突）。accepted_at 未給則由 DB 落 now()。
        """
        if kind not in KINDS:
            raise ValueError(f"非法 kind：{kind!r}（限 {KINDS}）")
        for name, val in (("scope", scope), ("source_url", source_url), ("summary", summary)):
            if not val or not str(val).strip():
                raise ValueError(f"{name} 不得為空")
        if not isinstance(payload_json, dict):
            raise ValueError("payload_json 必須為 dict")

        # DB 欄位 source_url 是正式去重鍵；payload 內同步成同一值，避免後續彙總去重讀到另一份 URL。
        normalized_payload = dict(payload_json)
        normalized_payload["source_url"] = source_url
        evidence_for_validation: dict[str, Any] = {
            "kind": kind,
            "scope": scope,
            "payload_json": normalized_payload,
        }
        if kind in _MARKET_KINDS:
            evidence_for_validation["market"] = target
        if kind in _SUBJECT_KINDS:
            evidence_for_validation["subject"] = target
        validate_evidence(evidence_for_validation)

        cols = ["kind", "scope", "target", "payload_json", "source_url", "summary"]
        params: list[Any] = [kind, scope, target, Jsonb(normalized_payload), source_url, summary]
        if accepted_at is not None:
            cols.append("accepted_at")
            params.append(accepted_at)
        placeholders = ", ".join(["%s"] * len(params))
        try:
            with self._connect() as conn:
                new_id = conn.execute(
                    f"INSERT INTO derived_layer.market_evidence ({', '.join(cols)}) "
                    f"VALUES ({placeholders}) RETURNING id",
                    params,
                ).fetchone()[0]
                conn.commit()
        except pg_errors.UniqueViolation as exc:
            raise DuplicateEvidenceError(
                f"同 scope={scope!r} 同 source_url={source_url!r} 已收錄，拒重複收錄"
            ) from exc
        return new_id

    def get_evidence(
        self,
        kind: str | None = None,
        scope: str | None = None,
        target: str | None = None,
    ) -> list[dict[str, Any]]:
        """依 kind／scope／target 過濾查詢；回傳每列 8 欄 dict（payload_json 為 dict），依 id 排序。"""
        clauses: list[str] = []
        params: list[Any] = []
        for col, val in (("kind", kind), ("scope", scope), ("target", target)):
            if val is not None:
                clauses.append(f"{col} = %s")
                params.append(val)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM derived_layer.market_evidence{where} ORDER BY id",
                params,
            ).fetchall()
        return [dict(zip(_COLUMNS, r)) for r in rows]

    def supersede_evidence(
        self,
        old_id: int,
        kind: str,
        scope: str,
        target: str | None,
        payload_json: dict[str, Any],
        source_url: str,
        summary: str,
        accepted_at: str | None = None,
        superseded_at: str | None = None,
    ) -> int:
        """作廢舊列＋append 新列（不 UPDATE 值）；回傳新列 id。

        舊列只在 payload_json 疊加 superseded 標記（superseded=true／superseded_by=新 id／可選
        superseded_at），數值欄位原封不動保留供稽核；更正證據以新 source_url append（同 scope 同 URL
        仍受唯一約束）。old_id 不存在則拒動（不 append 任何列）。
        """
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM derived_layer.market_evidence WHERE id = %s", (old_id,)
            ).fetchone()
        if exists is None:
            raise ValueError(f"supersede 指向不存在的證據 id={old_id}")

        new_id = self.save_evidence(
            kind, scope, target, payload_json, source_url, summary, accepted_at
        )
        marker: dict[str, Any] = {"superseded": True, "superseded_by": new_id}
        if superseded_at is not None:
            marker["superseded_at"] = superseded_at
        with self._connect() as conn:
            conn.execute(
                "UPDATE derived_layer.market_evidence "
                "SET payload_json = payload_json || %s::jsonb WHERE id = %s",
                (Jsonb(marker), old_id),
            )
            conn.commit()
        return new_id

    def aggregate_for_report(
        self,
        scope: str | None = None,
        metrics: tuple[str, ...] = ("market_size", "cagr", "share", "forecast"),
        include_superseded: bool = False,
    ) -> dict[str, Any]:
        """報告用彙總：讀證據 → 轉 aggregate.py 既有邏輯 → min–max／single_source／divergent。

        不重寫彙總邏輯；DB 列映射成 aggregate.py 期待的形狀（target→market／subject）。
        預設排除已作廢（superseded）列，避免作廢數值污染彙總。
        """
        evidences = [
            self._row_to_evidence(r)
            for r in self.get_evidence(scope=scope)
            if include_superseded or not (r.get("payload_json") or {}).get("superseded")
        ]
        metric_aggregates: dict[str, Any] = {}
        for metric in metrics:
            groups = aggregate.aggregate_metric(evidences, metric)
            if groups:
                metric_aggregates[metric] = groups
        return {
            "scope": scope,
            "metrics": metric_aggregates,
            "region_trends": aggregate.aggregate_region_trends(evidences),
            "customers": aggregate.aggregate_customers(evidences),
        }

    @staticmethod
    def _row_to_evidence(row: dict[str, Any]) -> dict[str, Any]:
        """DB 列 → aggregate.py 期待的證據形狀：target 同時映射成 market 與 subject（各 kind 取其一）。"""
        return {
            "id": row["id"],
            "kind": row["kind"],
            "scope": row["scope"],
            "market": row["target"],
            "subject": row["target"],
            "source_url": row["source_url"],
            "summary": row["summary"],
            "payload_json": row.get("payload_json") or {},
        }
