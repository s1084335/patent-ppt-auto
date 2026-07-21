"""0021 併表補充契約（不連 DB、不動共用 migration fixture）。

Topic key 契約：topic_assignments.topic_key 必須取正式 topics.topic_code；
無法映射時應明確失敗，禁止塞 topic_id 純數字 fallback。以完整 mismatch 灌進
共用 migration fixture 會破壞其他測試的 setUpClass，故此處採最小靜態契約，
直接對 0021 migration 原始碼斷言，不觸碰任何資料庫。
"""
from __future__ import annotations

import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "0021_derived_app_consolidation.py"
)


class TopicKeyContractTests(unittest.TestCase):
    def test_no_numeric_topic_key_fallback(self):
        """topic_assignments.topic_key 應由 topic_code 決定，且不得有純數字 fallback。"""
        src = MIGRATION.read_text(encoding="utf-8")
        # 正向：必須用正式 topic_code 當 key
        self.assertIn("topic_code", src, "topic_assignments 未由 topics.topic_code 取得 topic_key")
        # 反向：禁止映射失敗時 fallback 成 topic_id 數字字串（應改為明確失敗）
        self.assertNotIn(
            "ta.topic_id::text", src,
            "topic_key 使用純數字 fallback(ta.topic_id::text)；無法映射 topic_code 時應明確失敗而非塞數字",
        )


if __name__ == "__main__":
    unittest.main()
