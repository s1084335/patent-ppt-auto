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

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_build_ppt():
    if "build_ppt_for_threshold" in sys.modules:
        return sys.modules["build_ppt_for_threshold"]
    path = PROJECT_ROOT / "skills" / "patent-report-ppt" / "scripts" / "build_ppt.py"
    spec = importlib.util.spec_from_file_location("build_ppt_for_threshold", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_ppt_for_threshold"] = module
    spec.loader.exec_module(module)
    return module


def _rows(codes: list[str]) -> list[dict]:
    return [{"Orig. IPC(Main)": code, "patent_count": 5} for code in codes]


class EngineThresholdMetadataTests(unittest.TestCase):
    """引擎端：門檻判定寫進 report_data metadata；圖與 rows 照產（網頁不受影響）。"""

    def _run(self, codes):
        import json
        import tempfile

        from backend.app.reports import chart_runner

        def stub_run_report(name, filters=None, limit=None, patent_ids=None):
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


class PptThresholdSkipTests(unittest.TestCase):
    """PPT 端：below_threshold 的分類報表不出頁；缺頁原因進 manifest warnings。"""

    def setUp(self):
        self.bp = _load_build_ppt()

    def _report_data(self, ipc_below: bool, cpc_below: bool):
        return {
            "parameters": {"version": "t"},
            "reports": {
                "application_trend": {"label_zh": "專利申請趨勢",
                                      "rows": [{"application_year": 2024, "patent_count": 5}]},
                "ipc_main_distribution": {"label_zh": "IPC 主分類分布",
                                          "rows": _rows(["A63B-069/18", "F03G-005/00"])},
                "cpc_main_distribution": {"label_zh": "CPC 主分類分布",
                                          "rows": _rows(["A63B-0022/00"])},
            },
            "classification_thresholds": {
                "ipc_main_distribution": {
                    "below_threshold": ipc_below, "distinct_level4": 2,
                    "min_distinct_level4": 3,
                    "reason": "4 階 subclass 僅 2 種（門檻 3）",
                },
                "cpc_main_distribution": {
                    "below_threshold": cpc_below, "distinct_level4": 1,
                    "min_distinct_level4": 3,
                    "reason": "4 階 subclass 僅 1 種（門檻 3）",
                },
            },
        }

    def _page_keys(self, report_data):
        layout = self.bp._expand_page_layout(report_data)
        return {key for spec in layout for key in spec.report_keys}

    def test_below_threshold_reports_do_not_emit_pages(self):
        keys = self._page_keys(self._report_data(ipc_below=True, cpc_below=True))
        self.assertNotIn("ipc_main_distribution", keys, "IPC 低於門檻仍出頁")
        self.assertNotIn("cpc_main_distribution", keys, "CPC 低於門檻仍出頁")

    def test_above_threshold_reports_emit_pages(self):
        keys = self._page_keys(self._report_data(ipc_below=False, cpc_below=False))
        self.assertIn("ipc_main_distribution", keys)

    def test_skip_reason_surfaces_as_warning(self):
        """🔴 缺頁不得靜默：`threshold_skips()` 供 manifest 記 type 與原因。"""
        skips = self.bp.threshold_skips(self._report_data(ipc_below=True, cpc_below=False))
        self.assertEqual(len(skips), 1)
        self.assertEqual(skips[0]["type"], "below_threshold_skipped")
        self.assertEqual(skips[0]["report_key"], "ipc_main_distribution")
        self.assertIn("僅 2 種", skips[0]["reason"])

    def test_old_report_data_without_thresholds_unchanged(self):
        """⚠ 向後相容：舊版本沒有 classification_thresholds 鍵 → 行為完全不變。"""
        rd = self._report_data(ipc_below=True, cpc_below=True)
        rd.pop("classification_thresholds")
        keys = self._page_keys(rd)
        self.assertIn("ipc_main_distribution", keys)
        self.assertEqual(self.bp.threshold_skips(rd), [])


if __name__ == "__main__":
    unittest.main()
