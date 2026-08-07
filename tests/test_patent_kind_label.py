"""設計案標籤（P1 五之三，2026-08-05 定案；2026-08-07 動工）。

設計案不進分群但要有標籤：判定唯一入口＝transforms/patent_kind（document_kind
優先，不可用 patent_type 判設計）；種類用既有兩欄組合推導，不新增 DB 欄。
落點：兩個專利清單 API 帶 patent_kind_display（發明/新型/設計/未標示），
前端專利表顯示「專利種類」欄——11 件設計案的技術/功效分類空白因此可被
讀者理解為「設計案本來就不分」，不是漏分。
"""
from __future__ import annotations

import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "backend" / "app" / "static"
        / "index.html").read_text(encoding="utf-8")


class ListApiKindTests(unittest.TestCase):
    def test_global_list_attaches_kind_display(self):
        import inspect

        from backend.app.app_layer import patent_queries as q

        src = inspect.getsource(q.list_patents)
        self.assertIn("patent_kind_display", src)
        self.assertIn("patent_kind", src)

    def test_workspace_list_attaches_kind_display(self):
        import inspect

        from backend.app.app_layer import workspace_queries as wq

        src = inspect.getsource(wq.list_workspace_patents)
        self.assertIn("patent_kind_display", src)

    def test_kind_uses_single_definition(self):
        """推導一律走 transforms/patent_kind.patent_kind，不得另寫組合條件。"""
        from backend.app.transforms.patent_kind import patent_kind

        self.assertEqual(patent_kind({"document_kind": "S", "patent_type": "P"}), "設計")
        self.assertEqual(patent_kind({"document_kind": "A", "patent_type": "P"}), "發明")
        self.assertEqual(patent_kind({"document_kind": "U", "patent_type": "U"}), "新型")
        self.assertEqual(patent_kind({}), "未標示")


class FrontendKindColumnTests(unittest.TestCase):
    def test_patent_table_has_kind_column(self):
        self.assertIn("'patent_kind_display'", HTML)
        self.assertIn("專利種類", HTML)


if __name__ == "__main__":
    unittest.main()
