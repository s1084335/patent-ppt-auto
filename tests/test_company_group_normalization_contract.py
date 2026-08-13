from __future__ import annotations

import inspect
import json

from fastapi.testclient import TestClient
import pytest
from unittest import mock

from backend.app.main import app


def test_company_group_sse_event_contract():
    """Publish only refresh metadata on the shared patent_events channel."""
    from backend.app.repositories import company_group_repository as repo

    assert hasattr(repo, "_notify_company_groups_changed")
    calls: list[tuple[str, tuple[str]]] = []

    class Cursor:
        def execute(self, sql, params):
            calls.append((sql, params))

    repo._notify_company_groups_changed(Cursor(), action="rename")

    sql, params = calls[0]
    assert "pg_notify('patent_events'" in sql
    payload = json.loads(params[0])
    assert payload["kind"] == "data"
    assert payload["resource"] == "companyGroups"
    assert payload["action"] == "rename"
    assert payload["event_id"]
    assert "company" not in payload


def test_all_company_group_mutations_publish_sse_event():
    """Every repository write that changes the registry must publish SSE."""
    from backend.app.repositories import company_group_repository as repo

    expected_actions = {
        "create_manual_group": "create",
        "rename_group": "rename",
        "add_group_member": "add_member",
        "remove_group_member": "remove_member",
        "undo_suggestion_confirmation": "undo_confirmation",
        "delete_group": "delete_group",
        "ingest_cli_suggestions": "ingest_suggestions",
        "set_suggestion_decision": "review_suggestion",
    }
    for function_name, action in expected_actions.items():
        source = inspect.getsource(getattr(repo, function_name))
        assert "_notify_company_groups_changed" in source, function_name
        assert f'action="{action}"' in source, function_name


def test_company_group_api_routes_are_registered():
    """所有治理入口都必須掛在 /api/v1/company-groups，供前端與 CLI 使用。"""
    client = TestClient(app)
    path_items = client.app.openapi()["paths"]
    paths = set(path_items)
    assert "/api/v1/company-groups" in paths
    assert "/api/v1/company-groups/{group_id}" in paths
    assert "/api/v1/company-groups/{group_id}/members" in paths
    assert "/api/v1/company-groups/{group_id}" in paths
    assert "/api/v1/company-groups/suggestions" in paths
    assert "/api/v1/company-groups/suggestions/{member_id}/confirm" in paths
    assert "/api/v1/company-groups/suggestions/{member_id}/reject" in paths
    assert "/api/v1/company-groups/suggestions/{member_id}/undo-confirm" in paths
    assert "delete" in path_items["/api/v1/company-groups/{group_id}"]
    assert "post" in path_items["/api/v1/company-groups/suggestions/{member_id}/undo-confirm"]


def test_reversal_api_delegates_to_repository():
    """撤銷與解散 endpoint 必須把正確 id 委派給 repository。"""
    from backend.app.api import company_groups as api

    client = TestClient(app)
    with mock.patch.object(
        api.repo,
        "undo_suggestion_confirmation",
        return_value={"member_id": 7, "review_status": "suggested"},
    ) as undo, mock.patch.object(
        api.repo,
        "delete_group",
        return_value={"group_id": 3, "deleted": 1},
    ) as delete:
        undo_response = client.post("/api/v1/company-groups/suggestions/7/undo-confirm")
        delete_response = client.delete("/api/v1/company-groups/3")

    assert undo_response.status_code == 200
    assert delete_response.status_code == 200
    undo.assert_called_once_with(7)
    delete.assert_called_once_with(3)


def test_reversal_api_returns_404_when_mapping_no_longer_exists():
    """重複點擊或跨瀏覽器已先處理時，API 應明確回 404。"""
    from backend.app.api import company_groups as api

    client = TestClient(app)
    with mock.patch.object(
        api.repo, "undo_suggestion_confirmation", side_effect=LookupError("missing")
    ), mock.patch.object(api.repo, "delete_group", side_effect=LookupError("missing")):
        undo_response = client.post("/api/v1/company-groups/suggestions/7/undo-confirm")
        delete_response = client.delete("/api/v1/company-groups/3")

    assert undo_response.status_code == 404
    assert delete_response.status_code == 404


