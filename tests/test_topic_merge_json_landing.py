"""topic merge／unmerge／incremental 在 0021 JSON 落點的行為契約（拋棄式 DB，絕不碰 patent_ppt）。

0021 併表後主題狀態唯一來源是 derived_layer.topic_runs.topic_state_json->'topics'，
指派在 derived_layer.topic_assignments。本檔驗「不重跑 BERTopic 也能完成的主題結構操作」：

- merge：目標主題吸收來源主題的 patent 指派；來源主題保留但 status='merged' 且記
  merged_into_topic_id；產新 run（previous_run_id 指前一版），不就地改舊 run。
- unmerge：依 merge run 找回前一版，同樣產新 run 還原，而非刪掉新版。
- rename：只改 label 與 label_source='manual'，不動指派結構。
- merge-history：由 run 鏈（previous_run_id）＋ topic_state_json 內 merged 記錄組出，不另建表。
- 讀寫同源：上述操作後 PostgresTopicStateRepository.get_latest_topic_state 必須讀得懂。

沿用 test_clustering_0021_persistence.py 的拋棄式 DB 模式（建庫 → alembic upgrade head → 種 fixture）。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_topicmerge"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

WIPS = "wips_independent_claims"

# fixture ID 區段（與其他測試庫互不重疊）
WS_ID = 940001
WF_FINALIZE = 941001
PATENT_IDS = (940101, 940102, 940103, 940104)

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組出連測試庫的 psycopg 參數（與其他 0021 測試同源）。"""
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
    """建拋棄式 DB → upgrade head → 種 workspace/workflow_run/patents；admin 不可用則 skip。"""
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB

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
    """種 workspace、finalize workflow_run 與四筆專利。"""
    with psycopg.connect(**_kw(TEST_DB)) as c:
        for pid in PATENT_IDS:
            c.execute(
                "INSERT INTO core_layer.patents (id, title) VALUES (%s, 'topicmerge fixture')",
                (pid,),
            )
        c.execute(
            "INSERT INTO app_layer.workspaces (workspace_id, workspace_name) VALUES (%s, %s)",
            (WS_ID, "topicmerge_ws"),
        )
        c.execute(
            "INSERT INTO app_layer.workflow_runs (run_id, workspace_id, run_type, status) "
            "VALUES (%s, %s, 'clustering_finalize', 'succeeded')",
            (WF_FINALIZE, WS_ID),
        )
        c.commit()


def _reset():
    """每個測試前清掉分群 run／指派與衍生 workflow_run，只留 seed 的 finalize run。"""
    with psycopg.connect(**_kw(TEST_DB)) as c:
        c.execute("DELETE FROM derived_layer.topic_assignments")
        c.execute("DELETE FROM derived_layer.topic_runs")
        c.execute("DELETE FROM app_layer.workflow_runs WHERE run_id <> %s", (WF_FINALIZE,))
        c.commit()


def _seed_base_run() -> int:
    """種一個已完成的 finalize topic_run：三個 active 主題＋四筆指派，回傳 topic run_id。

    T001: 940101, 940102 / T002: 940103 / UNCLASSIFIED: 940104
    """
    from backend.app.clustering.runner import create_topic_run, persist_final_topics

    run_id = create_topic_run(
        workflow_run_id=WF_FINALIZE,
        source_field=WIPS,
        state={"run_mode": "full", "status": "completed", "input_doc_count": 4,
               "topic_count": 3, "artifact_version": 1},
    )
    topics = [
        {"topic_id": 1, "topic_code": "T001", "topic_kind": "model", "status": "active",
         "label": "鋸切結構", "label_source": "fallback", "display_order": 1, "doc_count": 2,
         "model_topic_ids": [0], "keywords": [{"term": "鋸切", "weight": 1.0}]},
        {"topic_id": 2, "topic_code": "T002", "topic_kind": "model", "status": "active",
         "label": "進給機構", "label_source": "fallback", "display_order": 2, "doc_count": 1,
         "model_topic_ids": [1], "keywords": [{"term": "進給", "weight": 1.0}]},
        {"topic_id": 3, "topic_code": "UNCLASSIFIED", "topic_kind": "unclassified",
         "status": "active", "label": "未分類", "label_source": "fallback",
         "display_order": 3, "doc_count": 1},
    ]
    assignments = [
        (PATENT_IDS[0], "T001", 0.1),
        (PATENT_IDS[1], "T001", 0.2),
        (PATENT_IDS[2], "T002", 0.3),
        (PATENT_IDS[3], "UNCLASSIFIED", None),
    ]
    persist_final_topics(
        run_id=run_id, topics=topics, assignments=assignments,
        metrics={"score": 0.9}, artifact_key="ws/940001/base.pkl",
    )
    return run_id


