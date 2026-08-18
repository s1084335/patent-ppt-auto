"""P2 第 3 節：report-research 唯讀 MCP profile（tasks 3.1–3.4、3.6）。

PRT-012（2026-08-07 回寫）：唯讀邊界由**工具層 allowlist ＋ 憑證隔離**保證，
不採 DB reader role（正式部署為公司內網自管伺服器、CLI 依架構不持有 credential）。

⚠ 為什麼要獨立 profile 而非在現有 server 隱藏寫入工具（design.md 第 2 點）：
同一 registry 日後新增工具容易**無聲擴權**；獨立 profile ＋ allowlist contract
test 讓任何新增工具都必須先讓測試紅、經人工複審。
"""
from __future__ import annotations

import unittest

from backend.app.mcp_server import report_research as rr


def _fake_snapshot(snapshot_id: str) -> dict:
    """假的報表版本資料（單元測試不碰 DB；真實路徑由 loader 注入 report_data.json）。"""
    return {
        "chart_rows": {
            "applicant_ranking": [
                {"applicant_display_name": "曾晴", "patent_count": 14},
                {"applicant_display_name": "廈門帝瑪斯健康科技", "patent_count": 13},
                {"applicant_display_name": "美商扭矩體適能", "patent_count": 4},
            ],
            "applicant_strength_profile": [
                {"applicant_display_name": "美商扭矩體適能", "patent_count": 4,
                 "family_count": 1, "country_count": 4, "topic_count": 1,
                 "granted_count": 0, "dead_count": 0, "kind_summary": "發明4"},
            ],
        },
        "population": {"applicant_ranking": "母體 55/55 件（含共同申請）"},
        "patents": [{"patent_id": 1, "patent_number": "TW-M641704", "title": "Fitness mechanism"}],
    }


class ToolAllowlistTests(unittest.TestCase):
    def test_exact_allowlist(self):
        """清單變動就要紅——擴權必須經人工複審。"""
        self.assertEqual(sorted(rr.TOOL_NAMES), sorted([
            "list_report_catalog",
            "preview_report_rows",
            "query_report_evidence",
            "get_chart_metadata",
            "lookup_company_evidence",
            "lookup_topic_evidence",
            "lookup_patent_evidence",
            # 2026-08-09 新增並經複審：其餘七支讀的是**報表快照**
            # （report_data.json），只有這支真的連資料庫——不加它，
            # 「取證統一到 MCP」等於把敘述線原有的 DB 查詢能力砍掉。
            # 唯讀性由 validate_sql（單句 SELECT/WITH）與連線層
            # default_transaction_read_only 雙重把關。
            "query_database",
        ]))

    def test_no_write_capable_tool_registered(self):
        banned = ("save", "refresh", "generate", "apply", "delete", "update",
                  "insert", "write", "shell", "exec", "run_")
        for name in rr.TOOL_NAMES:
            for token in banned:
                self.assertNotIn(token, name,
                                 f"唯讀 profile 出現疑似寫入工具：{name}")

    def test_tools_reject_sql_strings(self):
        """typed 查詢，不接受 SQL 字串（design.md 第 2 點）。"""
        with self.assertRaises(rr.ReportResearchError):
            rr.query_report_evidence(report_key="SELECT * FROM patents", snapshot_id="s1")


