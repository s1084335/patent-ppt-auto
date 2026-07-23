"""報表預覽內容端點契約測試（不需 DB，只讀檔案系統產出）。

匯出報告工作台需要「結構化的報表內容 + 解讀」才能做預覽與編輯；既有
GET /api/v1/report-latest 只回整頁 index.html，前端無法逐段編輯，故補一支
GET /api/v1/report-latest/content 回結構化 JSON，以及 asset 端點供圖檔。

一律以 tmp 目錄當假的 output root（monkeypatch 端點的 root 解析），不碰正式產出。
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.main import app


client = TestClient(app)


def _write_run(root: Path, version: str, *, narratives: bool = True) -> Path:
    """建一個最小但完整的報表輸出目錄（report_data.json + svg + narratives.json）。"""
    run_dir = root / version
    run_dir.mkdir(parents=True)
    report_data = {
        "parameters": {
            "generated_at": "2026-07-22T16:00:00",
            "version": version,
            "analysis_id": 7,
            "scope": "patent_ids_snapshot",
            "patent_ids_count": 525,
            "ranking_limit": 100,
        },
        "reports": {
            "annual_trend": {
                "report_name": "annual_trend",
                "label_zh": "專利申請趨勢",
                "row_count": 2,
                "rows": [{"year": 2024, "patent_count": 3}, {"year": 2025, "patent_count": 5}],
            }
        },
        "family_reports": {},
        "chart_rows": {},
        "sections": [
            {
                "title": "專利申請趨勢",
                "variants": [{"label": "Trend", "file": "annual_trend.svg", "variant_key": "default"}],
                "note": "以 application_date 計數。",
            },
            {
                "title": "無圖卡片",
                "report_key": "annual_trend",
                "variants": [{"label": "Bar", "file": "missing.svg", "variant_key": "alt"}],
            },
        ],
    }
    (run_dir / "report_data.json").write_text(
        json.dumps(report_data, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "annual_trend.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>',
        encoding="utf-8",
    )
    if narratives:
        (run_dir / "narratives.json").write_text(
            json.dumps(
                {
                    "based_on_version": version,
                    "reports": {
                        "annual_trend": {
                            "variants": {
                                "default": {
                                    "text": "近兩年申請量由 3 件增至 5 件。",
                                    "ai_model": "claude-opus-4-8",
                                    "prompt_version": "report_narrative_v2",
                                    "generated_at": "2026-07-22T16:30:00+08:00",
                                }
                            }
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return run_dir


class ReportContentEndpointTests(unittest.TestCase):
    """GET /report-latest/content 回結構化報表內容（供前端預覽/編輯）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="report_content_test_"))
        cls.run_dir = _write_run(cls.tmp, "report_trial_20260722_160000")
        cls._orig_root = main_module.REPORT_OUTPUT_ROOT
        main_module.REPORT_OUTPUT_ROOT = cls.tmp

    @classmethod
    def tearDownClass(cls):
        main_module.REPORT_OUTPUT_ROOT = cls._orig_root
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_content_returns_version_and_cover_metadata(self):
        """封面資訊：版本、產生時間、專利件數、範圍（缺就給空字串，不猜）。"""
        resp = client.get("/api/v1/report-latest/content")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["version"], "report_trial_20260722_160000")
        self.assertEqual(body["generated_at"], "2026-07-22T16:00:00")
        self.assertEqual(body["patent_count"], 525)
        self.assertEqual(body["scope"], "patent_ids_snapshot")

    def test_content_sections_carry_rows_narrative_and_chart_url(self):
        """每張卡片一次帶齊：數據 rows、圖 URL、AI 解讀（不用前端逐張再打 API）。"""
        body = client.get("/api/v1/report-latest/content").json()
        sections = body["sections"]
        self.assertEqual(len(sections), 2)
        first = sections[0]
        self.assertEqual(first["report_key"], "annual_trend")
        self.assertEqual(first["title"], "專利申請趨勢")
        self.assertEqual(first["row_count"], 2)
        self.assertEqual(first["rows"][0]["year"], 2024)
        variant = first["variants"][0]
        self.assertEqual(variant["variant_key"], "default")
        self.assertIn("annual_trend.svg", variant["chart_url"])
        self.assertIn("近兩年申請量", variant["narrative"]["text"])
        self.assertEqual(variant["narrative"]["ai_model"], "claude-opus-4-8")

    def test_missing_narrative_and_chart_are_explicit_not_guessed(self):
        """缺解讀→narrative 為 None；圖檔不存在→chart_url 為 None（不假造路徑）。"""
        body = client.get("/api/v1/report-latest/content").json()
        variant = body["sections"][1]["variants"][0]
        self.assertIsNone(variant["narrative"])
        self.assertIsNone(variant["chart_url"])

    def test_asset_endpoint_serves_chart_file(self):
        """asset 端點回傳該版本目錄下的圖檔。"""
        version = "report_trial_20260722_160000"
        resp = client.get(f"/api/v1/report-latest/asset/{version}/annual_trend.svg")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("<svg", resp.text)

    def test_asset_endpoint_rejects_path_traversal(self):
        """asset 端點不得被 ../ 逃出報表輸出根。"""
        version = "report_trial_20260722_160000"
        resp = client.get(f"/api/v1/report-latest/asset/{version}/..%2F..%2Freport_data.json")
        self.assertIn(resp.status_code, (400, 404))

    def test_asset_endpoint_missing_file_404(self):
        """不存在的檔案回 404。"""
        resp = client.get("/api/v1/report-latest/asset/report_trial_20260722_160000/nope.svg")
        self.assertEqual(resp.status_code, 404)


class ReportContentNoOutputTests(unittest.TestCase):
    """無任何報表產出時，端點回 404 並說明原因，不 500。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="report_content_empty_"))
        cls._orig_root = main_module.REPORT_OUTPUT_ROOT
        main_module.REPORT_OUTPUT_ROOT = cls.tmp

    @classmethod
    def tearDownClass(cls):
        main_module.REPORT_OUTPUT_ROOT = cls._orig_root
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_content_404_when_no_run(self):
        resp = client.get("/api/v1/report-latest/content")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