def _topic_runs() -> list[dict]:
    """取本 workspace/通道的全部 topic_run（含 run 鏈與 state），依 run_id 排序。"""
    with psycopg.connect(**_kw(TEST_DB), row_factory=dict_row) as c:
        return c.execute(
            """
            SELECT tr.run_id, tr.previous_run_id, tr.topic_state_json, wr.run_type, wr.status
            FROM derived_layer.topic_runs tr
            JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
            WHERE wr.workspace_id = %s AND tr.source_field = %s
            ORDER BY tr.run_id
            """,
            (WS_ID, WIPS),
        ).fetchall()


class MergeTopicsTests(unittest.TestCase):
    """merge：吸收指派、來源標 merged、產新 run。"""

    def setUp(self):
        _reset()
        self.base_run_id = _seed_base_run()

    def test_merge_creates_new_run_and_marks_source_merged(self):
        from backend.app.clustering.workspace_service import merge_workspace_topics

        summary = merge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            topic_keys=["T001", "T002"], merged_by="tester",
        )
        runs = _topic_runs()
        # 產新 run，不就地改舊 run
        self.assertEqual(len(runs), 2)
        new_run = runs[-1]
        self.assertEqual(new_run["run_id"], summary.run_id)
        self.assertEqual(new_run["previous_run_id"], self.base_run_id)
        self.assertEqual(new_run["run_type"], "topic_merge")
        self.assertEqual(new_run["status"], "succeeded")
        # 舊 run 的 state 未被改動（版本不覆蓋）
        old_topics = {t["topic_code"]: t for t in runs[0]["topic_state_json"]["topics"]}
        self.assertEqual(old_topics["T002"]["status"], "active")
        # 新 run：來源保留但標 merged 並指向目標
        topics = {t["topic_code"]: t for t in new_run["topic_state_json"]["topics"]}
        self.assertEqual(topics["T001"]["status"], "active")
        self.assertEqual(topics["T002"]["status"], "merged")
        self.assertEqual(topics["T002"]["merged_into_topic_id"], topics["T001"]["topic_id"])

    def test_merge_transfers_assignments_to_target(self):
        from backend.app.clustering.workspace_service import merge_workspace_topics

        summary = merge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            topic_keys=["T001", "T002"], merged_by="tester",
        )
        with psycopg.connect(**_kw(TEST_DB), row_factory=dict_row) as c:
            rows = c.execute(
                "SELECT patent_id, topic_key FROM derived_layer.topic_assignments "
                "WHERE run_id = %s ORDER BY patent_id",
                (summary.run_id,),
            ).fetchall()
        # 新 run 帶完整快照（四筆），T002 的專利已轉到 T001
        self.assertEqual(
            [(r["patent_id"], r["topic_key"]) for r in rows],
            [(PATENT_IDS[0], "T001"), (PATENT_IDS[1], "T001"),
             (PATENT_IDS[2], "T001"), (PATENT_IDS[3], "UNCLASSIFIED")],
        )

    def test_merge_label_defaults_to_target_and_can_be_overridden(self):
        from backend.app.clustering.workspace_service import merge_workspace_topics

        summary = merge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            topic_keys=["T001", "T002"], merged_by="tester",
        )
        runs = _topic_runs()
        topics = {t["topic_code"]: t for t in runs[-1]["topic_state_json"]["topics"]}
        # 不帶 label → 沿用目標主題現有名稱與來源
        self.assertEqual(topics["T001"]["label"], "鋸切結構")
        self.assertEqual(topics["T001"]["label_source"], "fallback")
        self.assertIsNotNone(summary.run_id)

        summary2 = merge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            topic_keys=["T001", "UNCLASSIFIED"], merged_by="tester", label="鋸切與其他",
        )
        runs = _topic_runs()
        topics = {t["topic_code"]: t for t in runs[-1]["topic_state_json"]["topics"]}
        # 帶 label → 人工命名，label_source='manual' 不被後續 AI 覆蓋
        self.assertEqual(topics["T001"]["label"], "鋸切與其他")
        self.assertEqual(topics["T001"]["label_source"], "manual")
        self.assertEqual(summary2.run_id, _topic_runs()[-1]["run_id"])

    def test_merge_rejects_non_active_or_unknown_topics(self):
        from backend.app.clustering.workspace_service import merge_workspace_topics

        with self.assertRaises(ValueError):
            merge_workspace_topics(
                workspace_id=WS_ID, source_field=WIPS,
                topic_keys=["T001", "NOPE"], merged_by="tester")
        with self.assertRaises(ValueError):
            merge_workspace_topics(
                workspace_id=WS_ID, source_field=WIPS,
                topic_keys=["T001", "T001"], merged_by="tester")

    def test_merge_read_write_same_source(self):
        """讀寫同源：合併後 PostgresTopicStateRepository 必須讀得懂合併鏈。"""
        from backend.app.clustering.workspace_service import merge_workspace_topics
        from backend.app.repositories.topic_state_repository import (
            PostgresTopicStateRepository,
        )

        summary = merge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            topic_keys=["T001", "T002"], merged_by="tester",
        )
        state = PostgresTopicStateRepository().get_latest_topic_state(WS_ID, WIPS)
        self.assertEqual(state["run_id"], summary.run_id)
        self.assertEqual(state["state_run_id"], summary.run_id)
        by_code = {t["topic_code"]: t for t in state["topics"]}
        # ② active 主題數正確：T002 已 merged，不再出現
        self.assertEqual(set(by_code), {"T001", "UNCLASSIFIED"})
        # ①③ 被合併主題的 patent 出現在目標主題，且完整不遺漏
        self.assertEqual(by_code["T001"]["patent_ids"],
                         [PATENT_IDS[0], PATENT_IDS[1], PATENT_IDS[2]])
        self.assertEqual(by_code["UNCLASSIFIED"]["patent_ids"], [PATENT_IDS[3]])
        total = sum(len(t["patent_ids"]) for t in state["topics"])
        self.assertEqual(total, len(PATENT_IDS))


