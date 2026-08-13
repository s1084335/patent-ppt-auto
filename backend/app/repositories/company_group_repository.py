from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

REVIEW_STATUSES = {"suggested", "confirmed", "rejected"}
SOURCE_TYPES = {"manual", "cli_ai"}


def _notify_company_groups_changed(cur: Any, *, action: str) -> None:
    """在目前 transaction 提交後通知瀏覽器重讀集團清單。"""
    payload = {
        "kind": "data",
        "resource": "companyGroups",
        "action": action,
        "event_id": f"companyGroups:{uuid4().hex}",
    }
    cur.execute(
        "SELECT pg_notify('patent_events', %s)",
        (json.dumps(payload, separators=(",", ":")),),
    )


def normalize_group_name(value: str) -> str:
    """把集團名稱壓成一致 lookup key，避免大小寫與多空白造成重複。"""
    return " ".join((value or "").strip().lower().split())


def evaluate_suggestion_basis(
    *,
    has_confirmed_seed: bool,
    user_target: str | None,
    strong_internal_pattern: bool,
) -> dict[str, Any]:
    """判斷 CLI/AI 是否有足夠依據提出集團建議。"""
    has_basis = bool(has_confirmed_seed or (user_target or "").strip() or strong_internal_pattern)
    if not has_basis:
        return {
            "decision": "insufficient_evidence",
            "warnings": ["insufficient_evidence"],
            "can_create_confident_candidate": False,
        }
    return {
        "decision": "suggest",
        "warnings": [],
        "can_create_confident_candidate": True,
    }


def _ensure_review_only(value: str | None, *, field: str) -> str:
    """CLI/AI 建議只能以 suggested 進審核池，不可直接成為 confirmed。"""
    if value in (None, ""):
        return "suggested"
    if value != "suggested":
        raise ValueError(f"CLI suggestion cannot write {field}={value!r}; only suggested is allowed")
    return value


def validate_cli_suggestion(payload: dict[str, Any]) -> dict[str, Any]:
    """驗證並正規化 CLI/AI 集團建議 payload，輸出永遠是 review-only。"""
    group_name = str(payload.get("group_name") or "").strip()
    if not group_name:
        raise ValueError("group_name is required")

    members = payload.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("members must be a non-empty list")

    normalized: dict[str, Any] = {
        "group_name": group_name,
        "normalized_group_name": normalize_group_name(group_name),
        "review_status": _ensure_review_only(payload.get("review_status"), field="review_status"),
        "source_type": "cli_ai",
        "members": [],
    }
    if payload.get("source_type") not in (None, "", "cli_ai"):
        raise ValueError("CLI suggestion source_type must be cli_ai")

    for index, raw_member in enumerate(members):
        if not isinstance(raw_member, dict):
            raise ValueError(f"members[{index}] must be an object")
        display_name = str(raw_member.get("company_display_name") or "").strip()
        if not display_name:
            raise ValueError(f"members[{index}].company_display_name is required")
        evidence = raw_member.get("evidence_json", {})
        if not isinstance(evidence, dict):
            raise ValueError(f"members[{index}].evidence_json must be an object")
        normalized["members"].append(
            {
                "company_code": raw_member.get("company_code"),
                "company_display_name": display_name,
                "review_status": _ensure_review_only(
                    raw_member.get("review_status"), field="member.review_status"
                ),
                "source_type": "cli_ai",
                "evidence_json": evidence,
            }
        )
    return normalized


def _connect():
    """延後匯入 psycopg，讓契約測試不需要實際 DB 也能載入模組。"""
    import psycopg

    from backend.app.db.connection import get_connection_kwargs

    return psycopg.connect(**get_connection_kwargs(), connect_timeout=15)


