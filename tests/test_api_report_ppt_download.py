"""報告 PPT 下載路由契約測試（deterministic、Web 直呼、不經 AI）。

匯出報告線第二塊：ai:report_ppt runner 把 .pptx 存進 report_artifacts；前端「輸出 PPT」
完成後打本路由下載。紅線：

1. deterministic、Web 直呼（不經 AI）——直接 read_file(version, filename) 回 bytes。
2. content-type 為 pptx 的 openxml MIME。
3. 不存在回 404（不 500）。
4. 只接 .pptx（白名單），版本名防 path traversal（沿既有 _is_safe_version）。

沿既有 report_artifact_store 讀取（不自造新表／新存取），以 mock store 驗證，不需真 DB
（同 test_report_artifact_store.CrossContainerReadTests 的替身做法）。
"""
from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.main import app


client = TestClient(app)

_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_VERSION = "report_trial_20260724_150000"
_PPTX_NAME = _VERSION + ".pptx"
_PPTX_BYTES = b"PK\x03\x04fake-pptx-content"


def _patched_read_file():
    """替身：只有 DB 有這一版的 .pptx（本機檔案系統沒有）。"""

    def _read_file(version, filename):
        if version == _VERSION and filename == _PPTX_NAME:
            return _PPTX_BYTES
        return None

    return mock.patch.object(main_module.report_artifact_store, "read_file", _read_file)


class ReportPptDownloadTests(unittest.TestCase):
    """GET .pptx 下載路由：deterministic、正確 MIME、404、白名單、防穿越。"""

    def test_download_returns_pptx_bytes_with_correct_mime(self):
        """存在的 .pptx → 200、原始 bytes、pptx openxml content-type。"""
        with _patched_read_file():
            resp = client.get(f"/api/v1/report-latest/ppt/{_VERSION}/{_PPTX_NAME}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, _PPTX_BYTES)
        self.assertEqual(resp.headers.get("content-type"), _PPTX_MIME)

    def test_download_missing_returns_404(self):
        """DB 沒有的版本／檔名回 404（不 500）。"""
        with _patched_read_file():
            resp = client.get(
                f"/api/v1/report-latest/ppt/report_trial_不存在/{_PPTX_NAME}")
        self.assertEqual(resp.status_code, 404)

    def test_download_rejects_non_pptx_extension(self):
        """只接 .pptx：其他副檔名一律 404（白名單）。"""
        with _patched_read_file():
            resp = client.get(
                f"/api/v1/report-latest/ppt/{_VERSION}/{_VERSION}.svg")
        self.assertEqual(resp.status_code, 404)

    def test_download_rejects_path_traversal_version(self):
        """版本名帶路徑分隔／.. 一律擋（沿 _is_safe_version）。"""
        with _patched_read_file():
            resp = client.get(
                f"/api/v1/report-latest/ppt/..%2F..%2Fetc/{_PPTX_NAME}")
        self.assertIn(resp.status_code, (404, 400))


if __name__ == "__main__":
    unittest.main()
