"""匯出報告頁的 deck 面（add-deck-delivery-line tasks 3.3 前端面）。

design §6 定案：匯出報告頁＝**交付物中心**——產製簡報按鈕、deck 紀錄、
逐頁預覽（先看到）、下載 pptx（再下載，使用者主動按）。
前端是字串模板單檔，比照 `test_export_page_cleanup.py` 以結構字串斷言驗接線
——驗的是「接了哪條 API、掛了哪個刷新目標」，不是渲染結果。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "backend" / "app" / "static" / "index.html"


class ExportPageDeckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_placeholder_removed(self):
        """「簡報產製功能規劃中」的空殼要被真的功能取代。"""
        self.assertNotIn("簡報產製功能規劃中", self.html)

    def test_create_job_posts_ai_report_deck(self):
        """產製按鈕走既有 POST /ai-tasks（不另造第二條建 job 的路）。"""
        self.assertIn("task_type: 'ai:report_deck'", self.html)
        # 建 job 要帶版本；版本與報表種類頁同源（reports/versions）
        self.assertIn("deck-version-select", self.html)

    def test_deck_records_from_api(self):
        self.assertIn("/deck-exports", self.html)
        self.assertIn("deck-records", self.html)

    def test_sse_refresh_target_registered(self):
        """ai:report_deck 完成→匯出頁自動刷新（design §6 明訂落點改頁後跟著改）。"""
        m = re.search(r"'ai:report_deck':\s*\[([^\]]*)\]", self.html)
        self.assertIsNotNone(m, "JOB_REFRESH_TARGETS 缺 ai:report_deck")
        self.assertIn("deckExports", m.group(1))
        m2 = re.search(r"'deckExports':\s*\{\s*navs:\s*\[([^\]]*)\]", self.html)
        self.assertIsNotNone(m2, "RESOURCE_REFRESHERS 缺 deckExports")
        self.assertIn("'export'", m2.group(1))

    def test_preview_then_download(self):
        """先看到（逐頁 img）、再下載（使用者主動按），不自動下載。"""
        self.assertIn("page_urls", self.html)
        self.assertIn("pptx_url", self.html)
        self.assertIn("下載 pptx", self.html)


if __name__ == "__main__":
    unittest.main()