class UnmergeTopicsTests(unittest.TestCase):
    """unmerge：找回前一版並產新 run 還原，不刪新版。"""

    def setUp(self):
        _reset()
        self.base_run_id = _seed_base_run()

    def test_unmerge_restores_source_topic_in_new_run(self):
        from backend.app.clustering.workspace_service import (
            merge_workspace_topics,
            unmerge_workspace_topics,
        )
        from backend.app.repositories.topic_state_repository import (
            PostgresTopicStateRepository,
        )

        merge = merge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            topic_keys=["T001", "T002"], merged_by="tester",
        )
        summary = unmerge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            merge_run_id=merge.run_id, reverted_by="tester",
        )
        runs = _topic_runs()
        # 三個 run：base → merge → unmerge，merge run 保留不刪
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[-1]["run_id"], summary.run_id)
        self.assertEqual(runs[-1]["previous_run_id"], merge.run_id)
        self.assertEqual(runs[-1]["run_type"], "topic_unmerge")
        # 還原後 T002 重新 active、指派回到 T002
        state = PostgresTopicStateRepository().get_latest_topic_state(WS_ID, WIPS)
        by_code = {t["topic_code"]: t for t in state["topics"]}
        self.assertEqual(set(by_code), {"T001", "T002", "UNCLASSIFIED"})
        self.assertEqual(by_code["T001"]["patent_ids"], [PATENT_IDS[0], PATENT_IDS[1]])
        self.assertEqual(by_code["T002"]["patent_ids"], [PATENT_IDS[2]])
        self.assertEqual(by_code["UNCLASSIFIED"]["patent_ids"], [PATENT_IDS[3]])

    def test_unmerge_rejects_unknown_or_already_reverted_merge(self):
        from backend.app.clustering.workspace_service import (
            merge_workspace_topics,
            unmerge_workspace_topics,
        )

        with self.assertRaises(ValueError):
            unmerge_workspace_topics(
                workspace_id=WS_ID, source_field=WIPS,
                merge_run_id=999999, reverted_by="tester")
        merge = merge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            topic_keys=["T001", "T002"], merged_by="tester")
        unmerge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            merge_run_id=merge.run_id, reverted_by="tester")
        # 同一 merge 不可重複還原
        with self.assertRaises(ValueError):
            unmerge_workspace_topics(
                workspace_id=WS_ID, source_field=WIPS,
                merge_run_id=merge.run_id, reverted_by="tester")


