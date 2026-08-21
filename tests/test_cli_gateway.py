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
import inspect
from pathlib import Path
from typing import Any, ClassVar

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

    def test_four_tiers_exist(self):
        from backend.app.worker import cli_gateway as gw

        self.assertEqual(gw.NO_TOOLS, "", "最小權限等級＝空白名單")
        self.assertEqual(gw.READ_ONLY_TOOLS, "Read")
        self.assertTrue(gw.RESEARCH_TOOLS, "取證等級要有工具")
        self.assertEqual(gw.WEB_RESEARCH_TOOLS, ("WebSearch", "WebFetch"))

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
    """每一條已註冊 AI job 都必須有實際 argv 路徑與明示權限。"""

    EXPECTED_TIERS: ClassVar[dict[str, str]] = {
        "ai:narrative": "RESEARCH_TOOLS",
        "ai:topic_backfill": "NO_TOOLS",
        "ai:topic_label": "READ_ONLY_TOOLS",
        "ai:patent_note": "READ_ONLY_TOOLS",
        "ai:candidate_explanation": "NO_TOOLS",
        "ai:company_zh_name": "READ_ONLY_TOOLS",
        "ai:company_group_suggestion": "WEB_RESEARCH_TOOLS",
        # 2026-08-18 補複審：`eaeefa4`（08-14）註冊了這條 job type 卻沒登記等級，
        # 本測試自那時起一直紅——它存在的目的正是逼人做這個複審。
        # 複審結論：runner 的 `build_company_normalization_cli_command` 只放行
        # WebSearch／WebFetch（`WEB_RESEARCH_TOOLS`），與姊妹 job 同級；查證公司
        # 歸屬本來就要連外查公開資料，但不得碰檔案系統或 Bash。
        "ai:company_normalization_suggestion": "WEB_RESEARCH_TOOLS",
        "ai:irrelevant_filter": "READ_ONLY_TOOLS",
        # 簡報產製（撰稿＋逐頁目視）：與 narrative 同級（design §2 定案）——
        # 讀素材、唯讀 MCP 取證、寫 content.json／verdict；無 Bash。
        # 初階篩選的中英轉換（2026-08-21）：只把一個關鍵字轉成英文詞族，
        # 不讀檔、不查 DB、不上網 ⇒ 最小權限。
        # ⚠ runner 自行 partial(tools=NO_TOOLS)，不從其他 runner import
        # ——那些是 partial(tools=RESEARCH_TOOLS)，會靜默取得 12 支工具＋MCP。
        "ai:keyword_expand": "NO_TOOLS",
        }

    DATA_FILE_RUNNERS: ClassVar[dict[str, tuple[str, str]]] = {
        "ai:topic_label": ("ai_topic_label_runner", "run_topic_label"),
        "ai:patent_note": ("ai_patent_note_runner", "run_patent_note"),
        "ai:company_zh_name": ("ai_company_zh_name_runner", "run_company_zh_name"),
        "ai:irrelevant_filter": ("ai_irrelevant_filter_runner", "run_irrelevant_filter"),
    }

    def _assert_policy_is_complete(self, job_types):
        self.assertEqual(
            set(job_types),
            set(self.EXPECTED_TIERS),
            "AI_JOB_TYPES 有新增或移除時，必須同步複審實際 argv 的最小權限等級",
        )

    def _actual_argv(self, job_type):
        import importlib

        from backend.app.worker import ai_payload_file as pf

        if job_type in self.DATA_FILE_RUNNERS:
            module_name, function_name = self.DATA_FILE_RUNNERS[job_type]
            module = importlib.import_module(f"backend.app.worker.{module_name}")
            source = inspect.getsource(getattr(module, function_name))
            self.assertIn(
                "pf.build_cli_command_with_payload",
                source,
                f"{job_type} 產品路徑未走 READ_ONLY payload helper",
            )
            return pf.build_cli_command_with_payload(
                "claude", instruction="test", payload_path=Path("payload.json")
            )
        if job_type == "ai:narrative":
            from backend.app.worker import ai_narrative_runner as module
            return module.build_cli_command("claude", "test")
        if job_type == "ai:topic_backfill":
            from unittest import mock

            from backend.app.worker import ai_topic_backfill_runner as module
            from backend.app.worker import cli_gateway as gw

            seen = []

            def fake_run_cli(argv, _timeout):
                seen.append(list(argv))
                return gw.CliResult(
                    exit_code=0, stdout='{"result": "{}"}', stderr=""
                )

            with mock.patch.object(module.cli_gateway, "run_cli", fake_run_cli):
                module.build_cli_runner("claude", None)("test", timeout_seconds=1)
            return seen[0]
        if job_type == "ai:candidate_explanation":
            from backend.app.worker import ai_candidate_explanation_runner as module
            return module.build_cli_command("claude", "test")
        if job_type == "ai:company_group_suggestion":
            from backend.app.worker import ai_company_group_suggestion_runner as module
            return module.build_company_group_cli_command("claude", "test")
        if job_type == "ai:company_normalization_suggestion":
            from backend.app.worker import (
                ai_company_normalization_suggestion_runner as module,
            )
            return module.build_company_normalization_cli_command("claude", "test")
        if job_type == "ai:keyword_expand":
            from backend.app.worker import ai_keyword_expand_runner as module
            return module.build_keyword_expand_cli_command("claude", "test")
        self.fail(f"沒有 {job_type} 的實際 argv 取樣器")

    def test_every_registered_job_uses_reviewed_actual_argv_tier(self):
        from backend.app.db.job_repository import AI_JOB_TYPES
        from backend.app.worker import cli_gateway as gw

        self._assert_policy_is_complete(AI_JOB_TYPES)
        for job_type in sorted(AI_JOB_TYPES):
            argv = self._actual_argv(job_type)
            expected = getattr(gw, self.EXPECTED_TIERS[job_type])
            names = list(expected) if not isinstance(expected, str) else [expected]
            actual = argv[argv.index("--allowedTools") + 1 :]
            with self.subTest(job_type=job_type):
                self.assertEqual(actual, names)

    def test_unreviewed_new_job_fails_the_guard(self):
        with self.assertRaises(AssertionError):
            self._assert_policy_is_complete({*self.EXPECTED_TIERS, "ai:unreviewed"})


