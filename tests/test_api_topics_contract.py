"""TDD Contract Tests for Topic API (Red -> Green phase).

Tests the HTTP contract, dependency injection, and FakeTopicRepository.
No real SQL/DB — routes are implemented minimally to pass contract.
"""
from __future__ import annotations

import warnings

from starlette.exceptions import StarletteDeprecationWarning

# 精準過濾 FastAPI TestClient 的單一 StarletteDeprecationWarning
warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=StarletteDeprecationWarning,
    module=r"fastapi\.testclient",
)

import ast
import inspect
import unittest
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.repositories.topic_repository import (
    TopicNotFoundError,
    WorkspaceNotFoundError,
    InvalidTopicOperationError,
    TopicRepositoryUnavailableError,
    ListTopicsResult,
    TopicDict,
    ListMergeSuggestionsResult,
    MergeSuggestionItem,
    MergeQueueResult,
    MergeHistoryItem,
    UnmergeQueueResult,
    RenameResult,
)


PREFIX = "/api/v1"
client = TestClient(app)


# ── Fake Repository (test-only) ────────────────────────────────


@dataclass
class FakeTopic:
    topic_key: str
    label: str
    summary: str
    doc_count: int
    keywords: list[str]
    label_source: str
    display_order: int
    status: str
    merged_into_topic_key: str | None

    def to_dict(self) -> TopicDict:
        return TopicDict(
            topic_key=self.topic_key,
            label=self.label,
            summary=self.summary,
            doc_count=self.doc_count,
            keywords=self.keywords,
            label_source=self.label_source,
            display_order=self.display_order,
            status=self.status,
            merged_into_topic_key=self.merged_into_topic_key,
        )


@dataclass
class FakeMergeSuggestion:
    topic_keys: list[str]
    labels: list[str]
    distance: float

    def to_dict(self) -> MergeSuggestionItem:
        return MergeSuggestionItem(
            topic_keys=self.topic_keys,
            labels=self.labels,
            distance=self.distance,
        )


@dataclass
class FakeMergeHistoryItem:
    merge_run_id: int
    source_topics: list[str]
    result_topic: str
    can_unmerge: bool
    blocked_reason: str | None

    def to_dict(self) -> MergeHistoryItem:
        return MergeHistoryItem(
            merge_run_id=self.merge_run_id,
            source_topics=self.source_topics,
            result_topic=self.result_topic,
            can_unmerge=self.can_unmerge,
            blocked_reason=self.blocked_reason,
        )


