"""公司中文名填了卻顯示英文（2026-07-29 使用者實測發現）。

## 症狀

使用者在中文名確認流程填了「南通鐵人運動用品」，但報表仍顯示
`NANTONG IRONMASTER SPORTING INDUSTRIAL CO., LTD.`。

## 三個根因（實測 UN226597 的 4 列）

| id | 中文名 | 正規化名稱 | 別稱 | 來源 |
|---|---|---|---|---|
| 56 | 南通鐵人運動用品 | NANTONG…CO., LTD. | NANTONG…CO., LTD. | zh_name_review |
| 57 | **None** | NANTONG…CO., LTD. | NANTONG…Co.,Ltd. | code_registry |
| 58 | **None** | NANTONG…CO., LTD. | NANTONG…CO LTD | code_registry |
| 60 | 南通鐵人運動用品 | **None** | **南通鐵人運動用品** | zh_name_review |

1. **中文名只寫進 1 列**——同代碼的其他變體（57、58）沒同步
2. **中文名被當成別稱多寫一列**（60），又多一列稀釋
3. **`mode()` 平手取字母序**——4 列的 COALESCE 結果是「中文、英文、英文、中文」
   2:2 平手，`N` 排在「南」前面 → **中文永遠輸**

## 定案（使用者：「中文名 bug 修掉再一起推」）

**顯示端優先取非空的中文名**，不靠眾數投票：
一個代碼只要**有任何一列**填了中文名，就用它——中文名是人工裁決的結果，
不該被「有幾列填了」這種資料形狀決定。

⚠ 不改 `mode()` 對正規化名稱的用法：那是多個英文寫法擇一，眾數是對的。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ZhNamePrioritySqlTests(unittest.TestCase):
    """refresh SQL 的收斂邏輯必須讓中文名優先於眾數。"""

    @staticmethod
    def _sql() -> str:
        return (PROJECT_ROOT / "backend" / "app" / "derived"
                / "refresh_report_patent_base.py").read_text(encoding="utf-8")

    def test_zh_name_not_subject_to_mode_tie(self):
        """中文名不得只靠 mode() 決定——平手時會輸給字母序在前的英文。"""
        sql = self._sql()
        start = sql.index("code_alias_names AS (")
        block = sql[start:sql.index("_code_names AS (", start + 1)]
        # 必須有「先取任一非空中文名」的邏輯，而不是把中文名丟進 mode() 賭眾數
        self.assertIn("公司中文名稱", block)
        # ⚠ regex 要不分大小寫——SQL 寫的是 MAX(，本測試初版用小寫而假失敗。
        self.assertRegex(
            block, r"(?i)(max|min|FILTER)\s*\(",
            "缺少『取任一非空中文名』的聚合——只用 mode() 會在平手時取字母序")


class ZhNamePriorityLogicTests(unittest.TestCase):
    """純邏輯驗證：不連 DB。

    ⚠ 原本這裡連正式庫查 UN226597，兩個問題：
    ① 使用者紅線是「測試不可碰正式庫」——讀也一樣，資料隨時會變（實測跑到一半
       該代碼就因使用者重建而消失）
    ② conftest 在 import 期灌 DATABASE_URL，環境變數優先於 .env，取到污染值

    改為驗真正該驗的**選值規則**：多列中有任一列填了中文名就取它，
    不受「幾列填了」影響。SQL 端由上面的靜態測試鎖住寫法。
    """

    @staticmethod
    def _pick(rows):
        """重現 SQL 的 COALESCE(MAX(中文名), mode(正規化名)) 語意。"""
        zh = [r["zh"] for r in rows if (r.get("zh") or "").strip()]
        if zh:
            return max(zh)
        norm = [r["norm"] for r in rows if (r.get("norm") or "").strip()]
        if not norm:
            return None
        return max(set(norm), key=lambda v: (norm.count(v), v))

    def test_single_chinese_row_wins_over_many_english(self):
        """1 列中文 vs 3 列英文——中文仍要贏（眾數會輸）。"""
        rows = [{"zh": "南通鐵人運動用品", "norm": "NANTONG A"},
                {"zh": None, "norm": "NANTONG A"},
                {"zh": None, "norm": "NANTONG A"},
                {"zh": None, "norm": "NANTONG A"}]
        self.assertEqual(self._pick(rows), "南通鐵人運動用品")

    def test_tie_case_from_real_data(self):
        """實測 UN226597 的形狀：中文、英文、英文、中文 → 2:2 平手。

        修前 mode() 平手取字母序，'N' < '南' 故顯示英文——這正是使用者回報的症狀。
        """
        rows = [{"zh": "南通鐵人運動用品", "norm": "NANTONG A"},
                {"zh": None, "norm": "NANTONG A"},
                {"zh": None, "norm": "NANTONG A"},
                {"zh": "南通鐵人運動用品", "norm": None}]
        self.assertEqual(self._pick(rows), "南通鐵人運動用品")

    def test_no_chinese_falls_back_to_mode(self):
        """完全沒有中文名時退回正規化名稱的眾數。"""
        rows = [{"zh": None, "norm": "ACME CORP"},
                {"zh": None, "norm": "ACME CORP"},
                {"zh": None, "norm": "ACME INC"}]
        self.assertEqual(self._pick(rows), "ACME CORP")

    def test_all_empty(self):
        self.assertIsNone(self._pick([{"zh": None, "norm": None}]))


if __name__ == "__main__":
    unittest.main()