def test_confirmation_api_accepts_edited_group_name_and_keeps_bodyless_compatibility():
    """確認可帶修訂名稱；舊客戶端不帶 body 時仍沿用原名稱。"""
    from backend.app.api import company_groups as api

    client = TestClient(app)
    with mock.patch.object(
        api.repo,
        "set_suggestion_decision",
        return_value={"member_id": 7, "review_status": "confirmed"},
    ) as decide:
        edited = client.post(
            "/api/v1/company-groups/suggestions/7/confirm",
            json={"group_name": "  創科集團  "},
        )
        unchanged = client.post("/api/v1/company-groups/suggestions/8/confirm")

    assert edited.status_code == 200
    assert unchanged.status_code == 200
    assert decide.call_args_list == [
        mock.call(7, "confirmed", group_name="  創科集團  "),
        mock.call(8, "confirmed", group_name=None),
    ]


def test_confirmation_api_rejects_blank_and_overlong_group_names():
    """無效名稱須在進 repository 前被 API 擋下。"""
    from backend.app.api import company_groups as api

    client = TestClient(app)
    with mock.patch.object(api.repo, "set_suggestion_decision") as decide:
        blank = client.post(
            "/api/v1/company-groups/suggestions/7/confirm",
            json={"group_name": "   "},
        )
        overlong = client.post(
            "/api/v1/company-groups/suggestions/7/confirm",
            json={"group_name": "集" * 256},
        )

    assert blank.status_code in {400, 422}
    assert overlong.status_code == 422
    decide.assert_not_called()


def test_cli_suggestion_rejects_direct_confirmed_write():
    """CLI/AI 只能寫 suggested，不可繞過人工確認直接建立 confirmed mapping。"""
    from backend.app.repositories.company_group_repository import validate_cli_suggestion

    with pytest.raises(ValueError, match="confirmed"):
        validate_cli_suggestion(
            {
                "group_name": "Creative Group",
                "review_status": "confirmed",
                "members": [
                    {
                        "company_display_name": "創科",
                        "review_status": "confirmed",
                        "evidence_json": {"basis": ["user_target"]},
                    }
                ],
            }
        )


def test_suggestion_without_basis_is_insufficient_evidence():
    """無 seed、使用者目標或高信心內部名稱規則時，不可產生 confident candidate。"""
    from backend.app.repositories.company_group_repository import evaluate_suggestion_basis

    decision = evaluate_suggestion_basis(
        has_confirmed_seed=False,
        user_target=None,
        strong_internal_pattern=False,
    )
    assert decision["decision"] == "insufficient_evidence"
    assert "insufficient_evidence" in decision["warnings"]
    assert decision["can_create_confident_candidate"] is False


def test_valid_cli_suggestion_is_review_only():
    """有效 CLI 建議也只能轉成 suggested/cli_ai 的 review-only payload。"""
    from backend.app.repositories.company_group_repository import validate_cli_suggestion

    suggestion = validate_cli_suggestion(
        {
            "group_name": "創科集團",
            "members": [
                {
                    "company_code": "WIPS-A",
                    "company_display_name": "創科",
                    "evidence_json": {"basis": ["user_target"]},
                }
            ],
        }
    )
    assert suggestion["review_status"] == "suggested"
    assert suggestion["source_type"] == "cli_ai"
    assert suggestion["members"][0]["review_status"] == "suggested"
    assert suggestion["members"][0]["source_type"] == "cli_ai"


def test_cli_suggestion_rejects_malformed_evidence():
    """CLI/AI 建議必須提供 object 型態 evidence，避免 jsonb 寫入時才失敗。"""
    from backend.app.repositories.company_group_repository import validate_cli_suggestion

    with pytest.raises(ValueError, match="evidence_json must be an object"):
        validate_cli_suggestion(
            {
                "group_name": "Creative Group",
                "members": [
                    {
                        "company_display_name": "創科",
                        "evidence_json": ["not", "an", "object"],
                    }
                ],
            }
        )


