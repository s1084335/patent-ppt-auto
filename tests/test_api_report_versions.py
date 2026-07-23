"""報表版本列表／指定版本內容端點契約測試（不需 DB，只讀檔案系統產出）。

使用者需求：進「報表種類」頁要立刻看到上次產的報表，舊版本保留但可收合展開。
既有端點只有「最新」一支，故補：
  GET /api/v1/reports/versions                  列出所有版本（輕量 metadata）
  GET /api/v1/reports/versions/{version}/content 指定版本結構化內容（形狀同 report-latest/content）

一律以 tmp 目錄當假的 output root（monkeypatch REPORT_OUTPUT_ROOT），不碰正式產出。
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

from tests.test_api_report_content import _write_run


client = TestClient(app)


class ReportVersionListTests(unittest.TestCase):
    """GET /reports/versions：列出所有版本，最新在前。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="report_versions_test_"))
        # 三個有效版本（含不同命名批次，驗證不綁死單一前綴）＋一個無效目錄。
        _write_run(cls.tmp, "report_trial_20260721_224641", narratives=False)
        _write_run(cls.tmp, "report_trial_20260722_001036")
        _write_run(cls.tmp, "analysis_1_20260723_090000", narratives=False)
        (cls.tmp / "not_a_run").mkdir()  # 無 report_data.json，不算版本
        cls._orig_root = main_module.REPORT_OUTPUT_ROOT
        main_module.REPORT_OUTPUT_ROOT = cls.tmp

    @classmethod
    def tearDownClass(cls):
        main_module.REPORT_OUTPUT_ROOT = cls._orig_root
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_lists_all_valid_versions_newest_first(self):
        """只列含 report_data.json 的目錄；依版本名（＝時間序）新到舊排。"""
        resp = client.get("/api/v1/reports/versions")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        names = [v["version"] for v in body["versions"]]
        self.assertEqual(
            names,
            [
                "report_trial_20260722_001036",
                "report_trial_20260721_224641",
                "analysis_1_20260723_090000",
            ],
        )
        self.assertEqual(body["total"], 3)
        self.assertNotIn("not_a_run", names)

    def test_version_entry_carries_display_metadata(self):
        """每筆帶可顯示的 metadata：產生時間、是否為最新、是否有解讀。"""
        body = client.get("/api/v1/reports/versions").json()
        latest = body["versions"][0]
        self.assertEqual(latest["version"], "report_trial_20260722_001036")
        self.assertTrue(latest["is_latest"])
        self.assertTrue(latest["has_narratives"])
        # 產生時間由目錄名時間戳解析（不必開 report_data.json 也拿得到）
        self.assertEqual(latest["generated_at"], "2026-07-22T00:10:36")
        self.assertFalse(body["versions"][1]["is_latest"])
        self.assertFalse(body["versions"][1]["has_narratives"])

    def test_listing_does_not_read_report_data_json(self):
        """效率契約：列版本不得把每個版本的 report_data.json 全載進來。

        以「把 report_data.json 內容換成不可解析的垃圾仍可正常列版本」驗證：
        端點若讀了該檔就會壞掉。
        """
        broken = self.tmp / "report_trial_20260722_001036" / "report_data.json"
        original = broken.read_text(encoding="utf-8")
        broken.write_text("{ NOT VALID JSON", encoding="utf-8")
        try:
            resp = client.get("/api/v1/reports/versions")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(resp.json()["versions"]), 3)
        finally:
            broken.write_text(original, encoding="utf-8")

    def test_limit_param_returns_recent_subset_with_total(self):
        """支援 limit 只取最近 N 個，但 total 仍回實際總數（供前端「顯示更多」）。"""
        body = client.get("/api/v1/reports/versions?limit=2").json()
        self.assertEqual(len(body["versions"]), 2)
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["versions"][0]["version"], "report_trial_20260722_001036")


