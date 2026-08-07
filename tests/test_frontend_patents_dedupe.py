"""前端去重：專利總覽刪除、兩個維護面板並存（2026-08-07 使用者定案）。

使用者指正（部署後實測）：
1. 「資料維護」與「TW 專利狀態管理」兩個面板**應該一起出現**，不管在全庫
   還是特定 workspace——原本一個在瀏覽專利、一個在專利總覽，拆兩處。
2. 「專利總覽」與「全庫」功能重複：瀏覽專利選全庫本來就是跨 workspace
   全庫清單（含所屬 Workspace 欄），總覽只是同一件事的第二個入口。
   依「同一概念不得兩處落點」刪總覽，選全庫直接進瀏覽專利。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "backend" / "app" / "static"
        / "index.html").read_text(encoding="utf-8")


def _render_browse_body() -> str:
    start = HTML.index("async function renderBrowse")
    end = HTML.index("async function", start + 10)
    return HTML[start:end]


class OverviewRemovedTests(unittest.TestCase):
    def test_render_patents_gone(self):
        """總覽渲染器與其專用載入函式一併刪除，不留死碼。"""
        self.assertNotIn("function renderPatents", HTML)
        self.assertNotIn("function loadAllPatents", HTML)
        self.assertNotIn("case 'patents'", HTML)

    def test_no_nav_references_patents(self):
        """所有導覽落點改指 browse——殘留 'patents' nav 會落到未知頁面。"""
        self.assertNotIn("navTo('patents')", HTML)
        self.assertNotIn("state.nav = 'patents'", HTML)
        self.assertNotIn("nav: 'patents'", HTML)


class PanelsTogetherTests(unittest.TestCase):
    def test_browse_has_both_maintenance_panels(self):
        body = _render_browse_body()
        self.assertIn('id="browse-maintenance"', body)
        self.assertIn('id="tw-legal-status-panel"', body)

    def test_tw_panel_not_duplicated(self):
        """面板只有一個落點（在瀏覽專利）。"""
        self.assertEqual(HTML.count('id="tw-legal-status-panel"'), 1)


if __name__ == "__main__":
    unittest.main()
