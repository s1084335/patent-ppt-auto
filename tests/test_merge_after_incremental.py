"""增量分群後仍能合併主題（2026-07-27 實機：merge target topic not found）。

實機症狀：`分群 → 合併 → 增量 → 再合併` 時第二次合併失敗
`ValueError: merge target topic not found in previous run: T005`，
且該 merge run 的 assignments 為 0（主題欄跟著全空）。

根因：**incremental run 的 `topic_state_json->topics` 是空的**（設計如此——
topics 掛在 finalize/merge run，incremental 只寫新增專利的 assignment）。
而 `_persist_topic_merge` 直接讀 `previous_run_id` 那一筆的 topics：

    run 3 (merge)       topics=10
    run 7 (incremental) topics=0   prev=3
    run 8 (merge)       topics=0   prev=7  ← 從 run 7 拿 topics → 空的 → 找不到目標主題

`topic_state_repository` 早已寫明正確規則：
> topics 取「最新且 topics 非空」的 run（沿 run_id 由大到小 fallback）
但 merge/unmerge 沒跟上——同一規則兩處實作、只有一處對（本專案反覆出現的型態）。

assignments 同理：incremental 只帶增量，合併時要取「全部 run 中每個 patent_id 的
最新一筆」，只讀單一 run 會漏掉舊專利。
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


TEST_DB = "patent_ppt_mergeafterinc"
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


class MergeAfterIncrementalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        os.environ["PGDATABASE"] = TEST_DB
        # ⚠ 必須設 DATABASE_URL（不是 pop）：get_connection_kwargs／get_pool 以它為準，
        # 只設 PG* 會讓連線池仍指向 .env 的 Supabase——實測撈到正式庫 200 筆專利。
        pwd = os.getenv("PGPASSWORD", "")
        auth = f"postgres:{pwd}@" if pwd else "postgres@"
        port = os.getenv("PGPORT", "5433")
        os.environ["DATABASE_URL"] = f"postgresql://{auth}127.0.0.1:{port}/{TEST_DB}"
        # 連線池在首次呼叫時以當下的 DATABASE_URL 建立並快取（connection.py 的 _pool）。
        # 先前測試若已建過池，它仍指向舊目標——強制清掉，讓下次 get_pool() 重建。
        from backend.app.db import connection as _conn_mod
        if getattr(_conn_mod, "_pool", None) is not None:
            try:
                _conn_mod._pool.close()
            except Exception:
                pass
        _conn_mod._pool = None
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
        """重建實機的 run 鏈：full(topics) → incremental(topics=0) 。"""
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            # TRUNCATE ... CASCADE ＋ RESTART IDENTITY：逐表 DELETE 會因 FK cascade
            # 與序列不重置，讓下一輪的 run_id 撞到殘留列（實測 (run_id,patent_id)=(3,1)）。
            c.execute(
                "TRUNCATE derived_layer.topic_assignments, derived_layer.topic_runs, "
                "app_layer.workflow_runs, app_layer.workspaces, core_layer.patents "
                "RESTART IDENTITY CASCADE")
            for pid in range(1, 9):
                c.execute("INSERT INTO core_layer.patents (id) VALUES (%s)", (pid,))
            self.ws = c.execute(
                "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json) "
                "VALUES ('ws-mai', %s) RETURNING workspace_id",
                (json.dumps(list(range(1, 9))),)).fetchone()[0]
            topics = [
                {"topic_id": 1, "topic_code": "T001", "label": "一", "status": "active",
                 "topic_kind": "model", "doc_count": 3, "model_topic_ids": [0]},
                {"topic_id": 2, "topic_code": "T002", "label": "二", "status": "active",
                 "topic_kind": "model", "doc_count": 2, "model_topic_ids": [1]},
            ]
            self.run_full = self._mk(c, topics, "full", prev=None, ver=1)
            for pid, key in ((1, "T001"), (2, "T001"), (3, "T001"), (4, "T002"), (5, "T002")):
                self._assign(c, self.run_full, pid, key)
            # incremental：**topics 為空**（實機就是這樣），只帶新專利
            self.run_inc = self._mk(c, [], "incremental", prev=self.run_full, ver=2)
            for pid, key in ((6, "T001"), (7, "T002"), (8, "T002")):
                self._assign(c, self.run_inc, pid, key)

    def _mk(self, conn, topics, mode, *, prev, ver):
        wf = conn.execute(
            "INSERT INTO app_layer.workflow_runs (workspace_id, run_type, status) "
            "VALUES (%s,'clustering_finalize','succeeded') RETURNING run_id",
            (self.ws,)).fetchone()[0]
        conn.execute(
            "INSERT INTO derived_layer.topic_runs (run_id, workflow_run_id, previous_run_id, "
            "source_field, topic_state_json, artifact_key) VALUES (%s,%s,%s,%s,%s,%s)",
            (wf, wf, prev, SF, json.dumps({
                "status": "completed", "run_mode": mode, "artifact_version": ver,
                "model_artifact_hash": "h" * 64, "topics": topics}),
             "clustering/ws/run.pkl"))
        return wf

    def _assign(self, conn, run_id, pid, key):
        conn.execute(
            "INSERT INTO derived_layer.topic_assignments "
            "(run_id, patent_id, topic_key, distance_to_centroid) VALUES (%s,%s,%s,0.1)",
            (run_id, pid, key))

    def test_merge_finds_topics_through_incremental_run(self):
        """previous_run 是 incremental（topics 空）時，仍要沿鏈找到帶 topics 的 run。"""
        from backend.app.clustering import workspace_service as ws

        # 建 merge run，previous_run_id 指向 topics 為空的 incremental run
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            merge_run = self._mk(c, [], "merge", prev=self.run_inc, ver=3)
            # merge 會把跨 run 的最新指派整份寫進新 run；確保該 run 沒有殘留列
            c.execute("DELETE FROM derived_layer.topic_assignments WHERE run_id=%s",
                      (merge_run,))

        # 不得 raise「merge target topic not found」
        ws._persist_topic_merge(
            run_id=merge_run, workspace_id=self.ws, source_field=SF,
            previous_run_id=self.run_inc, target_code="T001",
            source_codes=["T002"], merged_by="test", label="合併後",
            artifact_version=3)

        with psycopg.connect(**_kw(TEST_DB)) as c:
            state = c.execute(
                "SELECT topic_state_json FROM derived_layer.topic_runs WHERE run_id=%s",
                (merge_run,)).fetchone()[0]
            codes = {t["topic_code"]: t.get("status") for t in state.get("topics", [])}
        self.assertEqual(codes.get("T001"), "active", "目標主題應保留 active")
        self.assertEqual(codes.get("T002"), "merged", "來源主題應標 merged")

    def test_merge_carries_all_assignments_across_runs(self):
        """合併要帶**全部** run 的指派（含 incremental 才有的新專利），不是只有上一個 run。"""
        from backend.app.clustering import workspace_service as ws

        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            merge_run = self._mk(c, [], "merge", prev=self.run_inc, ver=3)
            c.execute("DELETE FROM derived_layer.topic_assignments WHERE run_id=%s",
                      (merge_run,))
        ws._persist_topic_merge(
            run_id=merge_run, workspace_id=self.ws, source_field=SF,
            previous_run_id=self.run_inc, target_code="T001",
            source_codes=["T002"], merged_by="test", label=None,
            artifact_version=3)

        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                "SELECT patent_id, topic_key FROM derived_layer.topic_assignments "
                "WHERE run_id=%s ORDER BY patent_id", (merge_run,)).fetchall()
        self.assertEqual(
            len(rows), 8,
            "合併後應涵蓋全部 8 筆專利（full 的 5 筆 ＋ incremental 的 3 筆）；"
            "只讀單一 run 會漏掉舊專利，主題欄跟著全空")
        # T002 的專利全部改指向 T001
        self.assertTrue(all(k == "T001" for _p, k in rows), "來源主題的專利應改派到目標")


if __name__ == "__main__":
    unittest.main()