class MergeHistoryTests(unittest.TestCase):
    """merge-history：由 run 鏈＋ state 內 merged 記錄組出，不另建表。"""

    def setUp(self):
        _reset()
        self.base_run_id = _seed_base_run()

    def test_history_lists_merge_and_blocks_after_unmerge(self):
        from backend.app.clustering.workspace_service import (
            merge_history,
            merge_workspace_topics,
            unmerge_workspace_topics,
        )

        merge = merge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            topic_keys=["T001", "T002"], merged_by="tester",
        )
        history = merge_history(workspace_id=WS_ID, source_field=WIPS)
        self.assertEqual(len(history), 1)
        item = history[0]
        self.assertEqual(item["merge_run_id"], merge.run_id)
        self.assertEqual(item["source_topics"], ["T002"])
        self.assertEqual(item["result_topic"], "T001")
        self.assertTrue(item["can_unmerge"])
        self.assertIsNone(item["blocked_reason"])

        unmerge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            merge_run_id=merge.run_id, reverted_by="tester")
        history = merge_history(workspace_id=WS_ID, source_field=WIPS)
        self.assertEqual(len(history), 1)
        self.assertFalse(history[0]["can_unmerge"])
        self.assertIsNotNone(history[0]["blocked_reason"])

    def test_only_latest_merge_can_unmerge(self):
        """兩次合併疊加時，只有最新一筆可直接還原（還原順序由 run 鏈決定）。"""
        from backend.app.clustering.workspace_service import (
            merge_history,
            merge_workspace_topics,
        )

        first = merge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            topic_keys=["T001", "T002"], merged_by="tester")
        second = merge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            topic_keys=["T001", "UNCLASSIFIED"], merged_by="tester")
        history = {h["merge_run_id"]: h for h in
                   merge_history(workspace_id=WS_ID, source_field=WIPS)}
        self.assertTrue(history[second.run_id]["can_unmerge"])
        self.assertFalse(history[first.run_id]["can_unmerge"])


class RenameTopicTests(unittest.TestCase):
    """rename：只改 label／label_source，不動指派結構。"""

    def setUp(self):
        _reset()
        self.base_run_id = _seed_base_run()

    def test_rename_sets_manual_label_source_without_new_run(self):
        from backend.app.repositories.postgres_topic_repository import PostgresTopicRepository
        from backend.app.repositories.topic_state_repository import (
            PostgresTopicStateRepository,
        )

        result = PostgresTopicRepository().rename_topic(
            workspace_id=WS_ID, topic_key="T001", label="鋸切總成", renamed_by="tester")
        self.assertEqual(result["label"], "鋸切總成")
        self.assertEqual(result["label_source"], "manual")
        # rename 不改變指派結構 → 不產新 run（避免版本膨脹）
        self.assertEqual(len(_topic_runs()), 1)
        state = PostgresTopicStateRepository().get_latest_topic_state(WS_ID, WIPS)
        by_code = {t["topic_code"]: t for t in state["topics"]}
        self.assertEqual(by_code["T001"]["label"], "鋸切總成")
        self.assertEqual(by_code["T001"]["patent_ids"], [PATENT_IDS[0], PATENT_IDS[1]])

    def test_rename_after_merge_targets_latest_run(self):
        """合併後改名要落在最新 run，不能改到被 merge 取代的舊版。"""
        from backend.app.clustering.workspace_service import merge_workspace_topics
        from backend.app.repositories.postgres_topic_repository import PostgresTopicRepository
        from backend.app.repositories.topic_state_repository import (
            PostgresTopicStateRepository,
        )

        merge = merge_workspace_topics(
            workspace_id=WS_ID, source_field=WIPS,
            topic_keys=["T001", "T002"], merged_by="tester")
        PostgresTopicRepository().rename_topic(
            workspace_id=WS_ID, topic_key="T001", label="合併後名稱", renamed_by="tester")
        runs = {r["run_id"]: r for r in _topic_runs()}
        merged_topics = {t["topic_code"]: t
                         for t in runs[merge.run_id]["topic_state_json"]["topics"]}
        self.assertEqual(merged_topics["T001"]["label"], "合併後名稱")
        self.assertEqual(merged_topics["T001"]["label_source"], "manual")
        state = PostgresTopicStateRepository().get_latest_topic_state(WS_ID, WIPS)
        by_code = {t["topic_code"]: t for t in state["topics"]}
        self.assertEqual(by_code["T001"]["label"], "合併後名稱")


class MergeSuggestionsTests(unittest.TestCase):
    """merge-suggestions：無主題相似度來源時回空清單，不捏造建議。"""

    def setUp(self):
        _reset()
        self.base_run_id = _seed_base_run()

    def test_suggestions_do_not_require_model_artifact(self):
        from backend.app.repositories.postgres_topic_repository import PostgresTopicRepository

        result = PostgresTopicRepository().list_merge_suggestions(WS_ID, WIPS)
        self.assertEqual(result["workspace_id"], WS_ID)
        self.assertEqual(result["suggestions"], [])


if __name__ == "__main__":
    unittest.main()
