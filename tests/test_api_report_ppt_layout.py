"""PPT 匯出版型端點契約測試。

本檔只測停止點 2：前端預覽需要的 theme geometry 與 PAGE_LAYOUT 從後端端點取得。
重點是實際打 `/reports/ppt-layout`，避免被 `/reports/{job_id}` 路由誤吃成 int。
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.reports.report_definitions import REPORT_DEFINITIONS


client = TestClient(app)


class ReportPptLayoutApiTests(unittest.TestCase):
    """驗證 PPT 版型 API 是可被前端直接使用的單一來源。"""

    def test_ppt_layout_route_returns_200_instead_of_job_id_422(self):
        """路由必須實際回 200；若排在 `/reports/{job_id}` 後面會變 422。"""
        resp = client.get("/api/v1/reports/ppt-layout")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["theme"]["slide"]["width_in"], 13.333)
        self.assertEqual(body["theme"]["slide"]["height_in"], 7.5)
        self.assertIn("geometry", body["theme"])

    def test_ppt_layout_pages_cover_template_outline_and_all_active_reports(self):
        """端點輸出 10 頁投影片與版型 kind，供前端唯讀縮圖使用。"""
        body = client.get("/api/v1/reports/ppt-layout").json()

        pages = body["pages"]
        covered_report_keys = {
            report_key
            for page in pages
            for report_key in page["report_keys"]
        }
        self.assertGreater(len(pages), 10)
        self.assertEqual([page["page"] for page in pages], list(range(1, len(pages) + 1)))
        self.assertEqual(pages[0]["kind"], "cover")
        self.assertEqual(pages[-1]["kind"], "narrative_only")
        self.assertTrue(any(page["source"] == "report_definition" for page in pages[9:-1]))
        self.assertTrue(set(REPORT_DEFINITIONS).issubset(covered_report_keys))
        self.assertIn("chart_with_narrative", body["kinds"])
        self.assertEqual(len(body["kinds"]), len(set(body["kinds"])))

    def test_page_payload_keeps_report_keys_charts_and_slots_separate(self):
        """頁面描述保留報表資料、圖檔與文案 slot 三種欄位，不混成一個自由文字。"""
        pages = client.get("/api/v1/reports/ppt-layout").json()["pages"]

        trend = pages[2]
        self.assertEqual(trend["page"], 3)
        self.assertEqual(trend["report_keys"], ["application_trend", "publication_trend"])
        self.assertEqual(trend["charts"], ["annual_trend.svg", "application_growth.svg"])
        self.assertEqual(trend["slots"], ["trend.narrative"])
