from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb


#: 母體範圍豁免（見 backend/app/db/population_scope.py）。
#: ⚠ 理由是給複核的人看的——「忘了接母體」與「刻意全庫」在程式碼上長得一樣。
POPULATION_SCOPE_EXEMPT = {
    "list_company_groups":
        "公司治理跨 workspace：集團定義不隸屬任一 workspace",
    "list_confirmed_group_candidates":
        "同上：候選來自全庫已確認公司",
}

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


def _sync_group_review_status(cur: Any, group_id: int) -> str:
    """依現有成員狀態重算父集團狀態，供確認與撤銷共用。"""
    cur.execute(
        """
        UPDATE derived_layer.company_groups AS g
        SET review_status = CASE
                WHEN EXISTS (
                    SELECT 1 FROM derived_layer.company_group_members AS m
                    WHERE m.group_id = g.group_id AND m.review_status = 'confirmed'
                ) THEN 'confirmed'
                WHEN EXISTS (
                    SELECT 1 FROM derived_layer.company_group_members AS m
                    WHERE m.group_id = g.group_id AND m.review_status = 'suggested'
                ) THEN 'suggested'
                ELSE 'rejected'
            END,
            source_type = CASE
                WHEN EXISTS (
                    SELECT 1 FROM derived_layer.company_group_members AS m
                    WHERE m.group_id = g.group_id AND m.review_status = 'suggested'
                ) THEN 'cli_ai'
                ELSE 'manual'
            END,
            updated_at = now()
        WHERE g.group_id = %s
        RETURNING review_status
        """,
        (group_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise LookupError("company group not found")
    return row[0]


def _ensure_company(cur: Any, company_code: str | None) -> None:
    """確保代碼已登記於 `derived_layer.companies`（集團成員的外鍵目標）。

    ⚠ 為什麼在這裡登記而不是在建立代碼的每一處：`companies` 這一版的用途只有
    「當集團成員的外鍵目標」（0053，使用者裁決範圍「丙」）。別稱表沒有外鍵指過來，
    所以不必在 12 處寫入點各補一次；集團成員是唯一需要它存在的地方。

    ⚠ 代價（知情選擇）：`companies` 在回填後會**惰性補齊**——沒進過集團的新代碼
    不在表裡。它是「外鍵層認得的代碼」而非「全部代碼」的完整登記簿。
    要變成完整登記簿，得補上別稱寫入路徑，那是另一輪的事。
    """
    if not company_code or not str(company_code).strip():
        return
    cur.execute(
        "INSERT INTO derived_layer.companies (company_code) VALUES (%s) "
        "ON CONFLICT (company_code) DO NOTHING",
        (str(company_code).strip(),),
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


def _optional_positive_id(value: Any, *, field: str) -> int | None:
    """把 optional ID 正規化為正整數，明確拒絕 Python bool。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")  # noqa: TRY004
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if normalized <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return normalized


def _normalize_cli_member(raw_member: Any, *, index: int) -> dict[str, Any]:
    """驗證一筆 CLI 建議成員並固定為 suggested/cli_ai。"""
    if not isinstance(raw_member, dict):
        raise ValueError(f"members[{index}] must be an object")  # noqa: TRY004
    display_name = str(raw_member.get("company_display_name") or "").strip()
    if not display_name:
        raise ValueError(f"members[{index}].company_display_name is required")
    evidence = raw_member.get("evidence_json", {})
    if not isinstance(evidence, dict):
        raise ValueError(  # noqa: TRY004
            f"members[{index}].evidence_json must be an object"
        )
    return {
        "company_code": raw_member.get("company_code"),
        "company_display_name": display_name,
        "review_status": _ensure_review_only(
            raw_member.get("review_status"), field="member.review_status"
        ),
        "source_type": "cli_ai",
        "evidence_json": evidence,
    }


def validate_cli_suggestion(payload: dict[str, Any]) -> dict[str, Any]:
    """驗證並正規化 CLI/AI 集團建議 payload，輸出永遠是 review-only。"""
    target_group_id = _optional_positive_id(
        payload.get("target_group_id"), field="target_group_id"
    )
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
        "target_group_id": target_group_id,
        "members": [],
    }
    if payload.get("source_type") not in (None, "", "cli_ai"):
        raise ValueError("CLI suggestion source_type must be cli_ai")

    normalized["members"] = [
        _normalize_cli_member(raw_member, index=index)
        for index, raw_member in enumerate(members)
    ]
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


def list_confirmed_group_candidates() -> list[dict[str, Any]]:
    """列出 CLI 可指向的已確認集團及已確認種子成員。"""
    sql = """
        SELECT
            g.group_id,
            g.group_name,
            COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'company_code', m.company_code,
                        'company_display_name', m.company_display_name
                    )
                    ORDER BY m.company_display_name
                ) FILTER (
                    WHERE m.member_id IS NOT NULL
                      AND m.review_status = 'confirmed'
                ),
                '[]'::jsonb
            ) AS confirmed_members
        FROM derived_layer.company_groups g
        LEFT JOIN derived_layer.company_group_members m ON m.group_id = g.group_id
        WHERE g.review_status = 'confirmed'
        GROUP BY g.group_id, g.group_name
        ORDER BY g.group_name
    """
    from psycopg.rows import dict_row

    with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
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
            _ensure_company(cur, company_code)
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


def delete_group(group_id: int) -> dict[str, Any]:
    """解散集團 mapping；成員由 FK cascade 刪除，不動公司或專利資料。"""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM derived_layer.company_groups
                WHERE group_id = %s
                RETURNING group_id
                """,
                (group_id,),
            )
            row = cur.fetchone()
            if row is not None:
                _notify_company_groups_changed(cur, action="delete_group")
        conn.commit()
    if row is None:
        raise LookupError("company group not found")
    return {"group_id": row[0], "deleted": 1}