class ReportVersionContentTests(unittest.TestCase):
    """GET /reports/versions/{version}/content：指定版本內容，形狀同 report-latest/content。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="report_version_content_"))
        _write_run(cls.tmp, "report_trial_20260721_224641", narratives=False)
        _write_run(cls.tmp, "report_trial_20260722_001036")
        cls._orig_root = main_module.REPORT_OUTPUT_ROOT
        main_module.REPORT_OUTPUT_ROOT = cls.tmp

    @classmethod
    def tearDownClass(cls):
        main_module.REPORT_OUTPUT_ROOT = cls._orig_root
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_old_version_content_same_shape_as_latest(self):
        """舊版本內容與 /report-latest/content 同形狀，前端可用同一套渲染。"""
        old = "report_trial_20260721_224641"
        resp = client.get(f"/api/v1/reports/versions/{old}/content")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        latest_body = client.get("/api/v1/report-latest/content").json()
        self.assertEqual(sorted(body.keys()), sorted(latest_body.keys()))
        self.assertEqual(body["version"], old)
        self.assertEqual(body["patent_count"], 525)
        self.assertEqual(len(body["sections"]), 2)
        self.assertEqual(body["sections"][0]["report_key"], "annual_trend")

    def test_chart_urls_point_at_requested_version(self):
        """圖 URL 走既有 asset 端點且帶該版本，不會指到最新版本的圖。"""
        old = "report_trial_20260721_224641"
        body = client.get(f"/api/v1/reports/versions/{old}/content").json()
        chart_url = body["sections"][0]["variants"][0]["chart_url"]
        self.assertIn(f"/report-latest/asset/{old}/", chart_url)
        # 該 URL 真的取得到圖（asset 端點已支援指定版本）
        self.assertEqual(client.get(chart_url).status_code, 200)

    def test_unknown_version_returns_404(self):
        """版本不存在回 404 並說明原因，不 500。"""
        resp = client.get("/api/v1/reports/versions/report_trial_不存在/content")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("detail", resp.json())

    def test_rejects_path_traversal_version(self):
        """版本參數不得逃出報表輸出根（沿用 asset 端點的防護做法）。

        契約＝「絕不回出報表內容」：越界一律非 200，且不得吐出報表 payload。
        （純 ".." 會被 HTTP client 正規化成 /reports/content 而打到別的路由，
        本身就到不了此端點，故不列入——真正會傳到端點的是編碼過的變體。）
        """
        for bad in ("..%2F..", "%2e%2e%2f%2e%2e", "..%2F..%2Foutput", "..%5C..", "....//"):
            with self.subTest(bad=bad):
                resp = client.get(f"/api/v1/reports/versions/{bad}/content")
                self.assertNotEqual(resp.status_code, 200)
                self.assertNotIn("sections", resp.json())

    def test_resolve_run_dir_rejects_escaping_root(self):
        """防護的單元層契約：解析函式對越界／根目錄本身一律回 None。"""
        for bad in ("..", "../..", "../../output", "", "."):
            with self.subTest(bad=bad):
                self.assertIsNone(main_module._resolve_run_dir(bad))

    def test_directory_without_report_data_is_404(self):
        """目錄存在但非有效報表版本（無 report_data.json）→ 404。"""
        (self.tmp / "empty_dir").mkdir(exist_ok=True)
        resp = client.get("/api/v1/reports/versions/empty_dir/content")
        self.assertEqual(resp.status_code, 404)


class ReportVersionsEmptyTests(unittest.TestCase):
    """無任何產出時列表回空陣列（200，不是 404），前端才好顯示提示。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="report_versions_empty_"))
        cls._orig_root = main_module.REPORT_OUTPUT_ROOT
        main_module.REPORT_OUTPUT_ROOT = cls.tmp

    @classmethod
    def tearDownClass(cls):
        main_module.REPORT_OUTPUT_ROOT = cls._orig_root
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_empty_listing_is_200_with_empty_versions(self):
        resp = client.get("/api/v1/reports/versions")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"versions": [], "total": 0})


if __name__ == "__main__":
    unittest.main()
