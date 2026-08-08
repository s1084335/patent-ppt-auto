"""headless CLI 呼叫的**唯一入口**與取證通道（2026-08-09 使用者定案）。

兩條定案合在一起做：

1. 「整合到 MCP 去，包括敘述線也是」——取證一律走 MCP 工具，不再各線各自
   用 `Bash(uv run:*)` 呼叫查詢閘道。
2. 「剩下能整合的都要整合」——`build_cli_command`／`_CLI_SPECS` 原本散在**七個**
   模組各自定義，其中三份一字不差只差例外類別名。

⚠ 整合的是 **argv 骨架**，不是權限。四支最小權限任務（company_zh_name、
irrelevant_filter、patent_note、report_ppt 舊路徑）資料內嵌 prompt，本來就
不需要任何工具——把它們併進同一份白名單是**擴權**，是安全退步不是整合。
所以權限做成顯式等級常數，由各 runner 宣告自己要哪一級。
"""
from __future__ import annotations

import unittest
from pathlib import Path

_WORKER = Path(__file__).resolve().parents[1] / "backend" / "app" / "worker"


class SingleGatewayTests(unittest.TestCase):
    """CLI 指令組裝只能有一個定義處。"""

    def test_cli_specs_defined_once(self):
        """`_CLI_SPECS` 全 repo 只有 cli_gateway 定義。

        ⚠ 多份落點的實害不是重複程式碼，是**改一處不會同步**：加 MCP 白名單
        要改七處，漏一處那條線就查不到 DB，而且不會報錯。
        """
        owners = [p.name for p in _WORKER.glob("*.py")
                  if "_CLI_SPECS: dict" in p.read_text(encoding="utf-8")
                  or "_CLI_SPECS = {" in p.read_text(encoding="utf-8")]
        self.assertEqual(owners, ["cli_gateway.py"], f"_CLI_SPECS 有多個定義處：{owners}")

    def test_build_cli_command_defined_once(self):
        """各 runner 只能 import，不得自己再寫一份 build_cli_command。"""
        owners = [p.name for p in _WORKER.glob("*.py")
                  if "def build_cli_command(" in p.read_text(encoding="utf-8")]
        self.assertEqual(owners, ["cli_gateway.py"], f"build_cli_command 有多份：{owners}")


class ToolTierTests(unittest.TestCase):
    """權限分級顯式宣告，整合骨架不擴權。"""

    def test_three_tiers_exist(self):
        from backend.app.worker import cli_gateway as gw

        self.assertEqual(gw.NO_TOOLS, "", "最小權限等級＝空白名單")
        self.assertEqual(gw.READ_ONLY_TOOLS, "Read")
        self.assertTrue(gw.RESEARCH_TOOLS, "取證等級要有工具")

    def test_research_tier_uses_mcp_not_bash(self):
        """取證改走 MCP：不得再靠 `Bash(uv run:*)` 呼叫查詢閘道。

        ⚠ Bash 前綴放行等於把「查哪張表」的判斷交給提示詞；MCP 工具是 typed
        參數，介面本身就是護欄。
        """
        from backend.app.worker import cli_gateway as gw

        joined = " ".join(gw.RESEARCH_TOOLS)
        # 前綴＝MCP config 的 server 名（cli_gateway.MCP_SERVER_NAME），
        # 命名在 Red 之後才定案為 patent_research（底線，便於白名單字串比對）。
        self.assertIn(f"mcp__{gw.MCP_SERVER_NAME}__", joined, "取證等級要放行 MCP 工具")
        self.assertNotIn("Bash", joined, "取證改走 MCP 後不應再放行 Bash")

    def test_research_argv_carries_mcp_config(self):
        """放行 mcp__ 工具但沒帶 --mcp-config＝工具根本起不來（靜默失效）。"""
        from backend.app.worker import cli_gateway as gw

        argv = gw.build_cli_command("claude", "hi", tools=gw.RESEARCH_TOOLS)
        self.assertIn("--mcp-config", argv)
        self.assertIn("--allowedTools", argv)

    def test_minimal_tier_stays_empty(self):
        """最小權限任務不得因為整合而拿到工具。"""
        from backend.app.worker import cli_gateway as gw

        argv = gw.build_cli_command("claude", "hi", tools=gw.NO_TOOLS)
        self.assertNotIn("--mcp-config", argv, "無工具等級不該掛 MCP")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "")

    def test_unknown_cli_kind_rejected(self):
        from backend.app.worker import cli_gateway as gw

        with self.assertRaises(gw.CliGatewayError):
            gw.build_cli_command("nope", "hi")


class MinimalPrivilegePreservedTests(unittest.TestCase):
    """四支最小權限 runner 併入後仍是空白名單（回歸防護）。"""

    CASES = ("ai_company_zh_name_runner", "ai_irrelevant_filter_runner",
             "ai_patent_note_runner", "ai_report_ppt_runner")

    def test_each_runner_declares_no_tools(self):
        import importlib

        from backend.app.worker import cli_gateway as gw

        for name in self.CASES:
            module = importlib.import_module(f"backend.app.worker.{name}")
            argv = module.build_cli_command("claude", "hi")
            with self.subTest(runner=name):
                self.assertEqual(argv[argv.index("--allowedTools") + 1], gw.NO_TOOLS,
                                 f"{name} 的白名單被擴權了")


class DatabaseEvidenceToolTests(unittest.TestCase):
    """MCP 要真的查得到資料庫，否則「統一到 MCP」是把取證能力砍掉。

    ⚠ 現有 report_research 七支工具讀的是 `report_data.json`（引擎產的報表
    快照），**不是資料庫**。敘述線原本靠 query_patents.py 連 DB；不補這支，
    改走 MCP 就是退步。
    """

    def test_sql_tool_exists_and_is_registered(self):
        from backend.app.mcp_server import report_research as rr

        self.assertIn("query_database", rr.TOOL_NAMES)
        self.assertTrue(callable(getattr(rr, "query_database", None)))

    def test_non_select_rejected(self):
        from backend.app.mcp_server import report_research as rr

        for sql in ("UPDATE patents SET title='x'", "DROP TABLE patents",
                    "SELECT 1; DELETE FROM patents"):
            with self.subTest(sql=sql), self.assertRaises(rr.ReportResearchError):
                rr.query_database(sql)

    def test_limit_capped(self):
        """上限對齊原查詢閘道（500 預設／2000 上限），不因換通道縮權。

        ⚠ 用快照工具的 MAX_EVIDENCE_ROWS（200）會讓逐案清單被靜默截斷——
        那是換通道造成的能力退步，不是安全收緊。
        """
        from backend.app.mcp_server import report_research as rr

        self.assertEqual(rr.SQL_DEFAULT_ROWS, 500)
        self.assertEqual(rr.SQL_MAX_ROWS, 2000)
        with self.assertRaises(rr.ReportResearchError):
            rr.query_database("SELECT 1", limit=rr.SQL_MAX_ROWS + 1)


if __name__ == "__main__":
    unittest.main()
