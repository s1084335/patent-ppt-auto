"""主題專利清單必須跨 run 取指派（2026-07-27 實機：增量分群後大部分主題點進去是空的）。

實機症狀：Hedge 匯入新資料跑完 incremental 後，功效通道 10 個主題裡
「優化變速性能與壽命 14」點進去看得到專利，「強化握持平衡與剛性 26」卻顯示
「此主題尚無指派的專利」——**有些有、有些沒有**。

根因：incremental run **只寫新增專利的 assignment**（run 7 只有 49 筆），
舊專利的 assignment 留在先前的 full/merge run（run 2/3 各 137 筆）。
而 `/topics/{key}/patents` 傳 `state["run_id"]`（最新 run）給 `assigned_patent_ids`，
該函式只查單一 run → **只拿得到落在最新 run 的那批**。
主題的專利若多數在舊 run，點進去就是空的。

`topic_state_repository` 的 docstring 早就寫明這個規則：
> assignments 取該 ws/field 全部 run 中每個 patent_id 的最新一筆（DISTINCT ON）
但 `assigned_patent_ids` 這條路徑沒跟上——同一規則兩處實作，只有一處對。

⚠ 標籤上的件數（doc_count）來自 topic_state，是跨 run 正確的；
清單卻只取單一 run —— **兩個數字來源不一致**，使用者看到「26 筆」卻列不出東西。
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_assignruns"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

SF = "effect_summary"


def _kw(dbname: str) -> dict:
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    pwd = os.getenv("PGPASSWORD")
    if pwd:
        kw["password"] = pwd
    return kw


class AssignedPatentsAcrossRunsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        command.upgrade(cfg, "head")

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:
            pass

    def setUp(self):
        """建「full run 有舊專利、incremental run 只有新專利」的實機情境。"""
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            for t in ("derived_layer.topic_assignments", "derived_layer.topic_runs",
                      "app_layer.workflow_runs", "app_layer.workspaces",
                      "core_layer.patents"):
                c.execute(f"DELETE FROM {t}")
            # 專利 1-5 舊、6-8 新
            for pid in range(1, 9):
                c.execute("INSERT INTO core_layer.patents (id) VALUES (%s)", (pid,))
            self.ws = c.execute(
                "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json) "
                "VALUES ('ws-runs', %s) RETURNING workspace_id",
                (json.dumps(list(range(1, 9))),),
            ).fetchone()[0]
            topics = [
                {"topic_code": "T001", "label": "主題一", "status": "active",
                 "topic_kind": "model", "doc_count": 5, "model_topic_ids": [0]},
                {"topic_code": "T002", "label": "主題二", "status": "active",
                 "topic_kind": "model", "doc_count": 3, "model_topic_ids": [1]},
            ]
            # full run：專利 1-5（T001 三筆、T002 兩筆）
            self.run_full = self._mk_run(c, topics, "full", prev=None)
            for pid, key in ((1, "T001"), (2, "T001"), (3, "T001"),
                             (4, "T002"), (5, "T002")):
                c.execute(
                    "INSERT INTO derived_layer.topic_assignments "
                    "(run_id, patent_id, topic_key, distance_to_centroid) VALUES (%s,%s,%s,0.1)",
                    (self.run_full, pid, key))
            # incremental run：**只有新專利 6-8**（實機就是這樣）
            self.run_inc = self._mk_run(c, [], "incremental", prev=self.run_full)
            for pid, key in ((6, "T001"), (7, "T002"), (8, "T002")):
                c.execute(
                    "INSERT INTO derived_layer.topic_assignments "
                    "(run_id, patent_id, topic_key, distance_to_centroid) VALUES (%s,%s,%s,0.2)",
                    (self.run_inc, pid, key))

    def _mk_run(self, conn, topics, mode, *, prev):
        wf = conn.execute(
            "INSERT INTO app_layer.workflow_runs (workspace_id, run_type, status) "
            "VALUES (%s,'clustering_finalize','succeeded') RETURNING run_id",
            (self.ws,)).fetchone()[0]
        conn.execute(
            "INSERT INTO derived_layer.topic_runs "
            "(run_id, workflow_run_id, previous_run_id, source_field, topic_state_json, "
            " artifact_key) VALUES (%s,%s,%s,%s,%s,%s)",
            (wf, wf, prev, SF, json.dumps({
                "status": "completed", "run_mode": mode, "artifact_version": 1,
                "model_artifact_hash": "h" * 64, "topics": topics}),
             "clustering/ws/run.pkl"))
        return wf

    def test_assigned_ids_span_all_runs(self):
        """T001 應含 full run 的 1,2,3 與 incremental run 的 6——不是只有 6。"""
        from backend.app.app_layer import workspace_queries

        ids = workspace_queries.assigned_patent_ids(
            workspace_id=self.ws, source_field=SF, topic_key="T001")
        self.assertEqual(
            sorted(ids), [1, 2, 3, 6],
            "只取最新 run → 舊專利消失，主題點進去是空的（實機症狀）")

    def test_second_topic_also_spans_runs(self):
        """T002 同理：full 的 4,5 ＋ incremental 的 7,8。"""
        from backend.app.app_layer import workspace_queries

        ids = workspace_queries.assigned_patent_ids(
            workspace_id=self.ws, source_field=SF, topic_key="T002")
        self.assertEqual(sorted(ids), [4, 5, 7, 8])

    def test_reassigned_patent_uses_latest_run(self):
        """同一專利在新 run 改派到別的主題時，以**最新 run** 為準，不得兩邊都算。"""
        from backend.app.app_layer import workspace_queries

        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            # 專利 1 原本 T001，在 incremental run 改派到 T002
            c.execute(
                "INSERT INTO derived_layer.topic_assignments "
                "(run_id, patent_id, topic_key, distance_to_centroid) VALUES (%s,1,'T002',0.3)",
                (self.run_inc,))

        t1 = workspace_queries.assigned_patent_ids(
            workspace_id=self.ws, source_field=SF, topic_key="T001")
        t2 = workspace_queries.assigned_patent_ids(
            workspace_id=self.ws, source_field=SF, topic_key="T002")
        self.assertNotIn(1, t1, "改派後不應仍留在舊主題")
        self.assertIn(1, t2, "改派後應出現在新主題")

    def test_counts_match_topic_doc_count(self):
        """清單筆數要與標籤上的件數一致——使用者看到「26 筆」就該列得出 26 筆。"""
        from backend.app.app_layer import workspace_queries

        total = sum(
            len(workspace_queries.assigned_patent_ids(
                workspace_id=self.ws, source_field=SF, topic_key=key))
            for key in ("T001", "T002"))
        self.assertEqual(total, 8, "全部 8 筆專利都要被列到某個主題底下")


if __name__ == "__main__":
    unittest.main()