class CredentialIsolationTests(unittest.TestCase):
    """CLI 可見的 MCP 設定不得洩漏憑證，且只能看到唯讀 profile。

    ⚠ 2026-08-13 改指受測對象：原本驗的是 `report_research.build_cli_mcp_config`
    （http 版），但**產品實際發給 CLI 的是** `cli_gateway.build_stdio_mcp_config()`
    ——Companion 與 CLI 同機，走 stdio 免 token、免開埠。那支 http 版全庫只有這兩支
    測試在用，且它宣告的 server 名 `patent-report-research` 與白名單前綴
    `mcp__patent_research__*` 對不上（連字號 vs 底線），誰改用它 MCP 工具會**靜默**
    全部不可用。函式已刪除，兩個判準原樣移到實際走的那條路徑上。
    """

    def _config(self):
        from backend.app.worker import cli_gateway as gw

        return gw.build_stdio_mcp_config()

    def test_cli_config_has_no_credential(self):
        """CLI 可見設定不得含 DB 連線字串／密碼／service key。"""
        blob = str(self._config()).lower()
        for leaked in ("postgres://", "postgresql://", "password", "service_role",
                       "database_url", "supabase"):
            self.assertNotIn(leaked, blob, f"CLI config 疑似洩漏憑證：{leaked}")

    def test_config_only_exposes_research_profile(self):
        """只掛一個 server，且它就是唯讀 profile。"""
        from backend.app.worker import cli_gateway as gw

        servers = self._config().get("mcpServers") or {}
        self.assertEqual(list(servers), [gw.MCP_SERVER_NAME])
        # ⚠ server 名與白名單前綴是同一份知識：對不上時工具會靜默全部不可用，
        # 故由同一個常數推導，不各寫一份字面值。
        self.assertTrue(
            all(t.startswith(f"mcp__{gw.MCP_SERVER_NAME}__")
                for t in gw.RESEARCH_TOOLS if t.startswith("mcp__")),
            "MCP 工具前綴與 config 的 server 名不一致——工具會靜默不可用")
        self.assertIn("--profile", servers[gw.MCP_SERVER_NAME]["args"])
        self.assertIn("research", servers[gw.MCP_SERVER_NAME]["args"])


class SnapshotScopeTests(unittest.TestCase):
    def test_query_requires_snapshot(self):
        with self.assertRaises(rr.ReportResearchError):
            rr.query_report_evidence(report_key="applicant_ranking", snapshot_id="")

    def test_unknown_report_key_rejected(self):
        with self.assertRaises(rr.ReportResearchError):
            rr.query_report_evidence(report_key="no_such_report", snapshot_id="s1")

    def test_row_limit_capped(self):
        """列數上限：不得讓 CLI 一次拉走整個資料集。"""
        self.assertLessEqual(rr.MAX_EVIDENCE_ROWS, 200)
        with self.assertRaises(rr.ReportResearchError):
            rr.query_report_evidence(report_key="applicant_ranking", snapshot_id="s1",
                                     limit=rr.MAX_EVIDENCE_ROWS + 1)


class NarrativeWorkspaceScopeTests(unittest.TestCase):
    def test_scoped_query_database_rejects_aggregate_sql(self):
        """scoped narrative 不允許用 raw SQL 產生全庫彙總證據。"""
        with self.assertRaises(rr.ReportResearchError):
            rr.validate_scoped_narrative_sql(
                "SELECT count(*) AS total FROM core_layer.patents"
            )

    def test_scoped_query_database_requires_patent_identity(self):
        """scoped narrative 的 raw SQL 必須回傳 patent_id/id 才能過濾。"""
        with self.assertRaises(rr.ReportResearchError):
            rr.validate_scoped_narrative_sql(
                "SELECT title FROM core_layer.patents"
            )

    def test_scoped_row_filter_keeps_only_workspace_patents(self):
        """查詢結果會依 workspace patent ids 做最後一道 row-level 過濾。"""
        rows = [(1, "A"), (2, "B"), (3, "C")]
        filtered = rr._filter_rows_to_workspace(["patent_id", "title"], rows, {1, 3})
        self.assertEqual(filtered, [(1, "A"), (3, "C")])

    def test_narrative_report_scope_restores_environment(self):
        """scope context 結束後不能污染下一個 CLI job。"""
        import os

        self.assertIsNone(os.environ.get(rr.NARRATIVE_WORKSPACE_ID_ENV))
        with rr.narrative_report_scope(workspace_id=42, snapshot_id="report_trial_x"):
            self.assertEqual(os.environ.get(rr.NARRATIVE_WORKSPACE_ID_ENV), "42")
            self.assertEqual(os.environ.get(rr.NARRATIVE_SNAPSHOT_ID_ENV), "report_trial_x")
        self.assertIsNone(os.environ.get(rr.NARRATIVE_WORKSPACE_ID_ENV))
        self.assertIsNone(os.environ.get(rr.NARRATIVE_SNAPSHOT_ID_ENV))


