"""解讀契約驗證不得因 points 形狀不符而**崩潰**（2026-08-10 實機 job 284）。

## 實機失敗

`ai:narrative` job 284 跑了 **878 秒**，CLI 解讀全部產完，最後倒在契約驗證：

    File "ai_narrative_runner.py", line 194, in validate_narrative_contract
    AttributeError: 'str' object has no attribute 'get'

    text = str((point or {}).get("text") or "")

契約要求 `points` 是物件陣列 `[{"text": ...}]`，CLI 這次回了字串陣列
`["文字", ...]`。`point or {}` 對**非空字串**回傳的是那個字串本身，`.get` 不存在。

## 為什麼這個特別糟

驗證函式的職責是「把不合契約的地方**列成 warnings**」，它自己卻用
崩潰來回應不合契約的輸入——整份解讀連同 878 秒的 CLI 成本一起丟掉，
使用者看到的只有一行 AttributeError，不知道哪一頁、哪一條有問題。

⚠ 同型風險：`(x or {}).get(...)` 這個寫法只防 `None` 與空值，防不了型別。
本檔同時釘住「字串形狀要被容忍並回報」與「其他型別不得崩潰」。
"""
from __future__ import annotations

import unittest

from backend.app.worker.ai_narrative_runner import validate_narrative_contract


def _payload(points):
    """一份最小的解讀 payload（reports → variants → entry），只有 points 形狀在變。"""
    return {
        "reports": {
            "application_trend": {
                "variants": {
                    "default": {
                        "headline": "申請量兩波高峰",
                        "text": "整體呈雙峰分布。",
                        "points": points,
                    }
                }
            }
        }
    }


class PointShapeTests(unittest.TestCase):
    """points 元素的形狀容錯。"""

    def test_string_points_do_not_crash(self):
        """🔴 CLI 回字串陣列 → 必須列 warning，不得拋 AttributeError。"""
        warnings = validate_narrative_contract(_payload(["2022 年 15 件為真爆發", "2024 年回落"]))
        self.assertIsInstance(warnings, list)
        self.assertTrue(any("形狀" in w or "字串" in w or "格式" in w for w in warnings),
                        f"字串形狀應被列為 warning，實得：{warnings}")

    def test_object_points_still_work(self):
        """物件陣列＝正常契約，不得因容錯而漏掉既有檢查。"""
        warnings = validate_narrative_contract(
            _payload([{"text": "2022 年 15 件為真爆發"}, {"text": "2024 年回落"}]))
        self.assertIsInstance(warnings, list)

    def test_none_and_other_types_do_not_crash(self):
        """None、數字、巢狀 list 等異常元素一律不得崩潰。"""
        for bad in ([None], [123], [["巢狀"]], [{"text": "正常"}, None]):
            with self.subTest(bad=bad):
                self.assertIsInstance(validate_narrative_contract(_payload(bad)), list)

    def test_overlong_string_point_is_measured(self):
        """字串形狀也要能量到字數——容錯不等於跳過檢查。"""
        warnings = validate_narrative_contract(_payload(["超" * 400]))
        self.assertTrue(any("超限" in w for w in warnings),
                        f"字串 point 的長度仍要檢查，實得：{warnings}")


if __name__ == "__main__":
    unittest.main()
