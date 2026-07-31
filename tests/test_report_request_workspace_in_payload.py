"""report_generate 的 workspace_id 必須進 payload（2026-07-31 實機 #145）。

## 問題

前端送了 `workspace_id`、API model 也宣告了該欄，但 job payload 裡沒有——
worker 端 `payload.get("workspace_id")` 恆為 None，於是：
- 分群兩份報表產不出來（cluster_data 取不到）
- `version_meta.json` 沒有歸屬鍵 → 前端帶 workspace 過濾後**整個清單空白**
  （使用者：「前端沒顯示阿，report_generate #145 都成功了」）

## 根因

`reports.py` 把 `workspace_id` 只當成 `create_job()` 的**獨立參數**
（寫進 `workflow_runs.workspace_id` 欄），**沒有併進 payload**。
handler 讀的是 payload——欄位在，只是不在消費端要的位置。

⚠ 本專案**第三次**同型錯誤：
1. 前端送 `aliases`、後端欄位叫 `variants`（9275d91）
2. `report_keys` 未宣告被 Pydantic 靜默丟棄（d9b20aa）
3. 本次：宣告了、也收到了，但沒進 payload

→ 契約測試一律驗**消費端實際拿到什麼**，不驗「有沒有這個欄位」。
"""
from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.main import app


class WorkspaceIdInPayloadTests(unittest.TestCase):
    def _post(self, body):
        captured = {}

        def fake_create_job(job_type, payload, **kwargs):
            captured["job_type"] = job_type
            captured["payload"] = payload
            captured["kwargs"] = kwargs

            # 欄位對齊 jobs.job_to_dict 讀的那些（少一個就 AttributeError）。
            class _Job:
                job_id = 1
                job_type = "report_generate"
                status = "queued"
                workspace_id = kwargs.get("workspace_id")
                payload_json = payload
                result_json = None
                progress_percent = 0
                current_stage = None
                attempt_count = 0
                max_attempts = 3
                error_message = None
            return _Job()

        with mock.patch("backend.app.api.reports.job_repository.create_job", fake_create_job):
            resp = TestClient(app).post("/api/v1/reports", json=body)
        return resp, captured

    def test_payload_carries_workspace_id(self):
        """🔴 handler 讀 payload.get('workspace_id')——不在 payload 就等於沒送。"""
        resp, captured = self._post({"report_names": ["application_trend"], "workspace_id": 1})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(captured["payload"].get("workspace_id"), 1,
                         "workspace_id 沒進 payload——分群報表與版本歸屬都會失效")

    def test_job_column_still_set(self):
        """⚠ 同時仍要寫進 job 的 workspace_id 欄（既有行為，不得回歸）。"""
        _, captured = self._post({"report_names": ["application_trend"], "workspace_id": 1})
        self.assertEqual(captured["kwargs"].get("workspace_id"), 1)

    def test_absent_when_not_given(self):
        """全庫以外情境未給時不落鍵（維持 None＝無分群範圍的既有語意）。"""
        _, captured = self._post({"report_names": ["application_trend"]})
        self.assertNotIn("workspace_id", captured["payload"])


if __name__ == "__main__":
    unittest.main()