class NarrativeReexportRetirementTests(unittest.TestCase):
    """一般 runner 不得再透過 narrative 取得 gateway 共用符號。"""

    RUNNERS = (
        "ai_company_zh_name_runner.py",
        "ai_irrelevant_filter_runner.py",
        "ai_patent_note_runner.py",
        "ai_topic_label_runner.py",
        "ai_candidate_explanation_runner.py",
    )

    def test_runners_import_gateway_directly(self):
        for name in self.RUNNERS:
            source = (_WORKER / name).read_text(encoding="utf-8")
            with self.subTest(runner=name):
                self.assertNotIn("from .ai_narrative_runner import", source)


class DataEmbeddedLinesUseNoToolsTests(unittest.TestCase):
    """兩條「prompt 資料內嵌」的 AI 線不得沿用敘述線的取證權限（2026-08-13）。

    ⚠ 這兩條原本從 `ai_narrative_runner` 借 `build_cli_command`——那是
    `partial(tools=RESEARCH_TOOLS)`，於是它們拿到 Read/Glob/Grep/**Write** ＋
    八支 MCP 取證工具＋`--mcp-config`，而它們的 prompt 完全是資料內嵌的
    （candidate_explanation 串候選指標、topic_backfill 串候選文本＋主題清單），
    一個工具都不需要。不報錯，只是靜默多權限。

    本類刻意**只守這兩條**，不擴成「全部 AI 線」的對照表：第四種權限等級
    （WebSearch／WebFetch）尚在另一條線定案中，現在定介面會定成兩份。
    """

    def _assert_no_tools(self, argv, where):
        """斷言 argv 是空白名單且沒掛 MCP config。"""
        from backend.app.worker import cli_gateway as gw

        self.assertIn("--allowedTools", argv, f"{where} 沒有工具白名單旗標")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], gw.NO_TOOLS,
                         f"{where} 的白名單被擴權了：{argv}")
        self.assertNotIn("--mcp-config", argv,
                         f"{where} 不需要取證工具，不該掛 MCP config")

    def test_candidate_explanation_uses_no_tools(self):
        """ai:candidate_explanation 實際跑的就是模組級 build_cli_command。"""
        from backend.app.worker import ai_candidate_explanation_runner as mod

        self._assert_no_tools(mod.build_cli_command("claude", "hi"),
                              "ai:candidate_explanation")

    def test_topic_backfill_uses_no_tools(self):
        """ai:topic_backfill 走 ai_bridge 組的那條 argv——驗**實際路徑**。

        以 fake 取代 run_topic_backfill 以攔下 bridge 傳入的 cli_runner，再呼叫它
        一次即可拿到真正送給 CLI 的 argv；DB fetcher 是 lambda，從頭到尾不被呼叫。
        ⚠ subprocess 執行器同時攔 `cli_gateway` 與 `ai_narrative_runner` 兩處綁定，
        argv 組裝日後搬家（closure → runner）也照樣攔得到。
        """
        from unittest import mock

        from backend.app.worker import ai_bridge
        from backend.app.worker import ai_topic_backfill_runner as backfill
        from backend.app.worker import cli_gateway as gw

        seen: list[list[str]] = []

        def fake_run_cli(argv, timeout):
            seen.append(list(argv))
            return gw.CliResult(exit_code=0, stdout='{"result": "{}"}', stderr="")

        captured: dict[str, Any] = {}

        def fake_run_topic_backfill(**kwargs):
            captured["cli"] = kwargs["cli_runner"]
            return {}

        class _Ctx:
            """只需要 heartbeat 的最小 JobContext 替身。"""

            def heartbeat(self, *args, **kwargs):
                return None

        with mock.patch.object(backfill, "run_topic_backfill", fake_run_topic_backfill), \
                mock.patch.object(gw, "run_cli", fake_run_cli):
            ai_bridge._run_ai_topic_backfill_job({"workspace_id": 1}, _Ctx())
            self.assertIn("cli", captured, "bridge 應傳入 cli_runner")
            captured["cli"]("PROMPT", timeout_seconds=1.0)

        self.assertEqual(len(seen), 1, f"應組出恰好一條 CLI argv：{seen}")
        self._assert_no_tools(seen[0], "ai:topic_backfill")


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

    def test_limit_not_capped_but_fused(self):
        """🔴 2026-08-12 使用者裁決契約更新：「列數上限取消（但要避免系統效能
        支援不了）」——原 2000 列硬上限移除（權限牆退場），效能保護改**回應量
        保險絲**（bytes）＋逾時，兩者都是保險絲不是牆：超過明示截斷，不擋查詢。

        沿革：原 test_limit_capped 守「不因換通道縮權」（500/2000 對齊舊閘道）；
        本次是使用者主動再放寬，同一精神的下一步。
        """
        from backend.app.mcp_server import report_research as rr

        self.assertEqual(rr.SQL_DEFAULT_ROWS, 500)   # 預設分頁不變
        self.assertFalse(hasattr(rr, "SQL_MAX_ROWS"), "硬上限應移除")
        self.assertGreaterEqual(rr.SQL_PAYLOAD_FUSE_BYTES, 1_000_000,
                                "保險絲小於 1MB 就變回牆了")

    def test_timeout_relaxed(self):
        """逾時放寬（2026-08-12 使用者裁決）：30s→120s，仍保留防拖垮 DB 的底。"""
        from backend.app.mcp_server import report_research as rr

        self.assertEqual(rr._SQL_TIMEOUT_MS, 120000)

    def test_payload_fuse_collects_and_truncates(self):
        """保險絲行為（純函式測，不連 DB）：容量內全收；超過即停並標 truncated。"""
        from backend.app.mcp_server import report_research as rr

        rows = [("x" * 100,) for _ in range(50)]
        it = iter(rows)

        def fetch(n):
            out = []
            for _ in range(n):
                try:
                    out.append(next(it))
                except StopIteration:
                    break
            return out

        got, truncated = rr._collect_rows(fetch, limit=None, fuse_bytes=10_000)
        self.assertEqual(len(got), 50)
        self.assertFalse(truncated)

        it = iter([("y" * 1000,) for _ in range(100)])
        got, truncated = rr._collect_rows(fetch, limit=None, fuse_bytes=5_000)
        self.assertTrue(truncated, "超過保險絲應標 truncated")
        self.assertLess(len(got), 100)
        self.assertGreater(len(got), 0, "保險絲不是擋門，已收的要回傳")

    def test_limit_still_paginates(self):
        """limit 仍是呼叫端的分頁工具：給了就收到 limit 為止並標 truncated。"""
        from backend.app.mcp_server import report_research as rr

        it = iter([(i,) for i in range(30)])

        def fetch(n):
            out = []
            for _ in range(n):
                try:
                    out.append(next(it))
                except StopIteration:
                    break
            return out

        got, truncated = rr._collect_rows(fetch, limit=10, fuse_bytes=10_000_000)
        self.assertEqual(len(got), 10)
        self.assertTrue(truncated)


if __name__ == "__main__":
    unittest.main()
