"""params 裡填的值不得被具名欄位的**預設值**蓋掉（2026-08-10 實機）。

## 實機失敗

前端按「產生 PPT」→ `POST /ai-tasks`：

    {"task_type": "ai:report_plan",
     "params": {"then_export_ppt": true, "selected_charts": [...], ...}}

到 worker 卻變成 `then_export_ppt=False`，規劃完成後**不接續組版**——
使用者按了按鈕只拿到 plan，PPT 沒出來。⚠ 全程沒有錯誤訊息：job succeeded、
畫面顯示成功，東西就是沒有。

## 根因

    payload = dict(self.params)              # then_export_ppt=True ✓
    named = model_dump(exclude_none=True)    # 具名欄位，預設 False（非 None）
    payload.update(named)                    # ⚠ False 蓋掉 True

`exclude_none` 只排除 None。`then_export_ppt: bool = False` 這種**有非 None 預設值**
的欄位一律會被輸出，於是「呼叫端沒填」與「呼叫端填了 False」變得無法區分——
而 params 裡明明填了 True。

修法：改用 `exclude_unset`，只有**真的送了**的具名欄位才覆蓋 params。

⚠ 這個模型刻意接受兩種 body 形狀（具名欄位／泛型 params），兩者同名時的優先序
就是它的核心契約，卻一直沒有測試。
"""
from __future__ import annotations

import unittest

from backend.app.api.ai_tasks import CreateAiTaskRequest


class PayloadMergeTests(unittest.TestCase):
    """兩種 body 形狀的合併優先序。"""

    def test_params_value_survives_unset_named_default(self):
        """🔴 params 填了 True，具名欄位沒送 → 必須是 True。"""
        req = CreateAiTaskRequest(
            task_type="ai:report_plan",
            params={"then_export_ppt": True, "snapshot_id": "v1"},
        )
        self.assertIs(req.to_payload()["then_export_ppt"], True,
                      "params 的值被具名欄位的預設值蓋掉了")

    def test_explicit_named_field_wins(self):
        """具名欄位**有送**時仍應覆蓋 params——那才是「具名優先」的原意。"""
        req = CreateAiTaskRequest(
            task_type="ai:report_plan",
            params={"then_export_ppt": True},
            then_export_ppt=False,
        )
        self.assertIs(req.to_payload()["then_export_ppt"], False)

    def test_params_only_fields_pass_through(self):
        """params 專有的鍵原樣帶過（selected_charts 等泛型欄位）。"""
        payload = CreateAiTaskRequest(
            task_type="ai:report_plan",
            params={"selected_charts": ["a:default"], "page_budget": 12},
        ).to_payload()
        self.assertEqual(payload["selected_charts"], ["a:default"])
        self.assertEqual(payload["page_budget"], 12)

    def test_unset_named_fields_do_not_appear(self):
        """沒送的具名欄位不該憑空出現在 payload 裡。

        ⚠ 憑空出現的預設值會讓下游分不清「沒指定」與「指定為預設」。
        """
        payload = CreateAiTaskRequest(task_type="ai:narrative",
                                      params={"based_on_version": "v1"}).to_payload()
        self.assertNotIn("then_export_ppt", payload)
        self.assertNotIn("report_keys", payload)


if __name__ == "__main__":
    unittest.main()