def list_company_groups() -> list[dict[str, Any]]:
    """列出集團與成員；給治理 UI 使用。"""
    sql = """
        SELECT
            g.group_id,
            g.group_name,
            g.normalized_group_name,
            g.review_status,
            g.source_type,
            COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'member_id', m.member_id,
                        'company_code', m.company_code,
                        'company_display_name', m.company_display_name,
                        'review_status', m.review_status,
                        'source_type', m.source_type,
                        'evidence_json', m.evidence_json
                    )
                    ORDER BY m.company_display_name
                ) FILTER (WHERE m.member_id IS NOT NULL),
                '[]'::jsonb
            ) AS members
        FROM derived_layer.company_groups g
        LEFT JOIN derived_layer.company_group_members m ON m.group_id = g.group_id
        GROUP BY g.group_id
        ORDER BY g.review_status, g.group_name
    """
    from psycopg.rows import dict_row

    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql)
            return list(cur.fetchall())


def list_suggestion_candidates(*, limit: int | None = None) -> list[dict[str, Any]]:
    """一次取出尚未進入有效集團關係的已確認公司，供 AI 受控查證。"""
    sql = """
        SELECT DISTINCT ON (ca."申請人代碼")
            ca."申請人代碼" AS company_code,
            COALESCE(
                NULLIF(BTRIM(ca."公司中文名稱"), ''),
                NULLIF(BTRIM(ca."正規化名稱"), ''),
                NULLIF(BTRIM(ca."別稱"), '')
            ) AS company_display_name
        FROM derived_layer.company_aliases ca
        WHERE ca.review_status = 'confirmed'
          AND NULLIF(BTRIM(ca."申請人代碼"), '') IS NOT NULL
          AND COALESCE(
                NULLIF(BTRIM(ca."公司中文名稱"), ''),
                NULLIF(BTRIM(ca."正規化名稱"), ''),
                NULLIF(BTRIM(ca."別稱"), '')
          ) IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM derived_layer.company_group_members gm
              WHERE gm.company_code = ca."申請人代碼"
                AND gm.review_status IN ('suggested', 'confirmed')
          )
        ORDER BY ca."申請人代碼", ca.updated_at DESC, ca.id DESC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += "\n        LIMIT %s"
        params = (int(limit),)
    from psycopg.rows import dict_row

    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def create_manual_group(group_name: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    """由使用者手動建立 confirmed 集團與 confirmed 成員。"""
    name = group_name.strip()
    if not name:
        raise ValueError("group_name is required")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO derived_layer.company_groups
                    (group_name, normalized_group_name, review_status, source_type)
                VALUES (%s, %s, 'confirmed', 'manual')
                RETURNING group_id
                """,
                (name, normalize_group_name(name)),
            )
            group_id = cur.fetchone()[0]
            for member in members:
                add_group_member(
                    group_id,
                    company_code=member.get("company_code"),
                    company_display_name=member["company_display_name"],
                    connection=conn,
                    notify=False,
                )
            _notify_company_groups_changed(cur, action="create")
        conn.commit()
    return {"group_id": group_id, "group_name": name}


def rename_group(group_id: int, group_name: str) -> dict[str, Any]:
    """重新命名 confirmed/suggested 集團；不動成員與原始專利資料。"""
    name = group_name.strip()
    if not name:
        raise ValueError("group_name is required")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE derived_layer.company_groups
                SET group_name = %s,
                    normalized_group_name = %s,
                    updated_at = now()
                WHERE group_id = %s
                RETURNING group_id, group_name
                """,
                (name, normalize_group_name(name), group_id),
            )
            row = cur.fetchone()
            if row is not None:
                _notify_company_groups_changed(cur, action="rename")
        conn.commit()
    if row is None:
        raise LookupError("company group not found")
    return {"group_id": row[0], "group_name": row[1]}


def add_group_member(
    group_id: int,
    *,
    company_code: str | None,
    company_display_name: str,
    connection: Any | None = None,
    notify: bool = True,
) -> dict[str, Any]:
    """新增 manual confirmed 成員；可共用外層 transaction。"""
    display_name = company_display_name.strip()
    if not display_name:
        raise ValueError("company_display_name is required")
    sql = """
        INSERT INTO derived_layer.company_group_members
            (group_id, company_code, company_display_name, review_status, source_type)
        VALUES (%s, %s, %s, 'confirmed', 'manual')
        RETURNING member_id
    """
    conn = connection or _connect()
    close_after = connection is None
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (group_id, company_code, display_name))
            member_id = cur.fetchone()[0]
            if notify:
                _notify_company_groups_changed(cur, action="add_member")
        if close_after:
            conn.commit()
    finally:
        if close_after:
            conn.close()
    return {"member_id": member_id, "group_id": group_id}


def remove_group_member(group_id: int, member_id: int) -> dict[str, Any]:
    """移除集團成員，不刪原始公司或專利資料。"""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM derived_layer.company_group_members
                WHERE group_id = %s AND member_id = %s
                """,
                (group_id, member_id),
            )
            deleted = cur.rowcount
            if deleted:
                _notify_company_groups_changed(cur, action="remove_member")
        conn.commit()
    return {"deleted": deleted}


