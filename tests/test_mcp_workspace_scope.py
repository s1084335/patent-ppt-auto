"""MCP 取證的 workspace 參數過濾（2026-08-14 使用者裁決「加 workspace 參數過濾就好」）。

## 為什麼要它

`query_database` 唯讀但**全庫可讀**——deck／narrative 的 CLI 取證技術上可以
SELECT 到別的 workspace。風險形態是**正確性**不是安全：同一申請人出現在兩個
workspace 時，CLI 可能引到別包的專利，而且不會報錯（workspace 一多必然發生）。

## 機制（與稽核落檔同一條通道）

runner 起 CLI 前設 `PATENT_RESEARCH_WORKSPACE_ID`（MCP server 是 CLI 的
子行程，繼承 env）。scope 生效時：

1. server 以 workspace 成員（`app_layer.workspaces.patent_ids_json`）組
   `workspace_scope(patent_id)` CTE **注入每一條查詢**；
2. **閘門**：查詢引用 patent 級資料表（patents／patent_attributes）卻沒
   引用 `workspace_scope` → 拒絕並附改法——錯誤訊息就是使用說明。

⚠ 判準（deepen design §1.2 三問）：Q2 有自由度（CLI 可假 join），但 Q3 偏差
是**多出來的**（查詢被拒、錯誤可見），不是缺席——意外跨包（真正要防的）
被完全擋下；惡意繞過不在威脅模型（CLI 是我們自己的 prompt）。
"""
from __future__ import annotations

import os
import unittest

from backend.app.mcp_server import report_research as rr


class ScopeTransformTests(unittest.TestCase):
    """`_apply_workspace_scope`：CTE 注入與 join 閘門（純函式，不碰 DB）。"""

    IDS = [93, 94, 96]

    def test_patents_query_without_join_rejected(self):
        with self.assertRaises(rr.ReportResearchError) as ctx:
            rr._apply_workspace_scope(
                "SELECT * FROM patents WHERE 申請人 LIKE '%曾晴%'", self.IDS)
        # 錯誤訊息＝使用說明：要告訴 CLI 怎麼改
        self.assertIn("workspace_scope", str(ctx.exception))

    def test_patent_attributes_also_guarded(self):
        with self.assertRaises(rr.ReportResearchError):
            rr._apply_workspace_scope(
                "SELECT * FROM patent_attributes", self.IDS)

    def test_select_gets_cte_prepended(self):
        out = rr._apply_workspace_scope(
            "SELECT p.* FROM patents p JOIN workspace_scope s "
            "ON s.patent_id = p.patent_id", self.IDS)
        self.assertTrue(out.upper().startswith("WITH WORKSPACE_SCOPE"))
        self.assertIn("(93)", out)
        self.assertIn("(96)", out)

    def test_with_query_merges_cte(self):
        out = rr._apply_workspace_scope(
            "WITH a AS (SELECT patent_id FROM workspace_scope) SELECT * FROM a",
            self.IDS)
        # 只能有一個 WITH，scope CTE 併進去
        self.assertEqual(out.upper().count("WITH "), 1)
        self.assertIn("workspace_scope(patent_id)", out)

    def test_non_patent_tables_pass_untouched_join_rule(self):
        """查非 patent 級資料表（如 company_aliases）不必 join——但 CTE 照注入
        （在那裡沒人引用它，Postgres 允許未使用的 CTE）。"""
        out = rr._apply_workspace_scope(
            "SELECT * FROM app_layer.company_aliases", self.IDS)
        self.assertIn("workspace_scope", out)

    def test_empty_membership_fails_loud(self):
        """空 workspace 的 scope 查詢無意義——fail loud，不得靜默退全庫。"""
        with self.assertRaises(rr.ReportResearchError):
            rr._apply_workspace_scope("SELECT 1", [])


class ScopeEnvTests(unittest.TestCase):
    """env 通道：與 `query_audit_file` 同模式（設定／還原／未設＝不啟用）。"""

    def test_context_manager_sets_and_restores(self):
        before = os.environ.get(rr.SCOPE_WORKSPACE_ENV)
        with rr.workspace_scope_env(7):
            self.assertEqual(os.environ.get(rr.SCOPE_WORKSPACE_ENV), "7")
        self.assertEqual(os.environ.get(rr.SCOPE_WORKSPACE_ENV), before)

    def test_none_means_no_scope(self):
        with rr.workspace_scope_env(None):
            self.assertIsNone(os.environ.get(rr.SCOPE_WORKSPACE_ENV))
        self.assertIsNone(rr._scope_workspace_id())

    def test_bad_env_value_is_none(self):
        os.environ[rr.SCOPE_WORKSPACE_ENV] = "not-a-number"
        try:
            self.assertIsNone(rr._scope_workspace_id())
        finally:
            os.environ.pop(rr.SCOPE_WORKSPACE_ENV, None)


class DeckRunnerScopeTests(unittest.TestCase):
    """deck runner 起 CLI 時要把 workspace scope 掛上 env（版本→workspace 綁定鏈）。"""

    def test_cli_calls_carry_workspace_scope(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from backend.app.worker import ai_report_deck_runner as deck
        from tests.test_report_deck_runner import FakeCli, FakeSteps, _write

        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        root = base / "reports"
        run_dir = root / "report_trial_20990101_000000"
        _write(run_dir / "report_data.json", "{}")
        work = base / "deck_work" / run_dir.name

        class ScopeProbeSteps(FakeSteps):
            def __call__(self, step, argv):
                code, out = super().__call__(step, argv)
                if step == "assemble":
                    # 蓋掉 report.json，帶 workspace_id（intake 真的會給）
                    _write(self.work / "report.json", json.dumps({
                        "report_meta": {"workspace_name": "滑雪機",
                                        "workspace_id": 3},
                        "sections": []}))
                return code, out

        seen: list[str | None] = []

        class ScopeProbeCli(FakeCli):
            def __call__(self, argv, timeout):
                seen.append(os.environ.get(rr.SCOPE_WORKSPACE_ENV))
                return super().__call__(argv, timeout)

        deck.run_deck(
            run_dir.name, root=root, work_root=work.parent,
            artifact_root=base / "artifacts",
            step_runner=ScopeProbeSteps(work),
            cli_runner=ScopeProbeCli(work))
        # 撰稿與目視兩次 CLI 都要帶 scope
        self.assertEqual(seen, ["3", "3"])
        # 跑完要還原，不汙染後續 job
        self.assertIsNone(os.environ.get(rr.SCOPE_WORKSPACE_ENV))


if __name__ == "__main__":
    unittest.main()
