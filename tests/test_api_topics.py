"""斷點 A regression：topics 端點 DI 在正式庫應真的注入 PostgresTopicRepository。

症狀：main.py 未接線，get_topic_repository() 無條件 raise TopicRepositoryUnavailableError，
六支 topics 端點在正式環境全回 503（即使 PostgresTopicRepository 與 0021 SQL 皆就緒）。

本檔用拋棄式 DB patent_ppt_topics_di（絕不碰正式庫 patent_ppt）＋0021 fixture，
不使用 dependency_overrides，直接走預設 DI，斷言 GET topics / merge-history 回 200 且結構正確、
非 503——證明預設 DI 已注入實作。沿用 test_postgres_topic_repository.py 的拋棄式 DB 模式。
"""
from __future__ import annotations

import os
import unittest
import warnings

import psycopg
from psycopg.types.json import Jsonb
from alembic import command
from alembic.config import Config

from pathlib import Path

from starlette.exceptions import StarletteDeprecationWarning

# 精準過濾 FastAPI TestClient 匯入時的單一 Starlette deprecation warning
warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=StarletteDeprecationWarning,
    module=r"fastapi\.testclient",
)

TEST_DB = "patent_ppt_topics_di"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
WIPS = "wips_independent_claims"

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組出連測試庫的 psycopg 參數（與 test_postgres_topic_repository 同源）。"""
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    password = os.getenv("PGPASSWORD")
    if password:
        kw["password"] = password
    return kw


def setUpModule():
    """建拋棄式 DB → upgrade head → 種 0021 fixture；admin 不可用則整組 skip。"""
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"  # Windows localhost 走 IPv6 會慢
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB  # DI 走 get_connection_kwargs() 需連測試庫

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    _seed()


def tearDownModule():
    for k, v in _prev_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    except Exception:  # noqa: BLE001
        pass


def _seed():
    """種一個 workspace＋一個 wips topic_run（含合併歷史用的 topic_merge run）。

    T01 指派兩件（930001、930002），T02 指派一件（930003），供 topic patents
    端點斷言「用指派關係篩、非 label 文字比對」；patents 帶明細（number/country/申請人）
    以驗證投影欄位。"""
    state = {"topics": [
        {"topic_id": 1, "topic_code": "T01", "label": "鋸切結構", "status": "active",
         "topic_kind": "model", "label_source": "model", "doc_count": 2},
        {"topic_id": 2, "topic_code": "T02", "label": "進給機構", "status": "active",
         "topic_kind": "model", "label_source": "model", "doc_count": 1},
    ]}
    tech_col = "獨立項[KR,JP,US,CN,EP,IN]"
    with psycopg.connect(**_kw(TEST_DB)) as c:
        # 三筆專利明細：title 刻意不含 topic label 文字，證明篩選靠指派非 label ILIKE。
        for pid, number, title in (
            (930001, "TW-AAA-1", "band saw base plate"),
            (930002, "TW-BBB-2", "cutting fixture assembly"),
            (930003, "TW-CCC-3", "feed drive module"),
        ):
            c.execute(
                f'INSERT INTO core_layer.patents (id, title, country_code, "授權公告號", "{tech_col}") '
                "VALUES (%s, %s, 'TW', %s, 'claim text')",
                (pid, title, number),
            )
        c.execute("INSERT INTO legacy_0021.report_patent_base (patent_id, applicant_display_name) "
                  "VALUES (930001, 'REXON INDUSTRIAL')")
        c.execute("INSERT INTO app_layer.workspaces (workspace_id, workspace_name, patent_ids_json) "
                  "VALUES (930001, 'di_ws', %s)", (Jsonb([930001, 930002, 930003]),))
        c.execute("INSERT INTO app_layer.workflow_runs (run_id, workspace_id, run_type, status) "
                  "VALUES (931001, 930001, 'clustering:wips_independent_claims', 'succeeded')")
        c.execute("INSERT INTO derived_layer.topic_runs "
                  "(run_id, workflow_run_id, source_field, topic_state_json) "
                  "VALUES (932001, 931001, %s, %s)", (WIPS, Jsonb(state)))
        for pid, key in ((930001, "T01"), (930002, "T01"), (930003, "T02")):
            c.execute("INSERT INTO derived_layer.topic_assignments (run_id, patent_id, topic_key) "
                      "VALUES (932001, %s, %s)", (pid, key))
        # 一筆已排程的 topic_merge run，供 merge-history 端點回傳非空
        c.execute("INSERT INTO app_layer.workflow_runs "
                  "(workspace_id, run_type, status, request_json) "
                  "VALUES (930001, 'topic_merge', 'queued', %s)",
                  (Jsonb({"source_field": WIPS, "topic_keys": ["T01", "T02"],
                          "requested_by": "web-user"}),))
        c.commit()


def _client():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    # 關鍵：不設 dependency_overrides，走 main.py 的預設 DI
    return TestClient(app)


class TopicsDefaultDiTests(unittest.TestCase):
    """預設 DI（無 override）下，topics 端點對拋棄式 0021 DB 應回 200 而非 503。"""

    def test_list_topics_returns_200_via_default_di(self):
        r = _client().get(f"/api/v1/workspaces/930001/topics?source_field={WIPS}")
        self.assertEqual(r.status_code, 200, r.text)  # 修前為 503
        body = r.json()
        self.assertEqual(body["workspace_id"], 930001)
        self.assertEqual(body["source_field"], WIPS)
        self.assertEqual({t["topic_key"] for t in body["topics"]}, {"T01", "T02"})

    def test_merge_history_returns_200_via_default_di(self):
        r = _client().get(
            f"/api/v1/workspaces/930001/topics/merge-history?source_field={WIPS}")
        self.assertEqual(r.status_code, 200, r.text)  # 修前為 503
        body = r.json()
        self.assertGreaterEqual(len(body), 1)
        self.assertEqual(body[0]["source_topics"], ["T01", "T02"])


class TopicPatentsEndpointTests(unittest.TestCase):
    """GET /workspaces/{id}/topics/{topic_key}/patents：以指派關係回該 topic 專利。"""

    def _get(self, topic_key, **params):
        params.setdefault("source_field", WIPS)
        return _client().get(
            f"/api/v1/workspaces/930001/topics/{topic_key}/patents", params=params)

    def test_topic_patents_returns_assigned_patents(self):
        """T01 指派兩件（930001/930002），回正確專利與件數；投影欄位齊全。"""
        r = self._get("T01", limit=200)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["total"], 2)
        ids = sorted(it["patent_id"] for it in body["items"])
        self.assertEqual(ids, [930001, 930002])
        first = body["items"][0]
        self.assertEqual(
            set(first.keys()),
            {"patent_id", "patent_number", "title", "country_code", "applicant_display_name"},
        )
        by_id = {it["patent_id"]: it for it in body["items"]}
        self.assertEqual(by_id[930001]["patent_number"], "TW-AAA-1")
        self.assertEqual(by_id[930001]["applicant_display_name"], "REXON INDUSTRIAL")

    def test_topic_patents_second_topic(self):
        """T02 指派一件（930003）；證明篩選靠指派非 label 文字（title 不含 label）。"""
        body = self._get("T02", limit=200).json()
        self.assertEqual(body["total"], 1)
        self.assertEqual([it["patent_id"] for it in body["items"]], [930003])

    def test_topic_patents_pagination(self):
        """分頁切片與全量一致，total 不受分頁影響。"""
        full = self._get("T01", limit=200).json()
        full_ids = [it["patent_id"] for it in full["items"]]
        p0 = self._get("T01", limit=1, offset=0).json()
        p1 = self._get("T01", limit=1, offset=1).json()
        self.assertEqual(p0["total"], 2)
        self.assertEqual([it["patent_id"] for it in p0["items"]], full_ids[0:1])
        self.assertEqual([it["patent_id"] for it in p1["items"]], full_ids[1:2])

    def test_unknown_topic_key_returns_404(self):
        """不存在／非 active 的 topic_key → 404，不回錯誤專利。"""
        self.assertEqual(self._get("T99", limit=200).status_code, 404)

    def test_workspace_not_found_returns_404(self):
        """不存在的 workspace → 404。"""
        r = _client().get(
            f"/api/v1/workspaces/999999999/topics/T01/patents?source_field={WIPS}")
        self.assertEqual(r.status_code, 404)


class TopicAiLabelEndpointTests(unittest.TestCase):
    """POST /workspaces/{id}/topics/ai-label：為正式 topic version 建立 AI 標籤任務。

    端點只負責「建任務」，不在請求執行緒內跑 CLI；payload 組裝與 CLI 呼叫都在
    AI bridge 那側（ai_topic_label_runner），因此這裡只斷言 job 建立契約。
    """

    def _post(self, body=None, workspace_id=930001):
        from unittest import mock

        from backend.app.api import topics as topics_api

        created = {}

        class _Job:
            job_id = 555
            job_type = "ai:topic_label"
            status = "queued"

        with mock.patch.object(topics_api.job_repository, "create_job") as create_job:
            create_job.return_value = _Job()
            r = _client().post(
                f"/api/v1/workspaces/{workspace_id}/topics/ai-label",
                json=body if body is not None else {"source_field": WIPS},
            )
            created["call"] = create_job.call_args
        return r, created["call"]

    def test_creates_ai_topic_label_job(self):
        """建立 ai:topic_label job，回 202 與 run_id 供前端輪詢。"""
        r, call = self._post()
        self.assertEqual(r.status_code, 202, r.text)
        body = r.json()
        self.assertEqual(body["run_id"], 555)
        self.assertEqual(body["job_type"], "ai:topic_label")
        self.assertEqual(call.args[0], "ai:topic_label")
        self.assertEqual(call.kwargs["workspace_id"], 930001)

    def test_job_payload_carries_only_identifiers(self):
        """🔴 紅線：建任務的 payload 只帶識別資訊，不含 keywords（文檔在 bridge 端才取）。"""
        _, call = self._post({"source_field": WIPS, "cli_kind": "claude",
                              "model": "claude-opus-4-8"})
        payload = call.kwargs["payload"]
        self.assertEqual(payload["source_field"], WIPS)
        self.assertEqual(payload["workspace_id"], 930001)
        self.assertEqual(payload["cli_kind"], "claude")
        self.assertEqual(payload["model"], "claude-opus-4-8")
        blob = str(payload).lower()
        self.assertNotIn("keyword", blob)

    def test_rejects_unknown_source_field(self):
        """非白名單通道 → 422，不建任務。"""
        r, _ = self._post({"source_field": "technical"})
        self.assertEqual(r.status_code, 422)

    def test_rejects_unknown_workspace(self):
        """不存在的 workspace → 404，不建空轉任務。"""
        r, _ = self._post({"source_field": WIPS}, workspace_id=999999999)
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
