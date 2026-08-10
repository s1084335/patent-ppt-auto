"""規劃階段必須實際查證，不得只憑選圖數據就寫（2026-08-10 使用者定案）。

使用者原話：「我給他的數據報表都是一定要產的，這個就是給模組控制，CLI 是根據這些
內容去判斷要找啥證據來寫，所以**不能讓它可以不去查資料庫就直接寫**」。

## 分工

- **模組**：使用者選的圖表一定要產、一定要進 PPT（由 `validate_slide_plan` 的
  「選了但未使用」檢查守，已存在）。
- **CLI**：看著這些圖表與數據，判斷要查什麼證據，**實際查**，再依查到的內容寫。

## 為什麼要擋

原本 `run_report_planning` 只把 `query_audit` 放進結果並註解「空清單有意義——代表
這次規劃完全沒有查證」。⚠ 但「有意義」不等於「有人看」：沒有任何地方會因為它是空的
而失敗，等於允許 CLI 只讀聚合數字就編出整份敘述。

`content_standard.md` 第三節已規定專利層事實必須查回來直接引用、衍生判斷必須附依據
——那條規則原本只寫在給 AI 看的提示裡，本測把它變成程式檢查。
（同型教訓見 `known-issues-optimization.md` C-1：只寫在提示、沒有程式驗證的規則，
等於沒有規則。）
"""
from __future__ import annotations

import unittest

from backend.app.reports.planning_contracts import validate_research_effort


class ResearchEffortTests(unittest.TestCase):
    """`query_audit` 是規劃有沒有真的查證的唯一證據。"""

    def test_empty_audit_is_rejected(self):
        """完全沒查 → 不合格。這是本測的核心：不查就寫必須被擋下。"""
        errors = validate_research_effort([])
        self.assertTrue(errors, "沒有任何查詢紀錄時必須報錯")
        self.assertIn("查證", errors[0])

    def test_successful_query_passes(self):
        """有成功查詢即通過——不規定要查幾次或查什麼，那是 CLI 依內容判斷的。"""
        self.assertEqual(
            validate_research_effort([{"tool": "query_patents", "status": "ok", "rows": 12}]),
            [],
        )

    def test_all_failed_queries_is_rejected(self):
        """查了但全部失敗 ≠ 有查證——敘述仍然沒有依據。

        ⚠ 這種情形最容易被誤判成「有查」：audit 非空，但每一筆都是錯誤。
        """
        errors = validate_research_effort([
            {"tool": "query_patents", "status": "error", "message": "syntax error"},
            {"tool": "query_patents", "status": "error", "message": "timeout"},
        ])
        self.assertTrue(errors, "全部查詢都失敗時必須報錯")

    def test_mixed_success_and_failure_passes(self):
        """部分失敗但有成功 → 通過。查錯再重查是正常的探索過程，不該懲罰。"""
        self.assertEqual(
            validate_research_effort([
                {"tool": "query_patents", "status": "error", "message": "syntax error"},
                {"tool": "query_patents", "status": "ok", "rows": 3},
            ]),
            [],
        )

    def test_missing_status_treated_as_success(self):
        """稽核缺 status 欄時當成功——寧可放行，不可因稽核格式變動而擋掉正常規劃。

        ⚠ 這條是刻意的鬆綁：本檢查的目的是擋「完全沒查」，不是當格式糾察隊。
        """
        self.assertEqual(validate_research_effort([{"tool": "list_report_catalog"}]), [])


if __name__ == "__main__":
    unittest.main()