def ingest_cli_suggestions(items: list[dict[str, Any]]) -> dict[str, Any]:
    """寫入 CLI/AI 建議；新集團或既有集團成員都只進 suggested。"""
    suggestions = [validate_cli_suggestion(item) for item in items]
    with _connect() as conn:
        with conn.cursor() as cur:
            inserted = 0
            for suggestion in suggestions:
                target_group_id = suggestion["target_group_id"]
                if target_group_id is not None:
                    # 寫入前鎖定並重驗，避免 CLI 取得清單後集團狀態已改變。
                    cur.execute(
                        """
                        SELECT group_id, group_name
                        FROM derived_layer.company_groups
                        WHERE group_id = %s
                          AND review_status = 'confirmed'
                        FOR UPDATE
                        """,
                        (target_group_id,),
                    )
                    target_group = cur.fetchone()
                    if target_group is None:
                        raise ValueError("confirmed target group not found")
                    group_id = target_group[0]
                else:
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
                    # AI 建議的成員同樣要有外鍵目標——建議階段就登記，
                    # 否則使用者確認時才失敗（那時錯誤離成因已經很遠）。
                    _ensure_company(cur, member["company_code"])
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


def set_suggestion_decision(
    member_id: int,
    decision: str,
    *,
    group_name: str | None = None,
) -> dict[str, Any]:
    """人工確認或拒絕單筆 CLI/AI 建議，並同步父集團的生效狀態。"""
    if decision not in {"confirmed", "rejected"}:
        raise ValueError("decision must be confirmed or rejected")
    edited_name = None
    if group_name is not None:
        edited_name = group_name.strip()
        if decision != "confirmed":
            raise ValueError("group_name can only be changed when confirming")
        if not edited_name:
            raise ValueError("group_name is required")
        if len(edited_name) > 255:
            raise ValueError("group_name must not exceed 255 characters")
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
                if edited_name is not None:
                    cur.execute(
                        """
                        UPDATE derived_layer.company_groups
                        SET group_name = %s,
                            normalized_group_name = %s,
                            updated_at = now()
                        WHERE group_id = %s
                        RETURNING group_id, group_name
                        """,
                        (edited_name, normalize_group_name(edited_name), row[1]),
                    )
                    if cur.fetchone() is None:
                        raise LookupError("company group not found")
                group_review_status = _sync_group_review_status(cur, row[1])
                _notify_company_groups_changed(cur, action="review_suggestion")
        conn.commit()
    if row is None:
        raise LookupError("suggestion member not found")
    return {
        "member_id": row[0],
        "group_id": row[1],
        "review_status": row[2],
        "group_review_status": group_review_status,
        "group_name": edited_name,
    }


def undo_suggestion_confirmation(member_id: int) -> dict[str, Any]:
    """把已確認的 AI 建議退回 suggested，保留 evidence_json 供再次審核。"""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE derived_layer.company_group_members
                SET review_status = 'suggested',
                    source_type = 'cli_ai',
                    updated_at = now()
                WHERE member_id = %s
                  AND review_status = 'confirmed'
                  AND jsonb_typeof(evidence_json -> 'sources') = 'array'
                  AND evidence_json ? 'sources'
                RETURNING member_id, group_id, review_status
                """,
                (member_id,),
            )
            row = cur.fetchone()
            if row is not None:
                group_review_status = _sync_group_review_status(cur, row[1])
                _notify_company_groups_changed(cur, action="undo_confirmation")
        conn.commit()
    if row is None:
        raise LookupError("confirmed AI suggestion member not found")
    return {
        "member_id": row[0],
        "group_id": row[1],
        "review_status": row[2],
        "group_review_status": group_review_status,
    }
