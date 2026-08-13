"""解讀階段也必須實際查證（2026-08-10 使用者要求延伸）。

使用者要求「順便監控有沒有去查證據來寫」。要監控這件事，真正的落點是
**narrative**——報表每一段解讀就是它產的，而它是唯一持有 `RESEARCH_TOOLS`
（MCP 唯讀取證）的 AI 線，有查證能力卻一度沒有任何紀錄。其餘 AI 線一律
`NO_TOOLS`／`READ_ONLY_TOOLS`，設計上就不查 DB，稽核不適用。

## 稽核工具的落點

`query_audit_file` 與 `read_query_audit` 定義在 `mcp_server.report_research`
（`AUDIT_PATH_ENV` 的定義處），由需要的呼叫端共用。
⚠ 複製第二份會讓稽核格式各自演進，而不一致本身不會報錯。

⚠ 2026-08-13：原本本檔還守著另一條走 MCP 取證的規劃線不留稽核副本；
該線已隨 PPT 交付線整條移除（2026-08-10），對應測試同步退場（見下方註解）。
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

    # ⚠ 2026-08-13 移除 test_planning_runner_uses_shared_tools：它讀的那支規劃線
    # runner 已隨 PPT 交付線整條移除（2026-08-10），檔案不存在故整支恆紅。
    # 第二份副本的風險隨該線消失，「只有一個定義處」由本類其餘測試與 narrative 那組續守。


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
