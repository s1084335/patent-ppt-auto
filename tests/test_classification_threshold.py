"""IPC/CPC 出頁門檻（openspec `improve-report-professionalism`，2026-08-05 使用者定案）。

## 定案

「如果 4 階沒有 3 種以上，那 IPC/CPC 就不出現在簡報」。

- 門檻看的是 **4 階（subclass）distinct 種類數**，`< 3` 即不出簡報。
- ⚠ **網頁報表照產**（報表保留＋出頁門檻，不是刪報表）——引擎仍出圖、仍落
  rows；只有 PPT 端不排版。
- 門檻值與缺頁**原因**必須進 metadata（design #5：「門檻與缺頁原因進 metadata」），
  不得讓頁面靜默消失——讀者查 manifest 要能知道「為什麼沒有 CPC 頁」。

## 實例（滑雪機 55 件，A5 實測）

IPC 4 階只有 A63B、F03G **2 種** → 低於門檻，四頁分類頁在範例標準下都不該出現；
p9（CPC L4）整頁只有一根 5 件的長條，正是這條規則要消滅的頁面。
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ⚠ PPT 端的出頁閘門（_load_build_ppt 與三個 Ppt*/Plan*/Eligibility* 測試類）已隨
# PPT 交付線移除（2026-08-10）；門檻判定仍由引擎寫進 metadata，網頁報表照產。


def _rows(codes: list[str]) -> list[dict]:
    return [{"Orig. IPC(Main)": code, "patent_count": 5} for code in codes]


class EngineThresholdMetadataTests(unittest.TestCase):
    """引擎端：門檻判定寫進 report_data metadata；圖與 rows 照產（網頁不受影響）。"""

    def _run(self, codes):
        import json
        import tempfile

        from backend.app.reports import chart_runner

        # ⚠ 用 **kwargs 收尾（2026-08-19）：`run_report` 在 795ef4a 新增了
        #   `report_scope`，替身簽章沒跟上就整批 TypeError——而錯誤訊息長得像
        #   「引擎壞了」，其實是替身太窄。本測試關心的是門檻判定，不是參數表，
        #   多收一個參數不會削弱它，簽章漂移也不再變成假紅。
        def stub_run_report(name, filters=None, limit=None, patent_ids=None,
                            **_ignored):
            return {"report_name": name, "label": name, "label_zh": "IPC 主分類分布",
                    "report_type": "aggregate", "rows": _rows(codes),
                    "row_count": len(codes), "rows_total": len(codes)}

        with tempfile.TemporaryDirectory() as tmp:
            # ⚠ patent_kind 摘要無條件連 DB（A4 的接縫）——單元測試一律注入假值，
            # 沿 test_chart_sections 同一做法，不讓門檻測試依賴真實連線。
            with mock.patch.object(chart_runner, "run_report", stub_run_report),                  mock.patch.object(chart_runner, "fetch_patent_kind_summary",
                                   return_value={"design_count": 0, "design_note": ""}):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp), report_names=["ipc_main_distribution"])
            run_dir = Path(result["output_dir"])
            report_data = json.loads((run_dir / "report_data.json").read_text(encoding="utf-8"))
            files = set(result["files"])
        return report_data, files

    def test_below_threshold_recorded_with_reason(self):
        report_data, files = self._run(["A63B-069/18", "A63B-021/00", "F03G-005/00"])
        # 3 個碼但 4 階只有 A63B/F03G 兩種 → below
        info = report_data["classification_thresholds"]["ipc_main_distribution"]
        self.assertTrue(info["below_threshold"])
        self.assertEqual(info["distinct_level4"], 2)
        self.assertEqual(info["min_distinct_level4"], 3)
        self.assertIn("4 階", info["reason"])
        # ⚠ 網頁端不受影響：圖照出、rows 照落
        self.assertIn("ipc_main_distribution_L4.svg", files)
        self.assertIn("ipc_main_distribution", report_data["reports"])

    def test_at_threshold_not_flagged(self):
        report_data, _ = self._run(["A63B-069/18", "A61H-001/00", "F03G-005/00"])
        info = report_data["classification_thresholds"]["ipc_main_distribution"]
        self.assertFalse(info["below_threshold"])
        self.assertEqual(info["distinct_level4"], 3)


# ⚠ PptThresholdSkipTests 已隨 PPT 交付線移除（2026-08-10，remove-ppt-delivery-line）。




# ⚠ PlanPathThresholdTests 已隨 PPT 交付線移除（2026-08-10，remove-ppt-delivery-line）。




# ⚠ EligibilityGateTests 已隨 PPT 交付線移除（2026-08-10，remove-ppt-delivery-line）。


class ThresholdOverrideTests(unittest.TestCase):
    """門檻值可由 `PPT_CLASSIFICATION_MIN_L4` 覆寫，供驗收暫時停用。

    2026-08-10 使用者定案：「篩選機制暫時不用有，因為要確定 IPC/CPC 也是 OK 的，
    但實機部署篩選機制要能生效」。實機部署不設此變數 → 維持預設 3。
    """

    def _reload_with(self, value: str | None):
        import os

        env = {k: v for k, v in os.environ.items() if k != "PPT_CLASSIFICATION_MIN_L4"}
        if value is not None:
            env["PPT_CLASSIFICATION_MIN_L4"] = value
        with mock.patch.dict("os.environ", env, clear=True):
            import backend.app.reports.chart_runner as cr
            return importlib.reload(cr)

    def test_default_is_three(self):
        self.assertEqual(self._reload_with(None).CLASSIFICATION_MIN_DISTINCT_L4, 3)

    def test_zero_disables_the_gate(self):
        """🔴 設 0 → distinct 數不可能小於 0，等於不篩。"""
        self.assertEqual(self._reload_with("0").CLASSIFICATION_MIN_DISTINCT_L4, 0)

    def tearDown(self):
        self._reload_with(None)  # 還原模組層常數，避免污染後續測試


if __name__ == "__main__":
    unittest.main()
