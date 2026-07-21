"""app_layer 併表後的純邏輯契約（不連 DB）：patent_ids 正規化與輸出版本不覆蓋。

TDD：本測試先於 backend/app/app_layer/workflow_model.py 實作存在（Red），
再以最小實作達 Green。對應需求：
- workspaces.patent_ids_json 必須排序、去重。
- workflow_outputs 重跑建立新 version、不覆蓋舊輸出。
"""
from __future__ import annotations

import unittest

from backend.app.app_layer import workflow_model


class NormalizePatentIdsTests(unittest.TestCase):
    def test_sorts_and_dedups(self):
        """去重且升冪排序，型別統一為 int。"""
        self.assertEqual(workflow_model.normalize_patent_ids([3, 1, 2, 3, 1]), [1, 2, 3])

    def test_accepts_str_ints_and_dedups_across_type(self):
        """字串與整數視為同一 id 去重。"""
        self.assertEqual(workflow_model.normalize_patent_ids(["2", 2, "1", 10, 10]), [1, 2, 10])

    def test_empty_is_empty(self):
        self.assertEqual(workflow_model.normalize_patent_ids([]), [])

    def test_rejects_non_positive_or_noninteger(self):
        """非正整數或無法轉 int 的值視為輸入錯誤。"""
        with self.assertRaises(ValueError):
            workflow_model.normalize_patent_ids([1, 0])
        with self.assertRaises(ValueError):
            workflow_model.normalize_patent_ids([1, "abc"])

    def test_rejects_float_not_silent_truncation(self):
        """float 不得被靜默截斷（1.5→1）；整數值 float（2.0）也視為型別錯誤。"""
        with self.assertRaises(ValueError):
            workflow_model.normalize_patent_ids([1, 1.5])
        with self.assertRaises(ValueError):
            workflow_model.normalize_patent_ids([2.0])

    def test_rejects_bool(self):
        """bool 為 int 子類但不可當 patent_id（True 不得被當成 1）。"""
        with self.assertRaises(ValueError):
            workflow_model.normalize_patent_ids([True])
        with self.assertRaises(ValueError):
            workflow_model.normalize_patent_ids([1, False])


class NextOutputVersionTests(unittest.TestCase):
    def test_first_version_is_one(self):
        """同一 (run_id, output_type) 尚無輸出時，第一版為 1。"""
        self.assertEqual(workflow_model.next_output_version([]), 1)

    def test_next_is_max_plus_one_not_count(self):
        """新版為現有最大版本 +1（不覆蓋、不依數量），即使版本不連續。"""
        self.assertEqual(workflow_model.next_output_version([1, 2, 5]), 6)

    def test_ignores_order(self):
        self.assertEqual(workflow_model.next_output_version([3, 1, 2]), 4)


if __name__ == "__main__":
    unittest.main()
