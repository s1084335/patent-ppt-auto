"""合併／解除合併的 run 必須沿用上游 artifact 的完整資訊（2026-07-27 實機 9k）。

實機症狀：合併兩個主題後再按「分類」→ `分類失敗：KeyError: 'model_artifact_hash'`，
**合併過的 workspace 永遠無法增量分群**。

根因：merge run 沿用前一版的 `artifact_key`（同一個 .pkl，因為純結構合併不動模型），
但 `topic_state_json` **只補了 artifact_version、沒補 model_artifact_hash**。
而 `_latest_completed_run` 的守門條件是 `artifact_key IS NOT NULL`——merge run 通過守門
被選為最新 run，接著 `str(latest["model_artifact_hash"])` 直接 KeyError
（`workspace_service.py:751` incremental、`:847` hierarchy_merge_suggestions）。

⚠ 這是 `model_artifact_hash` 的**第三種落點問題**（同日）：
1. `c4661dc`：finalize 寫進 metrics 子物件、incremental 讀頂層
2. 本次：merge run 根本沒寫
契約：**凡是會被 `_latest_completed_run` 選中的 run（artifact_key 非空），
其 state 就必須同時帶 model_artifact_hash**——兩者是一組，不得只搬一半。
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_SERVICE = PROJECT_ROOT / "backend" / "app" / "clustering" / "workspace_service.py"
sys.path.insert(0, str(PROJECT_ROOT))


class MergeRunCarriesArtifactTests(unittest.TestCase):
    """沿用 artifact 的兩處（merge／unmerge）都必須同時帶 key 與 hash。"""

    CARRY_FUNCTIONS = ("_persist_topic_merge", "_persist_unmerge")

    def _function_source(self, name: str) -> str:
        tree = ast.parse(WORKSPACE_SERVICE.read_text(encoding="utf-8"))
        node = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == name),
            None,
        )
        self.assertIsNotNone(node, f"找不到 {name}")
        return ast.get_source_segment(
            WORKSPACE_SERVICE.read_text(encoding="utf-8"), node) or ""

    def test_carry_functions_also_copy_hash(self):
        """沿用 artifact_key 的函式必須一併帶 model_artifact_hash。

        只搬 key 不搬 hash → 該 run 通過 _latest_completed_run 的守門，
        但下游讀 hash 時 KeyError。
        """
        for name in self.CARRY_FUNCTIONS:
            with self.subTest(function=name):
                src = self._function_source(name)
                if "artifact_key" not in src:
                    continue  # 該函式不碰 artifact，無此問題
                self.assertTrue(
                    "model_artifact_hash" in src,
                    f"{name} 沿用了 artifact_key 卻沒帶 model_artifact_hash"
                    "——該 run 會被選為最新 run，下游讀 hash 即 KeyError（實機 9k）")

    def test_latest_completed_run_guards_hash(self):
        """_latest_completed_run 的守門條件必須連 hash 一起檢查。

        僅檢查 artifact_key IS NOT NULL 不夠——缺 hash 的 run 照樣被選中。
        這是防禦層：即使日後有人又寫出「只搬 key」的路徑，也不會炸在下游。
        """
        src = self._function_source("_latest_completed_run")
        self.assertTrue(
            "model_artifact_hash" in src,
            "_latest_completed_run 未把 model_artifact_hash 納入守門條件——"
            "缺 hash 的 run 仍會被選為最新 run")


class MergeArtifactCarryBehaviourTests(unittest.TestCase):
    """純函式層級驗證：沿用邏輯把 key 與 hash 視為一組。"""

    def test_carry_helper_exists(self):
        """應有共用 helper 把「沿用上游 artifact」收口成一處。

        merge 與 unmerge 都要做同一件事；分開實作＝下次又只改一邊
        （本專案本日已出現 12 次同型斷鏈）。
        """
        from backend.app.clustering import workspace_service as ws

        self.assertTrue(
            hasattr(ws, "_carry_artifact_from"),
            "缺少沿用上游 artifact 的共用 helper（_carry_artifact_from）")



class CarryArtifactDatabaseTests(unittest.TestCase):
    """真跑 DB：merge/unmerge 後 _latest_completed_run 仍取得完整 artifact 資訊。

    這條驗的是使用者的實際要求——「就算執行過合併或拆分，再執行 incremental 仍要能運作」。
    上面的 AST 測試只驗「程式碼有寫」，這條驗「跑起來真的對」。
    """

    TEST_DB = "patent_ppt_carryart"

    @classmethod
    def setUpClass(cls):
        import os
        import psycopg
        from alembic import command
        from alembic.config import Config

        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = cls.TEST_DB
        kw = dict(host="127.0.0.1", port=int(os.getenv("PGPORT", "5433")),
                  user=os.getenv("PGUSER", "postgres"), dbname="postgres")
        if os.getenv("PGPASSWORD"):
            kw["password"] = os.getenv("PGPASSWORD")
        try:
            with psycopg.connect(**kw, autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{cls.TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{cls.TEST_DB}"')
        except Exception as exc:
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        command.upgrade(cfg, "head")

    @classmethod
    def tearDownClass(cls):
        import os
        import psycopg

        for key, value in cls._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        kw = dict(host="127.0.0.1", port=int(os.getenv("PGPORT", "5433")),
                  user=os.getenv("PGUSER", "postgres"), dbname="postgres")
        if os.getenv("PGPASSWORD"):
            kw["password"] = os.getenv("PGPASSWORD")
        try:
            with psycopg.connect(**kw, autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{cls.TEST_DB}" WITH (FORCE)')
        except Exception:
            pass

    def _conn(self):
        import os
        import psycopg

        kw = dict(host="127.0.0.1", port=int(os.getenv("PGPORT", "5433")),
                  user=os.getenv("PGUSER", "postgres"), dbname=self.TEST_DB)
        if os.getenv("PGPASSWORD"):
            kw["password"] = os.getenv("PGPASSWORD")
        return psycopg.connect(**kw, autocommit=True)

    def test_merge_run_keeps_hash_so_incremental_works(self):
        """合併後 _latest_completed_run 取到 merge run，且 hash 完整可用。"""
        import json

        from backend.app.clustering import workspace_service as ws

        SF = "wips_independent_claims"
        HASH = "a" * 64
        with self._conn() as c:
            wsid = c.execute(
                "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json) "
                "VALUES ('ws-carry', '[]') RETURNING workspace_id").fetchone()[0]
            # 上游 full run：有 key ＋ hash
            wf1 = c.execute(
                "INSERT INTO app_layer.workflow_runs (workspace_id, run_type, status) "
                "VALUES (%s,'clustering_finalize','succeeded') RETURNING run_id",
                (wsid,)).fetchone()[0]
            c.execute(
                "INSERT INTO derived_layer.topic_runs "
                "(run_id, workflow_run_id, source_field, topic_state_json, artifact_key) "
                "VALUES (%s,%s,%s,%s,%s)",
                (wf1, wf1, SF, json.dumps({
                    "status": "completed", "run_mode": "full", "artifact_version": 1,
                    "model_artifact_hash": HASH, "topics": []}), "clustering/ws/run_1.pkl"))
            # 下游 merge run：初始沒有 key／hash（模擬 _create_merge_run 剛建好的狀態）
            wf2 = c.execute(
                "INSERT INTO app_layer.workflow_runs (workspace_id, run_type, status) "
                "VALUES (%s,'topic_merge','succeeded') RETURNING run_id",
                (wsid,)).fetchone()[0]
            c.execute(
                "INSERT INTO derived_layer.topic_runs "
                "(run_id, workflow_run_id, previous_run_id, source_field, topic_state_json) "
                "VALUES (%s,%s,%s,%s,%s)",
                (wf2, wf2, wf1, SF, json.dumps({
                    "status": "completed", "run_mode": "merge", "artifact_version": 2,
                    "topics": []})))
            # 走受測的沿用邏輯
            with c.cursor() as cur:
                ws._carry_artifact_from(cur, previous_run_id=wf1, run_id=wf2)

        latest = ws._latest_completed_run(workspace_id=wsid, source_field=SF)
        self.assertEqual(latest["run_id"], wf2, "應取到 merge run（最新）")
        self.assertEqual(latest["model_artifact_hash"], HASH,
                         "merge run 必須帶著上游的 hash——否則 incremental 讀它就 KeyError")
        self.assertEqual(latest["model_artifact_path"], "clustering/ws/run_1.pkl")

    def test_run_without_hash_is_skipped_not_crashed(self):
        """防禦層：缺 hash 的 run 被跳過、退回上一個完整 run，而不是被選中後炸掉。"""
        import json

        from backend.app.clustering import workspace_service as ws

        SF = "effect_summary"
        HASH = "b" * 64
        with self._conn() as c:
            wsid = c.execute(
                "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json) "
                "VALUES ('ws-guard', '[]') RETURNING workspace_id").fetchone()[0]
            wf1 = c.execute(
                "INSERT INTO app_layer.workflow_runs (workspace_id, run_type, status) "
                "VALUES (%s,'clustering_finalize','succeeded') RETURNING run_id",
                (wsid,)).fetchone()[0]
            c.execute(
                "INSERT INTO derived_layer.topic_runs "
                "(run_id, workflow_run_id, source_field, topic_state_json, artifact_key) "
                "VALUES (%s,%s,%s,%s,%s)",
                (wf1, wf1, SF, json.dumps({
                    "status": "completed", "artifact_version": 1,
                    "model_artifact_hash": HASH, "topics": []}), "clustering/ws/run_1.pkl"))
            # 壞掉的 run：有 key、artifact_version 更高，但沒有 hash
            wf2 = c.execute(
                "INSERT INTO app_layer.workflow_runs (workspace_id, run_type, status) "
                "VALUES (%s,'topic_merge','succeeded') RETURNING run_id",
                (wsid,)).fetchone()[0]
            c.execute(
                "INSERT INTO derived_layer.topic_runs "
                "(run_id, workflow_run_id, source_field, topic_state_json, artifact_key) "
                "VALUES (%s,%s,%s,%s,%s)",
                (wf2, wf2, SF, json.dumps({
                    "status": "completed", "artifact_version": 9, "topics": []}),
                 "clustering/ws/run_1.pkl"))

        latest = ws._latest_completed_run(workspace_id=wsid, source_field=SF)
        self.assertEqual(latest["run_id"], wf1,
                         "缺 hash 的 run 應被跳過，退回上一個完整的 run")
        self.assertEqual(latest["model_artifact_hash"], HASH)


class ArbitraryOperationOrderTests(CarryArtifactDatabaseTests):
    """任意操作順序都要能接續（2026-07-27 使用者定）。

    使用者原話：「沒執行過合併，incremental 要成功；incremental 後要能支援合併，
    以此類推，各 workspace 和全庫都一樣。」

    核心不變式：**每個 completed run 都必須帶著可用的 artifact（key ＋ hash）**——
    不論它是 full／incremental／merge／unmerge 產生的。只要這條成立，任意順序組合
    都能接續，因為每一步都拿得到上一步的模型。

    本測試以 run 鏈模擬各種順序，驗 `_latest_completed_run` 每次都取到完整資訊。
    """

    TEST_DB = "patent_ppt_oporder"

    def _chain(self, *, is_global: bool, steps: list[str]) -> tuple[int, str]:
        """依 steps 建 run 鏈，回 (workspace_id, 最後一個 run 的 hash)。

        steps 用 'full'／'incremental'／'merge'／'unmerge'；merge/unmerge 走
        _carry_artifact_from 沿用上游，其餘視為重訓（自帶新 hash）。
        """
        import json

        from backend.app.clustering import workspace_service as ws

        SF = "wips_independent_claims"
        with self._conn() as c:
            # ⚠ DB 有唯一約束 ux_workspaces_is_global：**全庫只能有一個**。
            # 故全庫情境重用同一個 workspace，每輪先清掉它的舊 run 鏈再建新的。
            if is_global:
                row = c.execute(
                    "SELECT workspace_id FROM app_layer.workspaces WHERE is_global").fetchone()
                if row is None:
                    wsid = c.execute(
                        "INSERT INTO app_layer.workspaces "
                        "(workspace_name, patent_ids_json, is_global) "
                        "VALUES ('ws-global','[]',TRUE) RETURNING workspace_id").fetchone()[0]
                else:
                    wsid = row[0]
                    c.execute(
                        "DELETE FROM derived_layer.topic_runs WHERE run_id IN ("
                        "  SELECT tr.run_id FROM derived_layer.topic_runs tr"
                        "  JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id"
                        "  WHERE wr.workspace_id = %s)", (wsid,))
                    c.execute(
                        "DELETE FROM app_layer.workflow_runs WHERE workspace_id = %s", (wsid,))
            else:
                wsid = c.execute(
                    "INSERT INTO app_layer.workspaces "
                    "(workspace_name, patent_ids_json, is_global) VALUES (%s,'[]',FALSE) "
                    "RETURNING workspace_id",
                    (f"ws-{'-'.join(steps)}",),
                ).fetchone()[0]
            prev, last_hash, version = None, None, 0
            for index, mode in enumerate(steps):
                version += 1
                wf = c.execute(
                    "INSERT INTO app_layer.workflow_runs (workspace_id, run_type, status) "
                    "VALUES (%s,%s,'succeeded') RETURNING run_id",
                    (wsid, "topic_merge" if mode in ("merge", "unmerge")
                     else "clustering_finalize"),
                ).fetchone()[0]
                if mode in ("merge", "unmerge"):
                    # 純結構操作：建 run 時不帶 artifact，靠 _carry_artifact_from 沿用
                    c.execute(
                        "INSERT INTO derived_layer.topic_runs (run_id, workflow_run_id, "
                        "previous_run_id, source_field, topic_state_json) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (wf, wf, prev, SF, json.dumps({
                            "status": "completed", "run_mode": mode,
                            "artifact_version": version, "topics": []})))
                    with c.cursor() as cur:
                        ws._carry_artifact_from(cur, previous_run_id=prev, run_id=wf)
                else:
                    # 重訓：自帶新 artifact
                    last_hash = f"{index:064d}"
                    c.execute(
                        "INSERT INTO derived_layer.topic_runs (run_id, workflow_run_id, "
                        "previous_run_id, source_field, topic_state_json, artifact_key) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        (wf, wf, prev, SF, json.dumps({
                            "status": "completed", "run_mode": mode,
                            "artifact_version": version,
                            "model_artifact_hash": last_hash, "topics": []}),
                         f"clustering/ws{wsid}/run_{wf}.pkl"))
                prev = wf
        return wsid, last_hash

    def test_all_operation_orders_keep_usable_artifact(self):
        """各種順序組合下，最新 run 都拿得到可用的 artifact。"""
        from backend.app.clustering import workspace_service as ws

        orders = [
            ["full"],                                   # 只分群
            ["full", "incremental"],                    # 沒合併過 → 增量
            ["full", "incremental", "merge"],           # 增量後合併
            ["full", "merge", "incremental"],           # 合併後增量（實機 9k 炸的那條）
            ["full", "merge", "unmerge", "incremental"],       # 合併→拆分→增量
            ["full", "merge", "incremental", "merge"],         # 合併→增量→再合併
            ["full", "incremental", "merge", "unmerge", "incremental"],  # 混合
        ]
        for is_global in (False, True):
            for steps in orders:
                with self.subTest(global_ws=is_global, order="→".join(steps)):
                    wsid, expected_hash = self._chain(is_global=is_global, steps=steps)
                    latest = ws._latest_completed_run(
                        workspace_id=wsid, source_field="wips_independent_claims")
                    self.assertEqual(
                        latest["model_artifact_hash"], expected_hash,
                        f"順序 {steps} 下最新 run 的 hash 不對——"
                        "下一步操作會拿不到模型")
                    self.assertIsNotNone(
                        latest.get("model_artifact_path"),
                        f"順序 {steps} 下最新 run 沒有 artifact 路徑")


if __name__ == "__main__":
    unittest.main()
