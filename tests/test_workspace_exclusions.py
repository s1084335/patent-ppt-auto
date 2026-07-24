"""不相干專利排除清單 store ＋ 取成員收口函式契約測試（獨立測試 DB）。

規格唯一來源：irrelevant-patent-filter-spec.md 第 58-76、128-140 行。

鎖住的紅線：
- **取成員單一函式收口**：analysis_member_patent_ids 扣除排除清單；
  display_member_patent_ids 不扣（使用者仍要看得到被排除者與標記）。
- **全庫不做此限制**：全庫 workspace 的 analysis_member_patent_ids 不扣除排除清單。
- **排除是 workspace 級**：同一 patent_id 在 A 被排除、在 B 照常。
- **剔除不重跑分群**：exclude_patents 只寫排除表、移除該筆 topic_assignments、
  移出 workspace patent_ids_json；model artifact／distance_to_centroid 不動
  （本測試驗 assignment 與成員移動；artifact 不動由 runner 層測試與程式碼保證）。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_exclstore"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


class WorkspaceExclusionsTests(unittest.TestCase):
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

    def _new_workspace(self, conn, name: str, patent_ids: list[int], *, is_global=False) -> int:
        import json as _json
        # ux_workspaces_is_global 部分唯一索引只允許一個全庫；建新全庫前先清掉舊的。
        if is_global:
            conn.execute("DELETE FROM app_layer.workspaces WHERE is_global")
        return conn.execute(
            "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json, is_global) "
            "VALUES (%s, %s::jsonb, %s) RETURNING workspace_id",
            (name, _json.dumps(patent_ids), is_global),
        ).fetchone()[0]

    def _conn(self):
        return psycopg.connect(**_kw(TEST_DB))

    # ── store：寫排除清單 ────────────────────────────────────
    def test_exclude_patents_writes_rows(self):
        """exclude_patents 寫入排除清單（含理由），可再讀出。"""
        from backend.app.clustering import exclusions
        with self._conn() as c:
            ws = self._new_workspace(c, "ws-write", [1, 2, 3])
            c.commit()
            exclusions.exclude_patents(
                ws, [(2, "AI 判定不相干")], conn=c)
            c.commit()
            excluded = exclusions.excluded_patent_ids(ws, conn=c)
        self.assertEqual(excluded, {2})

    def test_exclude_patents_idempotent(self):
        """重複排除同一筆不報錯（複合 PK ON CONFLICT），可更新理由。"""
        from backend.app.clustering import exclusions
        with self._conn() as c:
            ws = self._new_workspace(c, "ws-idem", [1, 2, 3])
            c.commit()
            exclusions.exclude_patents(ws, [(2, "理由一")], conn=c)
            exclusions.exclude_patents(ws, [(2, "理由二")], conn=c)
            c.commit()
            excluded = exclusions.excluded_patent_ids(ws, conn=c)
        self.assertEqual(excluded, {2})

    # ── 收口：analysis 扣除、display 不扣 ─────────────────────
    def test_analysis_members_exclude_display_members_dont(self):
        """analysis_member_patent_ids 扣除排除清單；display_member_patent_ids 不扣。"""
        from backend.app.clustering import exclusions
        with self._conn() as c:
            ws = self._new_workspace(c, "ws-collar", [10, 20, 30])
            c.commit()
            exclusions.exclude_patents(ws, [(20, "不相干")], conn=c)
            c.commit()
            analysis = exclusions.analysis_member_patent_ids(ws, conn=c)
            display = exclusions.display_member_patent_ids(ws, conn=c)
        # 分析用扣除被排除者。
        self.assertEqual(analysis, [10, 30])
        # 顯示用保留全部（使用者仍看得到被排除的 20）。
        self.assertEqual(display, [10, 20, 30])

    def test_analysis_preserves_member_order(self):
        """收口後仍保留 patent_ids_json 的原順序（只濾除、不重排）。"""
        from backend.app.clustering import exclusions
        with self._conn() as c:
            ws = self._new_workspace(c, "ws-order", [30, 10, 20, 40])
            c.commit()
            exclusions.exclude_patents(ws, [(10, "x")], conn=c)
            c.commit()
            analysis = exclusions.analysis_member_patent_ids(ws, conn=c)
        self.assertEqual(analysis, [30, 20, 40])

    # ── 全庫不扣除 ───────────────────────────────────────────
    def test_global_workspace_analysis_does_not_exclude(self):
        """全庫 workspace 的 analysis 取成員不扣除排除清單（規格第 62-64 行）。"""
        from backend.app.clustering import exclusions
        with self._conn() as c:
            ws = self._new_workspace(c, "ws-global", [10, 20, 30], is_global=True)
            c.commit()
            # 即使排除表有紀錄，全庫也不扣。
            exclusions.exclude_patents(ws, [(20, "在別的 ws 判定不相干")], conn=c)
            c.commit()
            analysis = exclusions.analysis_member_patent_ids(ws, conn=c)
        self.assertEqual(analysis, [10, 20, 30])

    def test_exclude_keeps_member_ids_by_default(self):
        """預設 remove_from_member_ids=False：patent_ids_json 不被硬移出（規格第 68 行）。

        排除表為唯一事實來源，成員清單保留，讓顯示路徑仍看得到被排除者、可反悔。
        """
        from backend.app.clustering import exclusions
        import json as _json
        with self._conn() as c:
            ws = self._new_workspace(c, "ws-keep", [10, 20, 30])
            c.commit()
            exclusions.exclude_patents(ws, [(20, "不相干")], conn=c)
            c.commit()
            raw = c.execute(
                "SELECT patent_ids_json FROM app_layer.workspaces WHERE workspace_id = %s",
                (ws,),
            ).fetchone()[0]
        ids = [int(x) for x in raw]
        # 成員清單完整保留（含被排除的 20）。
        self.assertEqual(ids, [10, 20, 30])

    def test_exclude_removes_assignments_but_not_artifact(self):
        """🔴 剔除紅線：移除該筆 topic_assignments，但 model artifact 完全不動、
        distance_to_centroid 不重算、topic_state_json（含主題向量/候選）不變（不重跑分群）。"""
        from backend.app.clustering import exclusions
        with self._conn() as c:
            # topic_assignments.patent_id FK → core_layer.patents(id)：先建 3 筆最小專利。
            pids = []
            for _ in range(3):
                pid = c.execute(
                    "INSERT INTO core_layer.patents DEFAULT VALUES RETURNING id"
                ).fetchone()[0]
                pids.append(int(pid))
            ws = self._new_workspace(c, "ws-assign", pids)
            wr = c.execute(
                "INSERT INTO app_layer.workflow_runs (run_type, status, workspace_id) "
                "VALUES ('clustering_finalize', 'succeeded', %s) RETURNING run_id",
                (ws,),
            ).fetchone()[0]
            # topic_runs.run_id 無 default（非 IDENTITY），明給 run_id；source_field 走 CHECK 白名單。
            tr = 900001
            # topic_state_json 模擬含主題向量／centroid 的 model artifact 內容。
            state = '{"topics": [{"topic_code": "t1", "vector": [0.1, 0.2]}]}'
            c.execute(
                "INSERT INTO derived_layer.topic_runs "
                "(run_id, workflow_run_id, source_field, artifact_key, topic_state_json) "
                "VALUES (%s, %s, 'wips_independent_claims', 'artifact-abc', %s::jsonb)",
                (tr, wr, state),
            )
            for i, pid in enumerate(pids):
                c.execute(
                    "INSERT INTO derived_layer.topic_assignments "
                    "(run_id, patent_id, topic_key, distance_to_centroid) VALUES (%s, %s, %s, %s)",
                    (tr, pid, "t1", 0.5 + i * 0.1),
                )
            c.commit()
            excluded_pid = pids[1]
            # 記錄剔除前存活筆的 distance_to_centroid（用於證明不重算）。
            dist_before = {
                int(r[0]): r[1] for r in c.execute(
                    "SELECT patent_id, distance_to_centroid FROM derived_layer.topic_assignments "
                    "WHERE run_id = %s", (tr,)
                ).fetchall()
            }
            exclusions.exclude_patents(ws, [(excluded_pid, "不相干")], conn=c)
            c.commit()
            rows_after = c.execute(
                "SELECT patent_id, distance_to_centroid FROM derived_layer.topic_assignments "
                "WHERE run_id = %s", (tr,)
            ).fetchall()
            remaining = {int(r[0]) for r in rows_after}
            dist_after = {int(r[0]): r[1] for r in rows_after}
            artifact, state_after = c.execute(
                "SELECT artifact_key, topic_state_json FROM derived_layer.topic_runs "
                "WHERE run_id = %s", (tr,)
            ).fetchone()
        # 被剔除者的 assignment 被移除；其餘保留。
        self.assertEqual(remaining, {pids[0], pids[2]})
        # 🔴 artifact_key 完全不動（不重跑分群的關鍵）。
        self.assertEqual(artifact, "artifact-abc")
        # 🔴 topic_state_json（含主題向量/candidates 等 model artifact）完全不變。
        self.assertEqual(state_after, {"topics": [{"topic_code": "t1", "vector": [0.1, 0.2]}]})
        # 🔴 存活筆的 distance_to_centroid 不重算——與剔除前逐一相等。
        for pid in (pids[0], pids[2]):
            self.assertEqual(dist_after[pid], dist_before[pid])

    def test_exclusion_is_workspace_scoped(self):
        """同一 patent_id 在 A 被排除、在 B 照常參與分析（排除是 workspace 級）。"""
        from backend.app.clustering import exclusions
        with self._conn() as c:
            ws_a = self._new_workspace(c, "ws-scope-a", [100, 200])
            ws_b = self._new_workspace(c, "ws-scope-b", [200, 300])
            c.commit()
            exclusions.exclude_patents(ws_a, [(200, "對 A 不相干")], conn=c)
            c.commit()
            a = exclusions.analysis_member_patent_ids(ws_a, conn=c)
            b = exclusions.analysis_member_patent_ids(ws_b, conn=c)
        self.assertEqual(a, [100])          # A 扣掉 200
        self.assertEqual(b, [200, 300])     # B 不受影響

    # ── 分群 SQL 收口（runner.ANALYSIS_MEMBER_SUBQUERY）與 Python 收口同語意 ──
    def _run_member_subquery(self, conn, workspace_id: int) -> set[int]:
        """直接執行 load_clustering_corpus 內嵌的成員取樣 SQL（單一事實來源）。"""
        from backend.app.clustering.runner import ANALYSIS_MEMBER_SUBQUERY
        rows = conn.execute(ANALYSIS_MEMBER_SUBQUERY, (workspace_id,)).fetchall()
        return {int(r[0]) for r in rows}

    def test_clustering_subquery_excludes_for_normal_workspace(self):
        """分群 SQL 收口：一般 workspace 扣除排除清單（與 Python 收口一致）。"""
        with self._conn() as c:
            ws = self._new_workspace(c, "ws-sql-normal", [1, 2, 3])
            c.commit()
            from backend.app.clustering import exclusions
            exclusions.exclude_patents(ws, [(2, "不相干")], conn=c)
            c.commit()
            members = self._run_member_subquery(c, ws)
        self.assertEqual(members, {1, 3})

    def test_clustering_subquery_does_not_exclude_for_global(self):
        """分群 SQL 收口：全庫 workspace 不扣除（規格第 62-64 行）。"""
        with self._conn() as c:
            ws = self._new_workspace(c, "ws-sql-global", [1, 2, 3], is_global=True)
            c.commit()
            from backend.app.clustering import exclusions
            exclusions.exclude_patents(ws, [(2, "在別的 ws 不相干")], conn=c)
            c.commit()
            members = self._run_member_subquery(c, ws)
        self.assertEqual(members, {1, 2, 3})


if __name__ == "__main__":
    unittest.main()
