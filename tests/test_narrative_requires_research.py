"""解讀階段也必須實際查證（2026-08-10 使用者要求延伸）。

使用者要求「PPT 時順便監控有沒有去查證據來寫」。查證後釐清三個階段的權限：

| 階段 | 工具 | 能查 DB | 稽核 |
|---|---|---|---|
| `ai:narrative`（產每頁要點） | `RESEARCH_TOOLS`（MCP 唯讀） | ✅ | ❌ **本次補** |
| `ai:report_plan`（產 SlidePlan） | report-research MCP | ✅ | ✅ 已有（`31f8e81`） |
| `ai:report_ppt`（產文案 slots） | 只有 `Read` | ❌ 設計上不查 | 不適用 |

⚠ `ai:report_ppt` 不能查是**刻意**的：它的文案來自已經查證過的 `narratives.json`，
白名單維持最小（`READ_ONLY_TOOLS`）。要監控「有沒有查證據來寫」，真正的落點是
**narrative**——簡報上每一頁的要點就是它產的，而它有查證能力卻沒有任何紀錄。

## 稽核工具的落點

`query_audit_file` 與 `read_query_audit` 從 `report_planning_runner` 移到
`mcp_server.report_research`（`AUDIT_PATH_ENV` 的定義處），兩個 runner 共用。
⚠ 複製第二份會讓兩條線的稽核格式各自演進，而不一致本身不會報錯。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.mcp_server.report_research import query_audit_file, read_query_audit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NARRATIVE_RUNNER = PROJECT_ROOT / "backend" / "app" / "worker" / "ai_narrative_runner.py"


class SharedAuditToolsTests(unittest.TestCase):
    """稽核開檔與讀回是同一份知識，只能有一個定義處。"""

    def test_audit_file_sets_env_and_cleans_up(self):
        """context manager 進場設環境變數、離場還原並刪檔。"""
        import os

        from backend.app.mcp_server.report_research import AUDIT_PATH_ENV

        before = os.environ.get(AUDIT_PATH_ENV)
        with query_audit_file() as path:
            self.assertEqual(os.environ.get(AUDIT_PATH_ENV), str(path))
            path.write_text('{"tool": "query_database", "status": "ok"}\n', encoding="utf-8")
            self.assertEqual(len(read_query_audit(path)), 1)
        self.assertEqual(os.environ.get(AUDIT_PATH_ENV), before, "離場要還原環境變數")
        self.assertFalse(path.exists(), "離場要刪除稽核暫存檔")

    def test_planning_runner_uses_shared_tools(self):
        """規劃線改用共用實作，不留第二份。"""
        source = (PROJECT_ROOT / "backend" / "app" / "worker"
                  / "report_planning_runner.py").read_text(encoding="utf-8")
        self.assertIn("query_audit_file", source)
        self.assertNotIn("def _query_audit_file", source,
                         "私有副本必須移除——稽核格式只能有一個定義處")


class NarrativeResearchTests(unittest.TestCase):
    """解讀必須留下查證紀錄。"""

    def test_narrative_runner_opens_audit(self):
        """解讀執行時要開稽核檔，否則無從得知它有沒有查。"""
        source = NARRATIVE_RUNNER.read_text(encoding="utf-8")
        self.assertIn("query_audit_file", source,
                      "ai:narrative 有 RESEARCH_TOOLS 卻沒有稽核，等於可以不查就寫")

    def test_narrative_reports_query_audit(self):
        """稽核結果要進 job 回傳，才看得到「這次查了幾次」。"""
        source = NARRATIVE_RUNNER.read_text(encoding="utf-8")
        self.assertIn('"query_audit"', source,
                      "解讀結果需帶 query_audit，供監控與驗收檢視")


if __name__ == "__main__":
    unittest.main()