def ingest_cli_suggestions(items: list[dict[str, Any]]) -> dict[str, Any]:
    """寫入 CLI/AI 建議；只建立 suggested group/member。"""
    suggestions = [validate_cli_suggestion(item) for item in items]
    with _connect() as conn:
        with conn.cursor() as cur:
            inserted = 0
            for suggestion in suggestions:
                cur.execute(
                    """
                    INSERT INTO derived_layer.company_groups
                        (group_name, normalized_group_name, review_status, source_type)
                    VALUES (%s, %s, 'suggested', 'cli_ai')
                    RETURNING group_id
                    """,
                    (suggestion["group_name"], suggestion["normalized_group_name"]),
                )
                group_id = cur.fetchone()[0]
                for member in suggestion["members"]:
                    cur.execute(
                        """
                        INSERT INTO derived_layer.company_group_members
                            (
                                group_id, company_code, company_display_name,
                                review_status, source_type, evidence_json
                            )
                        VALUES (%s, %s, %s, 'suggested', 'cli_ai', %s)
                        """,
                        (
                            group_id,
                            member["company_code"],
                            member["company_display_name"],
                            Jsonb(member["evidence_json"]),
                        ),
                    )
                inserted += 1
            if inserted:
                _notify_company_groups_changed(cur, action="ingest_suggestions")
        conn.commit()
    return {"inserted": inserted}


def set_suggestion_decision(member_id: int, decision: str) -> dict[str, Any]:
    """人工確認或拒絕單筆 CLI/AI 建議，並同步父集團的生效狀態。"""
    if decision not in {"confirmed", "rejected"}:
        raise ValueError("decision must be confirmed or rejected")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE derived_layer.company_group_members
                SET review_status = %s,
                    source_type = 'manual',
                    updated_at = now()
                WHERE member_id = %s
                  AND review_status = 'suggested'
                RETURNING member_id, group_id, review_status
                """,
                (decision, member_id),
            )
            row = cur.fetchone()
            if row is not None:
                cur.execute(
                    """
                    UPDATE derived_layer.company_groups AS g
                    SET review_status = CASE
                            WHEN EXISTS (
                                SELECT 1
                                FROM derived_layer.company_group_members AS m
                                WHERE m.group_id = g.group_id
                                  AND m.review_status = 'confirmed'
                            ) THEN 'confirmed'
                            WHEN EXISTS (
                                SELECT 1
                                FROM derived_layer.company_group_members AS m
                                WHERE m.group_id = g.group_id
                                  AND m.review_status = 'suggested'
                            ) THEN 'suggested'
                            ELSE 'rejected'
                        END,
                        source_type = CASE
                            WHEN EXISTS (
                                SELECT 1
                                FROM derived_layer.company_group_members AS m
                                WHERE m.group_id = g.group_id
                                  AND m.review_status = 'suggested'
                            ) THEN g.source_type
                            ELSE 'manual'
                        END,
                        updated_at = now()
                    WHERE g.group_id = %s
                    RETURNING review_status
                    """,
                    (row[1],),
                )
                group_row = cur.fetchone()
                _notify_company_groups_changed(cur, action="review_suggestion")
        conn.commit()
    if row is None:
        raise LookupError("suggestion member not found")
    return {
        "member_id": row[0],
        "group_id": row[1],
        "review_status": row[2],
        "group_review_status": group_row[0],
    }
