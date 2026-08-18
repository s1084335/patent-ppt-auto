"""workspace 取證範圍收斂為單一機制（unify-workspace-scope）。

## 為什麼

2026-08-18 併線時發現同一件事有兩套實作、讀不同環境變數：

- deck：`PATENT_RESEARCH_WORKSPACE_ID` → 改寫 SQL（`workspace_scope` CTE ＋ join 閘門）
- 主線：`PATENT_REPORT_WORKSPACE_ID` → 執行後過濾回傳列

⚠ 兩者不等價：post-filter 只能砍列，擋不住 `COUNT`／`SUM`——那些數字會用全庫算完
再濾一列，錯的但看起來正常。主線的補法是在 prompt 寫「不准做彙總」——
那是**規則**不是機制。規則補得住的洞，機制本來就不該留。

保留 SQL 改寫那套：JOIN 了就正確、沒 JOIN 直接拒絕，而且錯誤訊息就是使用說明
（偏差是「多出來的」而不是缺席的）。
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SingleSourceOfTruthTests(unittest.TestCase):
    """同一份知識只能有一個定義處。"""

    def test_only_one_workspace_scope_env_constant(self):
        from backend.app.mcp_server import report_research as rr

        names = [n for n in dir(rr)
                 if n.endswith("_ENV") and "WORKSPACE" in n.upper()]
        self.assertEqual(
            len(names), 1,
            f"workspace scope 有 {len(names)} 個環境變數常數：{names}——"
            "今天靠『呼叫端不重疊』僥倖沒出事，任一邊擴大使用就會互相干擾且不報錯")

    def test_narrative_scope_delegates_to_the_single_entry(self):
        """⚠ 委派，不是兩邊都設——兩邊都設只是換個地方漂移。"""
        import inspect

        from backend.app.mcp_server import report_research as rr

        src = inspect.getsource(rr.narrative_report_scope)
        self.assertIn("workspace_scope_env", src,
                      "narrative 沒有委派給統一入口")

    def test_snapshot_binding_still_restored(self):
        """snapshot 與 workspace 無關，收斂時不得被順手拆掉。"""
        from backend.app.mcp_server import report_research as rr

        os.environ.pop(rr.NARRATIVE_SNAPSHOT_ID_ENV, None)
        with rr.narrative_report_scope(workspace_id=1, snapshot_id="v9"):
            self.assertEqual(os.environ.get(rr.NARRATIVE_SNAPSHOT_ID_ENV), "v9")
        self.assertIsNone(os.environ.get(rr.NARRATIVE_SNAPSHOT_ID_ENV),
                          "離開 scope 後沒有還原 snapshot 環境變數")


class ScopeIsAppliedBeforeAggregationTests(unittest.TestCase):
    """範圍要在彙總之前生效——這是保留 SQL 改寫那套的全部理由。"""

    IDS = [11, 22, 33]

    def _apply(self, sql: str) -> str:
        from backend.app.mcp_server import report_research as rr

        return rr._apply_workspace_scope(sql, self.IDS)

    def test_unscoped_patent_query_is_rejected_with_howto(self):
        from backend.app.mcp_server.report_research import ReportResearchError

        with self.assertRaises(ReportResearchError) as ctx:
            self._apply("SELECT * FROM patents WHERE 1=1")
        msg = str(ctx.exception)
        self.assertIn("workspace_scope", msg, "錯誤訊息沒指出要引用什麼")
        self.assertIn("JOIN", msg.upper(), "訊息沒有可照做的改寫方式")

    def test_aggregate_is_allowed_when_scoped(self):
        """🔴 與舊行為相反：舊實作一律拒絕彙總，因為 post-filter 修不了。"""
        out = self._apply(
            "SELECT count(*) FROM patents p JOIN workspace_scope s "
            "ON s.patent_id = p.patent_id")
        self.assertIn("workspace_scope(patent_id)", out.replace(" ", "").replace(
            "workspace_scope(patent_id)", "workspace_scope(patent_id)"))
        self.assertRegex(out, r"(?is)^WITH\s+workspace_scope")

    def test_injected_cte_carries_exactly_the_members(self):
        """⚠ 真的比對注入的 id，不是只驗「有注入」。"""
        out = self._apply(
            "SELECT count(*) FROM patents p JOIN workspace_scope s "
            "ON s.patent_id = p.patent_id")
        injected = [int(m) for m in re.findall(r"\((\d+)\)", out.split("VALUES", 1)[1])]
        self.assertEqual(sorted(injected), sorted(self.IDS))

    def test_existing_with_clause_is_preserved(self):
        out = self._apply(
            "WITH x AS (SELECT 1) SELECT count(*) FROM patents p "
            "JOIN workspace_scope s ON s.patent_id = p.patent_id")
        self.assertRegex(out, r"(?is)^WITH\s+workspace_scope.*,\s*x\s+AS")

    def test_empty_workspace_is_rejected_not_silently_widened(self):
        from backend.app.mcp_server import report_research as rr
        from backend.app.mcp_server.report_research import ReportResearchError

        with self.assertRaises(ReportResearchError):
            rr._apply_workspace_scope("SELECT 1", [])

    def test_non_patent_tables_need_no_scope(self):
        """彙總表（company_aliases 等）不在閘門範圍，否則會誤擋。"""
        out = self._apply("SELECT * FROM derived_layer.company_aliases")
        self.assertRegex(out, r"(?is)^WITH\s+workspace_scope")


class PostFilterIsGoneTests(unittest.TestCase):
    """拆掉靜默丟列那條路徑。"""

    def test_row_post_filter_removed(self):
        from backend.app.mcp_server import report_research as rr

        for name in ("_filter_rows_to_workspace", "_workspace_patent_ids"):
            self.assertFalse(
                hasattr(rr, name),
                f"{name} 還在——執行後過濾是一條靜默丟列的路徑，"
                "範圍已在 SQL 層限住，留著不增加保證")

    def test_scoped_validator_no_longer_bans_aggregates(self):
        import inspect

        from backend.app.mcp_server import report_research as rr

        if not hasattr(rr, "validate_scoped_narrative_sql"):
            return
        src = inspect.getsource(rr.validate_scoped_narrative_sql)
        self.assertNotIn("_SCOPED_SQL_AGGREGATE", src,
                         "還在封鎖彙總——該限制存在的唯一理由（post-filter）已消失")


class PromptDocsMatchTheMechanismTests(unittest.TestCase):
    """提示文件要描述實際生效的機制，不留已被取代的自律型限制。"""

    DOCS = ("backend/app/worker/prompts/data_access.md",
            "backend/app/worker/prompts/report-narrative-flow.md")

    def test_docs_do_not_ban_aggregates(self):
        for rel in self.DOCS:
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotRegex(
                text, r"(?i)do not use it\s+for aggregate claims",
                f"{rel} 仍寫著「不得彙總」——那條規則已被 join 閘門取代")

    def test_docs_tell_how_to_aggregate_in_scope(self):
        hits = 0
        for rel in self.DOCS:
            if "workspace_scope" in (ROOT / rel).read_text(encoding="utf-8"):
                hits += 1
        self.assertEqual(hits, len(self.DOCS),
                         "提示文件沒有告訴 CLI 彙總要 JOIN workspace_scope")


if __name__ == "__main__":
    unittest.main()
