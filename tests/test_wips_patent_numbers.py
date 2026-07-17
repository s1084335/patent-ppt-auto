from __future__ import annotations

import unittest

from backend.app.importers.wips_importer import normalize_record


class WipsPatentNumberNormalizationTests(unittest.TestCase):
    """驗證 WIPS 原值欄與兩個下游轉換欄不會混用。"""

    def test_taiwan_record_keeps_original_and_builds_transformed_columns(self) -> None:
        """TW 原值保留西元年，兩個轉換後欄使用民國年。"""
        patent = normalize_record(
            {
                "国家代码": "TW",
                "未审查的公开号": "202619621",
                "申请号": "2024132600",
            }
        )["patent"]

        self.assertEqual(patent["未審查的公開號"], "202619621")
        self.assertEqual(patent["未審查的公開號(轉換後)"], "11519621")
        self.assertEqual(patent["申請號"], "2024132600")
        self.assertEqual(patent["申請號(轉換後)"], "113132600")
        self.assertIn("未审查的公开号=11519621", patent["dedupe_key"])
        self.assertIn("申请号=113132600", patent["dedupe_key"])

    def test_non_taiwan_transformed_columns_equal_original_values(self) -> None:
        """非 TW 仍填轉換後欄，讓所有下游固定讀同一組欄位。"""
        patent = normalize_record(
            {
                "国家代码": "US",
                "未审查的公开号": "20240001",
                "申请号": "18/648768",
            }
        )["patent"]

        self.assertEqual(patent["未審查的公開號(轉換後)"], "20240001")
        self.assertEqual(patent["申請號(轉換後)"], "18/648768")


if __name__ == "__main__":
    unittest.main()
