"""呼叫端與 workspace_service 的參數名契約（純簽名檢查，不連 DB）。

背景：handlers／api 曾以 `topic_ids=` 呼叫 `merge_workspace_topics`，但引擎簽名是
`topic_keys=`，執行期直接 TypeError。既有 handler 測試用 `mock.patch.object` 無 autospec，
mock 接受任意 kwargs，因此不會紅——不一致長期沒被抓到。

本檔以 `autospec=True` 建立 mock：autospec 會套用**真實函式簽名**，呼叫端傳錯參數名時
mock 本身就拋 TypeError。任一邊日後改名而另一邊沒跟上，這裡會立刻紅。
"""
from __future__ import annotations

import inspect
from contextlib import nullcontext
import unittest
from unittest import mock

from backend.app.clustering import workspace_service
from backend.app.clustering.sources import SOURCE_FIELD_TECHNICAL
from backend.app.worker import handlers


class MergeSignatureContractTests(unittest.TestCase):
    """鎖住引擎端簽名本身，讓改名這件事是刻意行為而非意外。"""

    def test_merge_workspace_topics_accepts_topic_keys(self):
        """merge 以 topic_keys（topic_code 字串）識別主題，不是 topic_ids。"""
        params = inspect.signature(workspace_service.merge_workspace_topics).parameters
        self.assertIn("topic_keys", params)
        self.assertNotIn("topic_ids", params)

    def test_unmerge_workspace_topics_accepts_merge_run_id(self):
        """unmerge 以 merge_run_id 識別要復原的那筆合併。"""
        params = inspect.signature(workspace_service.unmerge_workspace_topics).parameters
        self.assertIn("merge_run_id", params)
        self.assertNotIn("topic_ids", params)


class WorkerHandlerCallContractTests(unittest.TestCase):
    """worker handler → 引擎：用 autospec mock 驗證實際傳入的 kwargs 名稱可被接受。"""

    def _context(self):
        context = mock.Mock()
        context.heartbeat.return_value = None
        context.keepalive.return_value = nullcontext()
        context.job.workspace_id = 7
        return context

    def test_handle_topic_merge_passes_topic_keys_to_engine(self):
        """autospec 會套真實簽名：handler 若傳 topic_ids= 這裡直接 TypeError。

        _resolve_active_topic_ids 是連 DB 的前置診斷，與本檔要驗的參數名契約無關，故 mock 掉。
        """
        with mock.patch.object(
            handlers, "_resolve_active_topic_ids", autospec=True, return_value=[11, 12]
        ), mock.patch.object(
            handlers, "merge_workspace_topics", autospec=True, return_value={"ok": True}
        ) as engine:
            handlers.handle_topic_merge(
                {
                    "source_field": SOURCE_FIELD_TECHNICAL,
                    "topic_keys": ["T01", "T02"],
                    "label": "合併後主題",
                    "requested_by": "web-user",
                },
                self._context(),
            )
        kwargs = engine.call_args.kwargs
        # 佇列存的是 topic_code 字串，必須原樣傳給引擎，不做 int 轉換
        self.assertEqual(kwargs["topic_keys"], ["T01", "T02"])
        self.assertNotIn("topic_ids", kwargs)
        self.assertEqual(kwargs["workspace_id"], 7)
        self.assertEqual(kwargs["merged_by"], "web-user")
        self.assertEqual(kwargs["label"], "合併後主題")

    def test_handle_topic_unmerge_passes_merge_run_id_to_engine(self):
        """unmerge 呼叫端同樣以真實簽名驗證。"""
        with mock.patch.object(
            handlers, "unmerge_workspace_topics", autospec=True, return_value={"ok": True}
        ) as engine:
            handlers.handle_topic_unmerge(
                {
                    "source_field": SOURCE_FIELD_TECHNICAL,
                    "merge_run_id": 55,
                    "requested_by": "web-user",
                },
                self._context(),
            )
        kwargs = engine.call_args.kwargs
        self.assertEqual(kwargs["merge_run_id"], 55)
        self.assertEqual(kwargs["reverted_by"], "web-user")
        self.assertNotIn("topic_ids", kwargs)


class ApiCallContractTests(unittest.TestCase):
    """FastAPI 端點 → 引擎：同樣以 autospec 鎖參數名。"""

    def test_merge_endpoint_passes_topic_keys_to_engine(self):
        """API 收到的 topic_keys 必須以 topic_keys= 傳進引擎。"""
        from backend.app.clustering import api

        with mock.patch.object(
            api, "merge_workspace_topics", autospec=True
        ) as engine, mock.patch.object(api, "workspace_dashboard", return_value={}), \
                mock.patch.object(api, "asdict", return_value={}):
            api.merge_topics(
                workspace_id=7,
                source_field=SOURCE_FIELD_TECHNICAL,
                request=api.MergeRequest(topic_keys=["T01", "T02"], merged_by="web-user"),
            )
        kwargs = engine.call_args.kwargs
        self.assertEqual(kwargs["topic_keys"], ["T01", "T02"])
        self.assertNotIn("topic_ids", kwargs)

    def test_unmerge_endpoint_passes_merge_run_id_to_engine(self):
        """unmerge 端點以 merge_run_id 呼叫引擎。"""
        from backend.app.clustering import api

        with mock.patch.object(
            api, "unmerge_workspace_topics", autospec=True
        ) as engine, mock.patch.object(api, "workspace_dashboard", return_value={}), \
                mock.patch.object(api, "asdict", return_value={}):
            api.unmerge_topics(
                workspace_id=7,
                source_field=SOURCE_FIELD_TECHNICAL,
                merge_run_id=55,
                request=api.UnmergeRequest(reverted_by="web-user"),
            )
        kwargs = engine.call_args.kwargs
        self.assertEqual(kwargs["merge_run_id"], 55)
        self.assertNotIn("topic_ids", kwargs)


if __name__ == "__main__":
    unittest.main()