def test_report_api_rejects_invalid_scope_before_job_creation():
    """report_scope 只能是 company/group，非法值應由 API schema 直接擋下。"""
    client = TestClient(app)

    response = client.post(
        "/api/v1/reports",
        json={"patent_ids": [1], "report_scope": "workspace"},
    )

    assert response.status_code == 422


def test_suggestion_decision_updates_parent_group_review_status():
    """Confirming a suggested member must activate its parent group for reports."""
    from backend.app.repositories import company_group_repository as repo

    executed: list[str] = []
    notifications: list[dict[str, object]] = []

    class Cursor:
        def execute(self, sql, params=None):
            executed.append(sql)
            if "pg_notify('patent_events'" in sql:
                notifications.append(json.loads(params[0]))
            self._row = (
                (7, 3, "confirmed")
                if "UPDATE derived_layer.company_group_members" in sql
                else ("confirmed",)
            )
            return self

        def fetchone(self):
            return self._row

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with mock.patch.object(repo, "_connect", return_value=Connection()):
        result = repo.set_suggestion_decision(7, "confirmed")

    sql = "\n".join(executed)
    assert "UPDATE derived_layer.company_groups" in sql
    assert "review_status = 'confirmed'" in sql
    assert "review_status = 'suggested'" in sql
    assert notifications[0]["kind"] == "data"
    assert notifications[0]["resource"] == "companyGroups"
    assert notifications[0]["action"] == "review_suggestion"
    assert result["group_review_status"] == "confirmed"


def test_confirm_with_edited_name_is_atomic_and_preserves_other_member_statuses():
    """名稱更新與單筆確認共用 transaction，且不得批次確認同組其他成員。"""
    from backend.app.repositories import company_group_repository as repo

    executed: list[tuple[str, object]] = []

    class Cursor:
        def execute(self, sql, params=None):
            executed.append((sql, params))
            if "UPDATE derived_layer.company_group_members" in sql:
                self._row = (7, 3, "confirmed")
            elif "group_name =" in sql:
                self._row = (3, "創科集團")
            else:
                self._row = ("confirmed",)
            return self

        def fetchone(self):
            return self._row

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def __init__(self):
            self.commits = 0

        def cursor(self):
            return Cursor()

        def commit(self):
            self.commits += 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    connection = Connection()
    with mock.patch.object(repo, "_connect", return_value=connection):
        result = repo.set_suggestion_decision(
            7,
            "confirmed",
            group_name="  創科集團  ",
        )

    member_updates = [
        sql
        for sql, _ in executed
        if sql.strip().startswith("UPDATE derived_layer.company_group_members")
    ]
    group_name_updates = [
        (sql, params) for sql, params in executed if "group_name =" in sql
    ]
    assert len(member_updates) == 1
    assert "WHERE member_id = %s" in member_updates[0]
    assert len(group_name_updates) == 1
    assert group_name_updates[0][1] == ("創科集團", "創科集團", 3)
    assert connection.commits == 1
    assert result["group_name"] == "創科集團"


@pytest.mark.parametrize(
    ("decision", "group_name", "message"),
    [
        ("rejected", "創科集團", "only be changed when confirming"),
        ("confirmed", "   ", "group_name is required"),
        ("confirmed", "集" * 256, "must not exceed 255"),
    ],
)
def test_suggestion_decision_rejects_invalid_edited_names_before_db_write(
    decision,
    group_name,
    message,
):
    """Repository 自身也必須守住名稱邊界，不能只依賴 HTTP schema。"""
    from backend.app.repositories import company_group_repository as repo

    with mock.patch.object(repo, "_connect") as connect:
        with pytest.raises(ValueError, match=message):
            repo.set_suggestion_decision(
                7,
                decision,
                group_name=group_name,
            )

    connect.assert_not_called()


def test_confirm_with_edited_name_rolls_back_when_parent_group_is_missing():
    """父 group 異常消失時不得提交已確認 member。"""
    from backend.app.repositories import company_group_repository as repo

    class Cursor:
        def __init__(self):
            self._row = None

        def execute(self, sql, params=None):
            self._row = (
                (7, 3, "confirmed")
                if "UPDATE derived_layer.company_group_members" in sql
                else None
            )
            return self

        def fetchone(self):
            return self._row

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def __init__(self):
            self.commits = 0

        def cursor(self):
            return Cursor()

        def commit(self):
            self.commits += 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    connection = Connection()
    with mock.patch.object(repo, "_connect", return_value=connection):
        with pytest.raises(LookupError, match="company group not found"):
            repo.set_suggestion_decision(7, "confirmed", group_name="創科集團")

    assert connection.commits == 0


