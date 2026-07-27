"""不相干篩選複核 API 契約（2026-07-27 定案）：手動觸發 ＋ 列待複核 ＋ 保留／確定。

補的斷鏈：`ai:irrelevant_filter` 原僅由 finalize 自動排程（已撤回），API 層完全沒有入口；
判讀結果也只留在 job 的 workflow_outputs，前端無從逐筆裁決。本組端點是使用者的操作入口。

用 mock 驗端點契約（呼叫對的引擎函式、帶對的參數、回傳形狀），不碰 DB。

端點：
- POST /workspaces/{id}/irrelevant-filter      手動觸發（建 ai:irrelevant_filter job）
- GET  /workspaces/{id}/exclusion-reviews      列待複核（pending）
- POST /workspaces/{id}/exclusion-reviews/keep     保留（刪列，留在原主題）
- POST /workspaces/{id}/exclusion-reviews/confirm  確定（→ excluded，歸到不相干）
"""
from __future__ import annotations

import unittest
import warnings
from unittest import mock

from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=StarletteDeprecationWarning,
    module=r"fastapi\.testclient",
)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.api import workspaces as workspaces_api  # noqa: E402
from backend.app.main import app  # noqa: E402


client = TestClient(app)
WS = 940302


class IrrelevantFilterTriggerTests(unittest.TestCase):
    """POST /workspaces/{id}/irrelevant-filter：手動觸發 AI 判讀。"""

    def test_trigger_creates_job(self):
        """建立 ai:irrelevant_filter job，payload 帶 workspace_id，回 job_id。"""
        fake = mock.MagicMock(return_value=4242)
        with mock.patch.object(workspaces_api, "is_global_workspace", return_value=False), \
                mock.patch.object(workspaces_api, "create_job", fake):
            resp = client.post(f"/api/v1/workspaces/{WS}/irrelevant-filter")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"workspace_id": WS, "job_id": 4242})
        args, kwargs = fake.call_args
        self.assertEqual(args[0], "ai:irrelevant_filter")
        self.assertEqual(args[1]["workspace_id"], WS)
        self.assertEqual(kwargs["workspace_id"], WS)


class GlobalWorkspaceGuardTests(unittest.TestCase):
    """全庫不做不相干篩選（2026-07-27 使用者重申；沿 spec 第 62-64 行既有定案）。

    排除是 **workspace 級**：對 A 不相干的專利，對全庫可能屬於另一技術領域、是相干的。
    全庫的 analysis_member_patent_ids 本就不扣除任何專利，對它跑篩選毫無意義——
    判讀結果只會堆在待複核清單佔位、白燒 CLI 額度。

    前端已隱藏入口（isGlobalSelected 護欄），後端仍須擋——前端擋不住直接打 API。
    """

    def test_trigger_rejects_global_workspace(self):
        """對全庫觸發篩選 → 400，且不得建 job（不白燒 CLI 額度）。"""
        fake_create = mock.MagicMock()
        with mock.patch.object(workspaces_api, "is_global_workspace", return_value=True), \
                mock.patch.object(workspaces_api, "create_job", fake_create):
            resp = client.post(f"/api/v1/workspaces/{WS}/irrelevant-filter")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("全庫", resp.json()["detail"])
        fake_create.assert_not_called()

    def test_trigger_allows_normal_workspace(self):
        """一般 workspace 照常放行。"""
        fake_create = mock.MagicMock(return_value=99)
        with mock.patch.object(workspaces_api, "is_global_workspace", return_value=False), \
                mock.patch.object(workspaces_api, "create_job", fake_create):
            resp = client.post(f"/api/v1/workspaces/{WS}/irrelevant-filter")
        self.assertEqual(resp.status_code, 200)
        fake_create.assert_called_once()


class ExclusionReviewListTests(unittest.TestCase):
    """GET /workspaces/{id}/exclusion-reviews：列待複核清單。"""

    def test_list_returns_pending_items(self):
        """回 pending_reviews 的結果，欄位原樣帶出供前端逐筆呈現。"""
        rows = [
            {"patent_id": 11, "ai_verdict": "irrelevant", "reason": "與主題無關",
             "reviewed_at": None},
            {"patent_id": 12, "ai_verdict": "irrelevant", "reason": "屬其他領域",
             "reviewed_at": None},
        ]
        with mock.patch.object(workspaces_api, "pending_reviews", return_value=rows):
            resp = client.get(f"/api/v1/workspaces/{WS}/exclusion-reviews")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["workspace_id"], WS)
        self.assertEqual([item["patent_id"] for item in body["items"]], [11, 12])
        self.assertEqual(body["items"][0]["reason"], "與主題無關")


class ExcludedPatentListTests(unittest.TestCase):
    """GET /workspaces/{id}/excluded-patents：列「不相干」桶內容。

    人工剔除與 AI 判讀確定者都在此（使用者定案：兩種來源最終都出現在不相干標籤）。
    原「未分類」「其他」兩個空系統桶已移除，此桶是排除清單的檢視入口。
    """

    def test_list_returns_excluded_patents(self):
        """回已確定排除的專利（status='excluded'），帶 source 供區分人工／AI。"""
        rows = [
            {"patent_id": 61, "source": "manual", "reason": "人工剔除",
             "ai_verdict": None, "excluded_at": None},
            {"patent_id": 62, "source": "ai", "reason": "AI 判定不相干",
             "ai_verdict": "不相干", "excluded_at": None},
        ]
        with mock.patch.object(workspaces_api, "excluded_patent_rows", return_value=rows):
            resp = client.get(f"/api/v1/workspaces/{WS}/excluded-patents")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["workspace_id"], WS)
        self.assertEqual([item["patent_id"] for item in body["items"]], [61, 62])
        self.assertEqual(body["items"][0]["source"], "manual")
        self.assertEqual(body["items"][1]["source"], "ai")


class ExclusionReviewDecisionTests(unittest.TestCase):
    """POST keep／confirm：使用者逐筆裁決。"""

    def test_keep_calls_engine(self):
        """保留 → keep_patents(workspace_id, patent_ids)，回實際筆數。"""
        fake = mock.MagicMock(return_value=2)
        with mock.patch.object(workspaces_api, "keep_patents", fake):
            resp = client.post(
                f"/api/v1/workspaces/{WS}/exclusion-reviews/keep",
                json={"patent_ids": [11, 12]},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"workspace_id": WS, "kept_count": 2})
        args, _ = fake.call_args
        self.assertEqual(args[0], WS)
        self.assertEqual(list(args[1]), [11, 12])

    def test_confirm_calls_engine(self):
        """確定 → confirm_exclusions(workspace_id, patent_ids)，回實際筆數。"""
        fake = mock.MagicMock(return_value=1)
        with mock.patch.object(workspaces_api, "confirm_exclusions", fake):
            resp = client.post(
                f"/api/v1/workspaces/{WS}/exclusion-reviews/confirm",
                json={"patent_ids": [11]},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"workspace_id": WS, "confirmed_count": 1})
        args, _ = fake.call_args
        self.assertEqual(args[0], WS)
        self.assertEqual(list(args[1]), [11])

    def test_empty_patent_ids_rejected(self):
        """空清單被 pydantic 擋下（min_length=1），不打到引擎。"""
        for path in ("keep", "confirm"):
            with self.subTest(path=path):
                resp = client.post(
                    f"/api/v1/workspaces/{WS}/exclusion-reviews/{path}",
                    json={"patent_ids": []},
                )
                self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
