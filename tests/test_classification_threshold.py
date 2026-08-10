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


class PlanPathThresholdTests(unittest.TestCase):
    """🔴 SlidePlan 路徑也要受門檻管（2026-08-10 實機失敗）。

    本類上方的 `PptThresholdSkipTests` 只驗 `_expand_page_layout`——那是**組版自己
    排頁**的路徑。頁序改由 CLI 規劃後，`page_specs_from_plan` 直接把 plan 指定的圖
    轉成 PageSpec，整條路徑不經過 `_report_key_has_data`，門檻形同虛設。

    實機：`below_threshold=True`、manifest 也記了 `below_threshold_skipped: 2`，
    但成品 p5 照樣是 IPC 主分類，貼著 L4/L5 兩張圖。
    ⚠ manifest 記了「skipped」反而讓人以為擋掉了——判定算對、警告發了、
    沒有人據以行動，是這類缺陷的共同特徵。

    ⚠ 只補這裡不夠：舊測試把「單一路徑生效」釘成了正確行為，才讓缺陷有地方躲。
    """

    def setUp(self):
        self.bp = _load_build_ppt()

    def _report_data(self):
        return {
            "parameters": {"version": "t"},
            "reports": {
                "ipc_main_distribution": {"label_zh": "IPC 主分類分布",
                                          "rows": _rows(["A63B-069/18", "F03G-005/00"])},
                # ⚠ 對照組要有真資料：`_report_key_has_data` 對「report_data 裡根本沒有
                # 這個 key」也回 False，少放這筆會讓兩種剔除原因混在一起分不出來。
                "application_trend": {"label_zh": "專利申請趨勢",
                                      "rows": [{"application_year": 2024, "patent_count": 5}]},
            },
            "classification_thresholds": {
                "ipc_main_distribution": {
                    "below_threshold": True, "distinct_level4": 2,
                    "min_distinct_level4": 3, "reason": "4 階 subclass 僅 2 種（門檻 3）",
                },
            },
        }

    def test_plan_specified_page_is_dropped(self):
        """CLI 規劃了 IPC 頁 → 低於門檻時整頁不得進成品。"""
        specs = [
            self.bp.PageSpec(page=1, kind="chart_with_points", title="技術分類",
                             topic="技術分類", report_keys=("ipc_main_distribution",),
                             charts=("ipc_main_distribution_L4.svg",)),
            self.bp.PageSpec(page=2, kind="chart_with_points", title="申請趨勢",
                             topic="申請趨勢", report_keys=("application_trend",),
                             charts=("application_trend.svg",)),
        ]
        kept = self.bp.drop_below_threshold_pages(specs, self._report_data())
        self.assertEqual([s.report_keys for s in kept], [("application_trend",)],
                         "plan 指定的 IPC 頁沒被門檻擋下")

    def test_pages_without_report_keys_survive(self):
        """封面、判讀說明這類沒有 report_key 的頁不受影響。"""
        specs = [self.bp.PageSpec(page=1, kind="cover", title="封面", topic="封面",
                                  report_keys=(), charts=())]
        self.assertEqual(len(self.bp.drop_below_threshold_pages(specs, self._report_data())), 1)

    def test_mixed_page_survives_if_any_key_has_data(self):
        """一頁掛兩個報表、只有一個低於門檻 → 頁留著（另一個仍有判讀價值）。"""
        specs = [self.bp.PageSpec(page=1, kind="comparison", title="混合", topic="混合",
                                  report_keys=("ipc_main_distribution", "application_trend"),
                                  charts=("a.svg", "b.svg"))]
        self.assertEqual(len(self.bp.drop_below_threshold_pages(specs, self._report_data())), 1)

    def test_uses_the_same_single_judgement(self):
        """⚠ 判準只能有一個入口：本函式必須走 `_report_key_has_data`，不得自立第二套。

        用 mock 確認真的呼叫了它——否則日後有人在這裡寫死
        `thresholds[key]['below_threshold']`，兩處就會各自演進。
        """
        specs = [self.bp.PageSpec(page=1, kind="chart_with_points", title="t", topic="t",
                                  report_keys=("ipc_main_distribution",), charts=("a.svg",))]
        with mock.patch.object(self.bp, "_report_key_has_data",
                               return_value=True) as spy:
            kept = self.bp.drop_below_threshold_pages(specs, self._report_data())
        self.assertTrue(spy.called, "沒有走 _report_key_has_data，判準被複製了第二份")
        self.assertEqual(len(kept), 1)


class EligibilityGateTests(unittest.TestCase):
    """後端可選清單：低於門檻的 section 一張圖都不給。

    ⚠ 這是門檻的**第一道**——CLI 拿不到圖就不會規劃那頁。組版端的
    `drop_below_threshold_pages` 是第二道，因為 skill 是獨立部署單元，
    不能假設上游一定過濾過。
    """

    def test_excluded_section_yields_no_eligible_variants(self):
        from backend.app.main import ppt_eligible_variant_keys
        section = {
            "report_key": "ipc_main_distribution",
            "ppt_excluded_reason": "4 階 subclass 僅 2 種（門檻 3）",
            "variants": [
                {"label": "L4", "file": "ipc_main_distribution_L4.svg", "variant_key": "L4"},
                {"label": "L5", "file": "ipc_main_distribution_L5.svg", "variant_key": "L5"},
            ],
        }
        self.assertEqual(ppt_eligible_variant_keys(section), set(),
                         "低於門檻的 section 不得有任何可選變體")

    def test_l5_cannot_survive_without_l4(self):
        """定案「4 階沒出現，5 階就不會有」——兩階同屬一個 report_key，
        門檻對 report_key 判定，所以這是結構保證而非另一條規則。"""
        from backend.app.main import ppt_eligible_variant_keys
        section = {"report_key": "cpc_main_distribution",
                   "ppt_excluded_reason": "4 階 subclass 僅 1 種（門檻 3）",
                   "variants": [{"file": "cpc_main_distribution_L5.svg", "variant_key": "L5"}]}
        self.assertNotIn("L5", ppt_eligible_variant_keys(section))

    def test_normal_section_unaffected(self):
        from backend.app.main import ppt_eligible_variant_keys
        section = {"report_key": "applicant_ranking",
                   "variants": [{"file": "applicant_ranking.svg", "variant_key": "default"}]}
        self.assertEqual(ppt_eligible_variant_keys(section), {"default"})

    def test_empty_reason_is_not_exclusion(self):
        """空字串／None 不算排除——避免把「有這個鍵」誤當「被排除」。"""
        from backend.app.main import ppt_eligible_variant_keys
        for value in (None, "", "   "):
            section = {"ppt_excluded_reason": value,
                       "variants": [{"file": "x.svg", "variant_key": "default"}]}
            self.assertEqual(ppt_eligible_variant_keys(section), {"default"}, f"reason={value!r}")


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