class CatalogTests(unittest.TestCase):
    def test_catalog_reuses_report_definitions(self):
        """目錄沿用 REPORT_DEFINITIONS，不另維護一份報表清單。"""
        import inspect

        src = inspect.getsource(rr.list_report_catalog)
        self.assertIn("REPORT_DEFINITIONS", src)

    def test_catalog_entries_carry_semantics(self):
        catalog = rr.list_report_catalog()
        self.assertTrue(catalog)
        for entry in catalog:
            for field in ("name", "label_zh", "report_type", "answers"):
                self.assertIn(field, entry)


class EvidenceCapabilityTests(unittest.TestCase):
    """🔴 本節的最大目標：**CLI 能去資料庫找證據來寫簡報**（2026-08-07 使用者
    校正）。白名單與憑證隔離是配套，不是重點——以下驗「真的查得到、查得準」。
    """

    SNAPSHOT = "report_trial_x"

    def test_catalog_tells_cli_what_each_report_answers(self):
        """CLI 要能從目錄判斷「想回答這個問題該查哪張報表」。"""
        catalog = {e["name"]: e for e in rr.list_report_catalog()}
        self.assertIn("applicant_ranking", catalog)
        self.assertTrue(catalog["applicant_ranking"]["answers"],
                        "目錄要說明這張報表回答什麼問題，否則 CLI 只能猜")

    def test_query_returns_rows_for_writing(self):
        """依 report_key 取回可直接引用的數據列（含欄位與母體註記）。"""
        result = rr.query_report_evidence(
            report_key="applicant_ranking", snapshot_id=self.SNAPSHOT,
            snapshot_loader=_fake_snapshot)
        self.assertEqual(result["report_key"], "applicant_ranking")
        self.assertTrue(result["rows"])
        self.assertIn("population_note", result)
        self.assertEqual(result["snapshot_id"], self.SNAPSHOT)

    def test_query_supports_typed_filter(self):
        """具名查證：只要某幾家的列（寫 KP 段落時要）。"""
        result = rr.query_report_evidence(
            report_key="applicant_ranking", snapshot_id=self.SNAPSHOT,
            filters={"applicant_display_name": ["曾晴"]},
            snapshot_loader=_fake_snapshot)
        names = {r["applicant_display_name"] for r in result["rows"]}
        self.assertEqual(names, {"曾晴"})

    def test_company_evidence_aggregates_for_narrative(self):
        """公司具名證據：件數、主題、國別——寫「扭矩是全球化布局者」要用。"""
        ev = rr.lookup_company_evidence(
            "美商扭矩體適能", snapshot_id=self.SNAPSHOT, snapshot_loader=_fake_snapshot)
        self.assertEqual(ev["applicant"], "美商扭矩體適能")
        self.assertIn("patent_count", ev)
        self.assertIn("evidence_ref", ev, "每筆證據要能被 narrative 引用")

    def test_patent_evidence_returns_identifiers(self):
        """專利號級證據：簡報要點名某件時用。"""
        ev = rr.lookup_patent_evidence(
            patent_ids=[1], snapshot_id=self.SNAPSHOT, snapshot_loader=_fake_snapshot)
        self.assertTrue(ev["patents"])
        self.assertIn("patent_number", ev["patents"][0])

    def test_truncation_is_disclosed(self):
        """超過上限要明說截斷，不得靜默給一半讓 CLI 以為是全部。"""
        result = rr.query_report_evidence(
            report_key="applicant_ranking", snapshot_id=self.SNAPSHOT, limit=1,
            snapshot_loader=_fake_snapshot)
        self.assertTrue(result["truncated"])
        self.assertGreater(result["total"], len(result["rows"]))


if __name__ == "__main__":
    unittest.main()