class FakeTopicRepository:
    """Test-only repository that records calls and returns canned data."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []
        self._topics: dict[tuple[int, str], list[FakeTopic]] = {}
        self._merge_suggestions: dict[tuple[int, str], list[FakeMergeSuggestion]] = {}
        self._merge_history: dict[tuple[int, str], list[FakeMergeHistoryItem]] = {}
        self._next_merge_run_id = 20
        self._topic_keys: set[str] = {"topic-1", "topic-2", "topic-3"}

    def _record(self, method: str, *args, **kwargs):
        self.calls.append((method, args, kwargs))

    def list_topics(
        self,
        workspace_id: int,
        source_field: str,
    ) -> ListTopicsResult:
        self._record("list_topics", workspace_id, source_field)
        key = (workspace_id, source_field)
        topics = self._topics.get(key, [])
        return ListTopicsResult(
            workspace_id=workspace_id,
            source_field=source_field,
            run_id=10,
            topics=[t.to_dict() for t in topics],
        )

    def list_merge_suggestions(
        self,
        workspace_id: int,
        source_field: str,
    ) -> ListMergeSuggestionsResult:
        self._record("list_merge_suggestions", workspace_id, source_field)
        key = (workspace_id, source_field)
        suggestions = self._merge_suggestions.get(key, [])
        return ListMergeSuggestionsResult(
            workspace_id=workspace_id,
            source_field=source_field,
            suggestions=[s.to_dict() for s in suggestions],
        )

    def queue_merge(
        self,
        workspace_id: int,
        source_field: str,
        topic_keys: list[str],
        label: str | None,
        requested_by: str,
        request_key: str | None,
    ) -> MergeQueueResult:
        self._record("queue_merge", workspace_id, source_field, topic_keys, label, requested_by, request_key)
        if len(topic_keys) != 2 or topic_keys[0] == topic_keys[1]:
            raise InvalidTopicOperationError("merge requires exactly two distinct topic_keys")
        if not all(k in self._topic_keys for k in topic_keys):
            raise TopicNotFoundError("topic not found")
        run_id = self._next_merge_run_id
        self._next_merge_run_id += 1
        return MergeQueueResult(
            run_id=run_id,
            workspace_id=workspace_id,
            operation="topic_merge",
            status="queued",
        )

    def list_merge_history(
        self,
        workspace_id: int,
        source_field: str,
    ) -> list[MergeHistoryItem]:
        self._record("list_merge_history", workspace_id, source_field)
        key = (workspace_id, source_field)
        return [h.to_dict() for h in self._merge_history.get(key, [])]

    def queue_unmerge(
        self,
        workspace_id: int,
        source_field: str,
        merge_run_id: int,
        requested_by: str,
        request_key: str | None,
    ) -> UnmergeQueueResult:
        self._record("queue_unmerge", workspace_id, source_field, merge_run_id, requested_by, request_key)
        if merge_run_id not in {20, 21}:
            raise InvalidTopicOperationError("merge_run not found or not unmergeable")
        run_id = self._next_merge_run_id
        self._next_merge_run_id += 1
        return UnmergeQueueResult(
            run_id=run_id,
            workspace_id=workspace_id,
            operation="topic_unmerge",
            status="queued",
        )

    def rename_topic(
        self,
        workspace_id: int,
        topic_key: str,
        label: str,
        renamed_by: str,
    ) -> RenameResult:
        self._record("rename_topic", workspace_id, topic_key, label, renamed_by)
        if topic_key not in self._topic_keys:
            raise TopicNotFoundError("topic not found")
        if not label.strip():
            raise InvalidTopicOperationError("label cannot be empty")
        return RenameResult(
            topic_key=topic_key,
            label=label,
            label_source="manual",
        )


# ── Test Cases ─────────────────────────────────────────────────


class TopicApiContractTests(unittest.TestCase):
    """Contract tests for Topic API endpoints."""

    def setUp(self):
        self.fake_repo = FakeTopicRepository()
        # Override dependency
        from backend.app.api.topics import get_topic_repository

        app.dependency_overrides[get_topic_repository] = lambda: self.fake_repo

    def tearDown(self):
        app.dependency_overrides.clear()

    # ── 1. GET /workspaces/{workspace_id}/topics ───────────────
    def test_get_topics_happy_path(self):
        self.fake_repo._topics[(1, "wips_independent_claims")] = [
            FakeTopic(
                topic_key="topic-1",
                label="Test Topic",
                summary="Summary",
                doc_count=20,
                keywords=["kw1", "kw2"],
                label_source="model",
                display_order=1,
                status="active",
                merged_into_topic_key=None,
            )
        ]
        resp = client.get(f"{PREFIX}/workspaces/1/topics?source_field=wips_independent_claims")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["workspace_id"], 1)
        self.assertEqual(body["source_field"], "wips_independent_claims")
        self.assertEqual(body["run_id"], 10)
        self.assertEqual(len(body["topics"]), 1)
        t = body["topics"][0]
        self.assertEqual(t["topic_key"], "topic-1")
        self.assertEqual(t["label_source"], "model")
        self.assertEqual(t["status"], "active")
        # Verify repo called correctly
        self.assertIn(("list_topics", (1, "wips_independent_claims"), {}), self.fake_repo.calls)

    def test_get_topics_invalid_source_field_422(self):
        resp = client.get(f"{PREFIX}/workspaces/1/topics?source_field=invalid_field")
        self.assertEqual(resp.status_code, 422)

    def test_get_topics_workspace_not_found_404(self):
        self.fake_repo._topics[(999999, "wips_independent_claims")] = []
        # Need to raise WorkspaceNotFoundError from repo
        original = self.fake_repo.list_topics

        def raising(*args, **kwargs):
            raise WorkspaceNotFoundError("workspace not found")

        self.fake_repo.list_topics = raising
        resp = client.get(f"{PREFIX}/workspaces/999999/topics?source_field=wips_independent_claims")
        self.assertEqual(resp.status_code, 404)

    def test_get_topics_repo_unavailable_503(self):
        app.dependency_overrides.clear()
        # No override = default raises TopicRepositoryUnavailableError -> 503
        resp = client.get(f"{PREFIX}/workspaces/1/topics?source_field=wips_independent_claims")
        self.assertEqual(resp.status_code, 503)

    # ── 2. GET /workspaces/{workspace_id}/topics/merge-suggestions ──
    def test_get_merge_suggestions_happy_path(self):
        self.fake_repo._merge_suggestions[(1, "wips_independent_claims")] = [
            FakeMergeSuggestion(
                topic_keys=["topic-1", "topic-2"],
                labels=["Label 1", "Label 2"],
                distance=0.12,
            )
        ]
        resp = client.get(
            f"{PREFIX}/workspaces/1/topics/merge-suggestions?source_field=wips_independent_claims"
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["workspace_id"], 1)
        self.assertEqual(body["source_field"], "wips_independent_claims")
        self.assertEqual(len(body["suggestions"]), 1)
        s = body["suggestions"][0]
        self.assertEqual(s["topic_keys"], ["topic-1", "topic-2"])
        self.assertEqual(s["distance"], 0.12)

    def test_get_merge_suggestions_invalid_source_field_422(self):
        resp = client.get(f"{PREFIX}/workspaces/1/topics/merge-suggestions?source_field=bad")
        self.assertEqual(resp.status_code, 422)

    # ── 3. POST /workspaces/{workspace_id}/topics/merge ──────────
    def test_post_merge_happy_path_202(self):
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/merge",
            json={
                "source_field": "wips_independent_claims",
                "topic_keys": ["topic-1", "topic-2"],
                "label": None,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["workspace_id"], 1)
        self.assertEqual(body["operation"], "topic_merge")
        self.assertEqual(body["status"], "queued")
        self.assertIn("run_id", body)
        # Verify repo called
        self.assertIn(
            ("queue_merge", (1, "wips_independent_claims", ["topic-1", "topic-2"], None, "web-user", None), {}),
            self.fake_repo.calls,
        )

    def test_post_merge_less_than_two_topics_422(self):
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/merge",
            json={
                "source_field": "wips_independent_claims",
                "topic_keys": ["topic-1"],
                "label": None,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_post_merge_more_than_two_topics_422(self):
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/merge",
            json={
                "source_field": "wips_independent_claims",
                "topic_keys": ["topic-1", "topic-2", "topic-3"],
                "label": None,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_post_merge_duplicate_topic_keys_422(self):
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/merge",
            json={
                "source_field": "wips_independent_claims",
                "topic_keys": ["topic-1", "topic-1"],
                "label": None,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_post_merge_invalid_source_field_422(self):
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/merge",
            json={
                "source_field": "bad_field",
                "topic_keys": ["topic-1", "topic-2"],
                "label": None,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_post_merge_topic_not_found_404(self):
        # Make repo raise TopicNotFoundError
        def raising(*args, **kwargs):
            raise TopicNotFoundError("topic not found")

        self.fake_repo.queue_merge = raising
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/merge",
            json={
                "source_field": "wips_independent_claims",
                "topic_keys": ["topic-1", "topic-99"],
                "label": None,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 404)

    def test_post_merge_domain_conflict_409(self):
        def raising(*args, **kwargs):
            raise InvalidTopicOperationError("cannot merge merged topic")

        self.fake_repo.queue_merge = raising
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/merge",
            json={
                "source_field": "wips_independent_claims",
                "topic_keys": ["topic-1", "topic-2"],
                "label": None,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 409)

    def test_post_merge_repo_unavailable_503(self):
        app.dependency_overrides.clear()
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/merge",
            json={
                "source_field": "wips_independent_claims",
                "topic_keys": ["topic-1", "topic-2"],
                "label": None,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 503)

    # ── 4. GET /workspaces/{workspace_id}/topics/merge-history ───
    def test_get_merge_history_happy_path(self):
        self.fake_repo._merge_history[(1, "wips_independent_claims")] = [
            FakeMergeHistoryItem(
                merge_run_id=20,
                source_topics=["topic-1", "topic-2"],
                result_topic="topic-1",
                can_unmerge=True,
                blocked_reason=None,
            )
        ]
        resp = client.get(
            f"{PREFIX}/workspaces/1/topics/merge-history?source_field=wips_independent_claims"
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["merge_run_id"], 20)
        self.assertEqual(body[0]["can_unmerge"], True)

    def test_get_merge_history_invalid_source_field_422(self):
        resp = client.get(f"{PREFIX}/workspaces/1/topics/merge-history?source_field=bad")
        self.assertEqual(resp.status_code, 422)

    # ── 5. POST /workspaces/{workspace_id}/topics/unmerge ────────
    def test_post_unmerge_happy_path_202(self):
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/unmerge",
            json={
                "source_field": "wips_independent_claims",
                "merge_run_id": 20,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["operation"], "topic_unmerge")
        self.assertEqual(body["status"], "queued")

    def test_post_unmerge_invalid_source_field_422(self):
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/unmerge",
            json={
                "source_field": "bad_field",
                "merge_run_id": 20,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_post_unmerge_not_found_404(self):
        def raising(*args, **kwargs):
            raise InvalidTopicOperationError("merge_run not found")

        self.fake_repo.queue_unmerge = raising
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/unmerge",
            json={
                "source_field": "wips_independent_claims",
                "merge_run_id": 999,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 409)

    def test_post_unmerge_repo_unavailable_503(self):
        app.dependency_overrides.clear()
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/unmerge",
            json={
                "source_field": "wips_independent_claims",
                "merge_run_id": 20,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 503)

    # ── 6. PATCH /workspaces/{workspace_id}/topics/{topic_key} ───
    def test_patch_rename_topic_happy_path(self):
        resp = client.patch(
            f"{PREFIX}/workspaces/1/topics/topic-1",
            json={"label": "人工主題名稱", "renamed_by": "web-user"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["topic_key"], "topic-1")
        self.assertEqual(body["label"], "人工主題名稱")
        self.assertEqual(body["label_source"], "manual")
        # Verify repo called
        self.assertIn(
            ("rename_topic", (1, "topic-1", "人工主題名稱", "web-user"), {}),
            self.fake_repo.calls,
        )

    def test_patch_rename_empty_label_422(self):
        resp = client.patch(
            f"{PREFIX}/workspaces/1/topics/topic-1",
            json={"label": "   ", "renamed_by": "web-user"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_patch_rename_topic_not_found_404(self):
        def raising(*args, **kwargs):
            raise TopicNotFoundError("topic not found")

        self.fake_repo.rename_topic = raising
        resp = client.patch(
            f"{PREFIX}/workspaces/1/topics/topic-99",
            json={"label": "新名稱", "renamed_by": "web-user"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_patch_rename_repo_unavailable_503(self):
        app.dependency_overrides.clear()
        resp = client.patch(
            f"{PREFIX}/workspaces/1/topics/topic-1",
            json={"label": "新名稱", "renamed_by": "web-user"},
        )
        self.assertEqual(resp.status_code, 503)

    # ── Cross-cutting: no direct psycopg import in API ───────────
    def test_api_module_no_psycopg_import(self):
        import backend.app.api.topics as topics_module

        # Use AST to check for actual import statements, not string matching
        source = inspect.getsource(topics_module)
        tree = ast.parse(source)
        has_psycopg_import = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "psycopg" or alias.name.startswith("psycopg."):
                        has_psycopg_import = True
                        break
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module == "psycopg" or node.module.startswith("psycopg.")):
                    has_psycopg_import = True
                    break
        self.assertFalse(has_psycopg_import, "topics.py must not import psycopg")

    # ── 7. 安全性：未預期例外不洩漏敏感資訊 ──────────────────────
    def test_unexpected_exception_returns_500_no_leak(self):
        """Repo 拋出非 domain exception 時回 500，且不洩漏原始錯誤訊息。"""
        def raising(*args, **kwargs):
            raise RuntimeError("database password=secret123 connection failed")

        self.fake_repo.list_topics = raising
        resp = client.get(f"{PREFIX}/workspaces/1/topics?source_field=wips_independent_claims")
        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        # 不應包含敏感字串
        self.assertNotIn("password", str(body).lower())
        self.assertNotIn("secret123", str(body))
        # 應為通用錯誤訊息
        self.assertIn("detail", body)

    def test_unexpected_exception_in_merge_returns_500_no_leak(self):
        def raising(*args, **kwargs):
            raise ValueError("internal config: api_key=sk-live-xyz")

        self.fake_repo.queue_merge = raising
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/merge",
            json={
                "source_field": "wips_independent_claims",
                "topic_keys": ["topic-1", "topic-2"],
                "label": None,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertNotIn("api_key", str(body))
        self.assertNotIn("sk-live", str(body))

    def test_unexpected_exception_in_rename_returns_500_no_leak(self):
        def raising(*args, **kwargs):
            raise KeyError("secret_token=abc123")

        self.fake_repo.rename_topic = raising
        resp = client.patch(
            f"{PREFIX}/workspaces/1/topics/topic-1",
            json={"label": "新名稱", "renamed_by": "web-user"},
        )
        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertNotIn("secret_token", str(body))
        self.assertNotIn("abc123", str(body))

    # ── 8. 額外 422 驗證 ───────────────────────────────────────
    def test_get_topics_workspace_id_zero_or_negative_422(self):
        resp = client.get(f"{PREFIX}/workspaces/0/topics?source_field=wips_independent_claims")
        self.assertEqual(resp.status_code, 422)
        resp = client.get(f"{PREFIX}/workspaces/-1/topics?source_field=wips_independent_claims")
        self.assertEqual(resp.status_code, 422)

    def test_get_merge_suggestions_workspace_id_zero_or_negative_422(self):
        resp = client.get(f"{PREFIX}/workspaces/0/topics/merge-suggestions?source_field=wips_independent_claims")
        self.assertEqual(resp.status_code, 422)

    def test_get_merge_history_workspace_id_zero_or_negative_422(self):
        resp = client.get(f"{PREFIX}/workspaces/0/topics/merge-history?source_field=wips_independent_claims")
        self.assertEqual(resp.status_code, 422)

    def test_post_merge_workspace_id_zero_or_negative_422(self):
        resp = client.post(
            f"{PREFIX}/workspaces/0/topics/merge",
            json={
                "source_field": "wips_independent_claims",
                "topic_keys": ["topic-1", "topic-2"],
                "label": None,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_post_unmerge_merge_run_id_zero_or_negative_422(self):
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/unmerge",
            json={
                "source_field": "wips_independent_claims",
                "merge_run_id": 0,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 422)
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/unmerge",
            json={
                "source_field": "wips_independent_claims",
                "merge_run_id": -1,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_patch_rename_empty_topic_key_422(self):
        # Empty topic_key in path should 422
        resp = client.patch(
            f"{PREFIX}/workspaces/1/topics/",
            json={"label": "新名稱", "renamed_by": "web-user"},
        )
        # FastAPI returns 404 for missing path param, but we can test with whitespace
        resp = client.patch(
            f"{PREFIX}/workspaces/1/topics/   ",
            json={"label": "新名稱", "renamed_by": "web-user"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_patch_rename_empty_requested_by_422(self):
        resp = client.patch(
            f"{PREFIX}/workspaces/1/topics/topic-1",
            json={"label": "新名稱", "renamed_by": "   "},
        )
        self.assertEqual(resp.status_code, 422)

    def test_patch_rename_empty_renamed_by_422(self):
        # renamed_by is required field, empty string should 422
        resp = client.patch(
            f"{PREFIX}/workspaces/1/topics/topic-1",
            json={"label": "新名稱", "renamed_by": ""},
        )
        self.assertEqual(resp.status_code, 422)

    def test_post_merge_label_only_whitespace_422(self):
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/merge",
            json={
                "source_field": "wips_independent_claims",
                "topic_keys": ["topic-1", "topic-2"],
                "label": "   ",
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_post_merge_topic_keys_contain_empty_422(self):
        resp = client.post(
            f"{PREFIX}/workspaces/1/topics/merge",
            json={
                "source_field": "wips_independent_claims",
                "topic_keys": ["topic-1", ""],
                "label": None,
                "requested_by": "web-user",
                "request_key": None,
            },
        )
        self.assertEqual(resp.status_code, 422)

    # ── 9. RenameRequest model_post_init type hints ─────────────
    def test_rename_request_model_post_init_type_hints_resolvable(self):
        """typing.get_type_hints(RenameRequest.model_post_init) 不應 NameError。"""
        from backend.app.api.topics import RenameRequest
        import typing
        # 這行不應拋出 NameError
        hints = typing.get_type_hints(RenameRequest.model_post_init)
        # context 參數應解析為 Any
        self.assertIn("context", hints)


class TopicRepositoryProtocolTests(unittest.TestCase):
    """Verify the Protocol shape exists and is importable."""

    def test_protocol_importable(self):
        from backend.app.repositories.topic_repository import TopicRepository

        self.assertTrue(hasattr(TopicRepository, "list_topics"))
        self.assertTrue(hasattr(TopicRepository, "list_merge_suggestions"))
        self.assertTrue(hasattr(TopicRepository, "queue_merge"))
        self.assertTrue(hasattr(TopicRepository, "list_merge_history"))
        self.assertTrue(hasattr(TopicRepository, "queue_unmerge"))
        self.assertTrue(hasattr(TopicRepository, "rename_topic"))


if __name__ == "__main__":
    unittest.main()
