"""排除復原契約測試（2026-07-27 使用者要求：預防使用者後悔）。

補的斷鏈：0035 的設計說明寫「標記須留存、可追溯、**可反悔**」，但確定排除時會
`DELETE topic_assignments`，而排除表**不記原主題**——反悔後根本回不到原本的主題。

## 方案 A：排除前記下 topic_key（0037）
刪 assignment 前把原主題寫進 `restored_topic_key`，放回時精準還原到原主題。
- 為何不用「重算最近主題」：主題被合併或停用後會算到別處，且需載 embeddings，成本高。
- 為何不用「只從清單移除、不指派」：系統桶已於 2026-07-27 移除，沒有主題的專利會
  從主題視圖消失，等於換一種方式不見。

⚠ 0035 當時把 topic_key 列為「待評估／可由 topic_assignments 推導不重複存」——
該推導前提在「確定排除會刪掉 assignment」的行為下不成立。需求出現，故補此欄。

鎖住的紅線：
- 確定排除時記下原主題（每通道各一筆；一筆專利可能同時在技術與功效通道有指派）。
- 放回：刪排除列 ＋ 還原**所有通道**的 assignment（含原 distance_to_centroid，不重算）。
- 放回已不存在／已停用的主題時不憑空造 assignment，回報該通道無法還原，其餘照還。
- 待複核（pending）不需復原——它從未移除 assignment。
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


TEST_DB = "patent_ppt_exclrestore"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


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


def _alembic_cfg() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


class ExclusionRestoreTests(unittest.TestCase):
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
        command.upgrade(_alembic_cfg(), "head")

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
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            c.execute("DELETE FROM derived_layer.workspace_excluded_patents")
            c.execute("DELETE FROM derived_layer.topic_assignments")
            c.execute("DELETE FROM derived_layer.topic_runs")
            c.execute("DELETE FROM app_layer.workflow_runs")
            c.execute("DELETE FROM app_layer.workspaces")
            c.execute("DELETE FROM core_layer.patents")

    def _setup_workspace(self, conn, patent_ids: list[int]) -> int:
        """建專利 ＋ workspace ＋ 兩通道各一個 topic run，回 workspace_id。

        topic_assignments 對 core_layer.patents 有 FK，故先把測試用的 patent_id
        以明給 id 的方式插入（patents.id 有序列但可明指）。
        """
        for pid in patent_ids:
            conn.execute(
                "INSERT INTO core_layer.patents (id) VALUES (%s) ON CONFLICT DO NOTHING",
                (pid,),
            )
        ws = conn.execute(
            "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json) "
            "VALUES (%s, %s) RETURNING workspace_id",
            ("ws-restore", json.dumps(patent_ids)),
        ).fetchone()[0]
        for source_field in ("wips_independent_claims", "effect_summary"):
            wr = conn.execute(
                "INSERT INTO app_layer.workflow_runs (workspace_id, run_type, status) "
                "VALUES (%s, 'clustering_finalize', 'succeeded') RETURNING run_id",
                (ws,),
            ).fetchone()[0]
            # run_id 無 default：專案慣例是 job_id 即 topic run id（見 handlers 傳
            # workflow_run_id=context.job.job_id），測試沿用同一值。
            conn.execute(
                "INSERT INTO derived_layer.topic_runs "
                "(run_id, workflow_run_id, source_field, topic_state_json) "
                "VALUES (%s, %s, %s, %s)",
                (wr, wr, source_field, json.dumps({
                    "topics": [
                        {"topic_code": "T001", "label": "主題一", "status": "active",
                         "topic_kind": "model", "model_topic_ids": [0]},
                        {"topic_code": "T002", "label": "主題二", "status": "active",
                         "topic_kind": "model", "model_topic_ids": [1]},
                    ],
                })),
            )
        return ws

    def _assign(self, conn, ws: int, patent_id: int, topic_key: str, distance: float):
        """把某專利指派到兩個通道的同一 topic_code（測試用簡化）。"""
        for run_id in [
            r[0] for r in conn.execute(
                "SELECT tr.run_id FROM derived_layer.topic_runs tr "
                "JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id "
                "WHERE wr.workspace_id = %s ORDER BY tr.run_id", (ws,)).fetchall()
        ]:
            conn.execute(
                "INSERT INTO derived_layer.topic_assignments "
                "(run_id, patent_id, topic_key, distance_to_centroid) VALUES (%s,%s,%s,%s)",
                (run_id, patent_id, topic_key, distance),
            )

    def _assignment_rows(self, conn, patent_id: int) -> list[tuple]:
        return [
            (r[0], float(r[1]) if r[1] is not None else None)
            for r in conn.execute(
                "SELECT topic_key, distance_to_centroid FROM derived_layer.topic_assignments "
                "WHERE patent_id = %s ORDER BY run_id", (patent_id,)).fetchall()
        ]

    def test_confirm_records_original_topic(self):
        """確定排除時記下原主題（供之後放回還原）。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._setup_workspace(c, [1, 2, 3])
            self._assign(c, ws, 2, "T002", 0.42)
            exclusions.store_ai_verdicts(
                ws, [{"patent_id": 2, "verdict": "不相干", "reason": "AI"}], conn=c)
            c.commit()
            exclusions.confirm_exclusions(ws, [2], conn=c)
            c.commit()
            saved = c.execute(
                "SELECT restored_topic_key FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id=%s AND patent_id=2", (ws,)).fetchone()[0]
        self.assertIsNotNone(saved, "確定排除時未記下原主題，之後無法還原")
        self.assertIn("T002", json.dumps(saved, ensure_ascii=False))

    def test_manual_exclude_records_original_topic(self):
        """人工剔除同樣記下原主題（兩條路徑都要能反悔）。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._setup_workspace(c, [1, 2])
            self._assign(c, ws, 1, "T001", 0.31)
            exclusions.exclude_patents(ws, [(1, "人工剔除")], conn=c)
            c.commit()
            saved = c.execute(
                "SELECT restored_topic_key FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id=%s AND patent_id=1", (ws,)).fetchone()[0]
        self.assertIsNotNone(saved)
        self.assertIn("T001", json.dumps(saved, ensure_ascii=False))

    def test_restore_puts_back_all_channels(self):
        """放回：刪排除列 ＋ 還原所有通道的 assignment（含原距離，不重算）。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._setup_workspace(c, [1, 2, 3])
            self._assign(c, ws, 3, "T001", 0.77)
            before = self._assignment_rows(c, 3)
            exclusions.exclude_patents(ws, [(3, "誤剔除")], conn=c)
            c.commit()
            self.assertEqual(self._assignment_rows(c, 3), [], "排除後 assignment 應已移除")

            restored = exclusions.restore_patents(ws, [3], conn=c)
            c.commit()
            after = self._assignment_rows(c, 3)
            left = c.execute(
                "SELECT count(*) FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id=%s AND patent_id=3", (ws,)).fetchone()[0]

        self.assertEqual(restored, 1)
        self.assertEqual(left, 0, "放回後不得留在排除清單")
        self.assertEqual(after, before, "兩通道的 topic_key 與距離都要原樣還原")

    def test_restore_returns_to_analysis(self):
        """放回後重新計入分析成員。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._setup_workspace(c, [1, 2, 3])
            self._assign(c, ws, 2, "T001", 0.5)
            exclusions.exclude_patents(ws, [(2, "剔除")], conn=c)
            c.commit()
            self.assertEqual(exclusions.analysis_member_patent_ids(ws, conn=c), [1, 3])
            exclusions.restore_patents(ws, [2], conn=c)
            c.commit()
            members = exclusions.analysis_member_patent_ids(ws, conn=c)
        self.assertEqual(members, [1, 2, 3])

    def test_restore_survives_deleted_run(self):
        """原 run 已被刪除時該通道還原不了，但仍要移出排除清單、且不得 raise。

        ⚠ topic_assignments 的 FK 是對 **run_id**（不是 topic_code），所以「還原不了」
        的真實情境是 run 被刪（例如重跑分群清掉舊 run），而非 topic_code 不在 state 內
        ——後者仍插得進去。使用者要它回來，不能因為還原不了就繼續關著。
        """
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            ws = self._setup_workspace(c, [1, 2])
            self._assign(c, ws, 1, "T001", 0.5)
            exclusions.exclude_patents(ws, [(1, "剔除")], conn=c)
            # 模擬事後重跑分群把舊 run 清掉：快照裡的 run_id 已不存在。
            c.execute("DELETE FROM derived_layer.topic_runs")
            restored = exclusions.restore_patents(ws, [1], conn=c)
            rows = self._assignment_rows(c, 1)
            left = c.execute(
                "SELECT count(*) FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id=%s AND patent_id=1", (ws,)).fetchone()[0]
        self.assertEqual(restored, 1, "仍要移出排除清單（使用者要它回來）")
        self.assertEqual(rows, [], "run 不存在時不得憑空造 assignment，也不得 raise")
        self.assertEqual(left, 0)

    def test_restore_ignores_pending(self):
        """待複核（pending）不走復原——它從未移除 assignment，用 keep 即可。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._setup_workspace(c, [1, 2])
            self._assign(c, ws, 1, "T001", 0.5)
            exclusions.store_ai_verdicts(
                ws, [{"patent_id": 1, "verdict": "不相干", "reason": "AI"}], conn=c)
            c.commit()
            restored = exclusions.restore_patents(ws, [1], conn=c)
            c.commit()
            still_pending = c.execute(
                "SELECT status FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id=%s AND patent_id=1", (ws,)).fetchone()
        self.assertEqual(restored, 0, "pending 不是排除，不該被復原處理")
        self.assertEqual(still_pending[0], "pending", "pending 列應原封不動")


if __name__ == "__main__":
    unittest.main()