def test_undo_confirmation_restores_ai_suggestion_and_preserves_evidence():
    """撤銷只改狀態與來源，不得覆寫原 AI 證據。"""
    from backend.app.repositories import company_group_repository as repo

    executed: list[tuple[str, object]] = []

    class Cursor:
        def execute(self, sql, params=None):
            executed.append((sql, params))
            self._row = (
                (7, 3, "suggested")
                if "UPDATE derived_layer.company_group_members" in sql
                else ("suggested",)
            )
            return self

        def fetchone(self):
            return self._row

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with mock.patch.object(repo, "_connect", return_value=Connection()):
        result = repo.undo_suggestion_confirmation(7)

    sql = "\n".join(statement for statement, _ in executed)
    assert "review_status = 'suggested'" in sql
    assert "source_type = 'cli_ai'" in sql
    assert "evidence_json ? 'sources'" in sql
    assert "evidence_json =" not in sql
    assert result["review_status"] == "suggested"
    assert result["group_review_status"] == "suggested"


def test_delete_group_removes_only_group_mapping_and_relies_on_member_cascade():
    """解散只刪 group parent；member 由 FK cascade，不能碰公司或專利資料。"""
    from backend.app.repositories import company_group_repository as repo

    executed: list[tuple[str, object]] = []

    class Cursor:
        def execute(self, sql, params=None):
            executed.append((sql, params))
            self._row = (3,) if "DELETE FROM derived_layer.company_groups" in sql else None
            return self

        def fetchone(self):
            return self._row

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with mock.patch.object(repo, "_connect", return_value=Connection()):
        result = repo.delete_group(3)

    sql = "\n".join(statement for statement, _ in executed)
    assert "DELETE FROM derived_layer.company_groups" in sql
    assert "DELETE FROM derived_layer.company_group_members" not in sql
    assert "company_aliases" not in sql
    assert "delete from core_layer.patent" not in sql.lower()
    assert "delete from derived_layer.report_patent" not in sql.lower()
    assert result == {"group_id": 3, "deleted": 1}


def test_reversal_repository_rejects_missing_targets():
    """不存在的 group/member 不得回報成功，也不得產生假 SSE。"""
    from backend.app.repositories import company_group_repository as repo

    class Cursor:
        def execute(self, sql, params=None):
            return self

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    cursor = Cursor()
    with pytest.raises(LookupError, match="company group not found"):
        repo._sync_group_review_status(cursor, 3)
    with mock.patch.object(repo, "_connect", return_value=Connection()):
        with pytest.raises(LookupError, match="company group not found"):
            repo.delete_group(3)
        with pytest.raises(LookupError, match="confirmed AI suggestion member not found"):
            repo.undo_suggestion_confirmation(7)


def test_ingest_existing_group_suggestion_adds_only_a_suggested_member():
    """指定既有 group 時不得 INSERT parent，也不得改名或確認 member。"""
    from backend.app.repositories import company_group_repository as repo

    executed: list[tuple[str, object]] = []

    class Cursor:
        def execute(self, sql, params=None):
            executed.append((sql, params))
            if "SELECT group_id, group_name" in sql:
                self._row = (9, "創科集團")
            elif "INSERT INTO derived_layer.company_group_members" in sql:
                self._row = (101,)
            else:
                self._row = None
            return self

        def fetchone(self):
            return self._row

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def __init__(self):
            self.commits = 0

        def cursor(self):
            return Cursor()

        def commit(self):
            self.commits += 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    connection = Connection()
    suggestion = {
        "target_group_id": 9,
        "group_name": "創科集團",
        "members": [
            {
                "company_code": "C001",
                "company_display_name": "新子公司",
                "evidence_json": {
                    "confidence": "high",
                    "sources": [{"url": "https://example.com/sub"}],
                },
            }
        ],
    }

    with mock.patch.object(repo, "_connect", return_value=connection):
        result = repo.ingest_cli_suggestions([suggestion])

    sql = "\n".join(statement for statement, _ in executed)
    member_params = next(
        params
        for statement, params in executed
        if "INSERT INTO derived_layer.company_group_members" in statement
    )
    assert "INSERT INTO derived_layer.company_groups" not in sql
    assert "UPDATE derived_layer.company_groups" not in sql
    assert member_params[:3] == (9, "C001", "新子公司")
    assert "'suggested', 'cli_ai'" in sql
    notify_params = next(
        params for statement, params in executed if "pg_notify('patent_events'" in statement
    )
    notify_payload = json.loads(notify_params[0])
    assert notify_payload["kind"] == "data"
    assert notify_payload["resource"] == "companyGroups"
    assert notify_payload["action"] == "ingest_suggestions"
    assert connection.commits == 1
    assert result == {"inserted": 1}


def test_existing_group_candidates_include_only_confirmed_groups_and_members():
    """送給 CLI 的既有目標及種子成員都必須受 confirmed 條件控制。"""
    from backend.app.repositories import company_group_repository as repo

    executed: list[str] = []

    class Cursor:
        def execute(self, sql, params=None):
            executed.append(sql)
            return self

        def fetchall(self):
            return [{"group_id": 9, "group_name": "創科集團", "confirmed_members": []}]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def cursor(self, **_kwargs):
            return Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with mock.patch.object(repo, "_connect", return_value=Connection()):
        rows = repo.list_confirmed_group_candidates()

    sql = "\n".join(executed)
    assert "g.review_status = 'confirmed'" in sql
    assert "m.review_status = 'confirmed'" in sql
    assert rows[0]["group_id"] == 9


@pytest.mark.parametrize("invalid_target", [True, "not-an-id", 0])
def test_cli_suggestion_rejects_invalid_target_group_id(invalid_target):
    """repository 邊界只接受 optional positive integer target。"""
    from backend.app.repositories import company_group_repository as repo

    with pytest.raises(ValueError, match="target_group_id must be a positive integer"):
        repo.validate_cli_suggestion(
            {
                "target_group_id": invalid_target,
                "group_name": "創科集團",
                "members": [{"company_display_name": "新子公司"}],
            }
        )


def test_ingest_new_group_suggestion_still_creates_suggested_parent():
    """未指定 target 時維持原本建立待審 parent 的流程。"""
    from backend.app.repositories import company_group_repository as repo

    executed: list[str] = []

    class Cursor:
        def execute(self, sql, params=None):
            executed.append(sql)
            self._row = (20,) if "INSERT INTO derived_layer.company_groups" in sql else None
            return self

        def fetchone(self):
            return self._row

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    suggestion = {
        "group_name": "新集團",
        "members": [{"company_code": "C001", "company_display_name": "新子公司"}],
    }
    with mock.patch.object(repo, "_connect", return_value=Connection()):
        result = repo.ingest_cli_suggestions([suggestion])

    sql = "\n".join(executed)
    assert "INSERT INTO derived_layer.company_groups" in sql
    assert "INSERT INTO derived_layer.company_group_members" in sql
    assert result == {"inserted": 1}


def test_ingest_rejects_missing_or_nonconfirmed_target_group():
    """即使繞過 runner 直打 ingest，未知或非 confirmed target 仍不可寫入。"""
    from backend.app.repositories import company_group_repository as repo

    class Cursor:
        def execute(self, sql, params=None):
            return self

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def __init__(self):
            self.commits = 0

        def cursor(self):
            return Cursor()

        def commit(self):
            self.commits += 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    connection = Connection()
    suggestion = {
        "target_group_id": 999,
        "group_name": "不存在集團",
        "members": [
            {
                "company_code": "C001",
                "company_display_name": "新子公司",
                "evidence_json": {"sources": [{"url": "https://example.com/sub"}]},
            }
        ],
    }

    with mock.patch.object(repo, "_connect", return_value=connection):
        with pytest.raises(ValueError, match="confirmed target group not found"):
            repo.ingest_cli_suggestions([suggestion])

    assert connection.commits == 0
