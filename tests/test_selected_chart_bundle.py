"""P2 第 2 節：選圖資料包 producer／materializer（tasks 2.1–2.4）。

從既有 report 版本目錄（report_data.json ＋ SVG／PNG）產出 immutable 的
`SelectedChartBundle`：圖片複製到受控唯讀工作目錄、數據取該圖的 rows slice、
checksum 綁定兩者。

⚠ 為什麼要 materialize 而不是直接給路徑：CLI 只能看到列入 manifest 的檔案
（design.md 第 1 點），且圖片與數據要能證明來自同一版本——直接指向報表目錄
會讓 CLI 看到未選的圖，也無法保證中途沒被重產。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.reports import chart_bundle as cbd


REPORT_DATA = {
    "sections": [
        {"title": "主要申請人排名", "report_key": "applicant_ranking",
         "variants": [{"label": "Bar", "file": "applicant_ranking.svg",
                       "variant_key": "default"}]},
        {"title": "專利狀態分析", "report_key": "lifecycle",
         "variants": [{"label": "Matrix", "file": "lifecycle.svg",
                       "variant_key": "default"}]},
    ],
    "chart_rows": {
        "applicant_ranking": [{"applicant_display_name": "A", "patent_count": 3}],
        "lifecycle": [{"applicant_display_name": "A", "已授權": 2}],
    },
    "population": {"applicant_ranking": "母體 55/55 件（含共同申請）"},
    "parameters": {"workspace_name": "滑雪機"},
}


class _Fixture:
    """建一個假的報表版本目錄（含 report_data.json 與兩張圖）。"""

    def __init__(self, tmp: str):
        self.run_dir = Path(tmp) / "report_trial_x"
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "report_data.json").write_text(
            json.dumps(REPORT_DATA, ensure_ascii=False), encoding="utf-8")
        for name in ("applicant_ranking.svg", "lifecycle.svg"):
            (self.run_dir / name).write_text(f"<svg>{name}</svg>", encoding="utf-8")
        self.work_dir = Path(tmp) / "work"


class BundleProducerTests(unittest.TestCase):
    def test_builds_bundle_for_selected_only(self):
        """只包使用者選的——CLI 不該看到未選的圖。"""
        with TemporaryDirectory() as tmp:
            f = _Fixture(tmp)
            bundles = cbd.build_selected_bundles(
                f.run_dir, ["applicant_ranking:default"], f.work_dir)
            self.assertEqual([b["chart_identity"] for b in bundles],
                             ["applicant_ranking:default"])
            files = {p.name for p in f.work_dir.rglob("*") if p.is_file()}
            self.assertNotIn("lifecycle.svg", files)

    def test_bundle_pairs_image_and_rows(self):
        with TemporaryDirectory() as tmp:
            f = _Fixture(tmp)
            b = cbd.build_selected_bundles(
                f.run_dir, ["applicant_ranking:default"], f.work_dir)[0]
            self.assertTrue(Path(b["image_path"]).exists())
            self.assertEqual(b["data_rows"], REPORT_DATA["chart_rows"]["applicant_ranking"])
            self.assertIn("母體", b["population_note"])
            self.assertTrue(b["checksum"])
            self.assertEqual(b["version"], "report_trial_x")

    def test_checksum_changes_with_content(self):
        """checksum 綁圖片＋數據：任一改變就換值（阻止錯配）。"""
        with TemporaryDirectory() as tmp:
            f = _Fixture(tmp)
            first = cbd.build_selected_bundles(
                f.run_dir, ["applicant_ranking:default"], f.work_dir)[0]["checksum"]
            (f.run_dir / "applicant_ranking.svg").write_text("<svg>changed</svg>",
                                                            encoding="utf-8")
            second = cbd.build_selected_bundles(
                f.run_dir, ["applicant_ranking:default"], f.work_dir / "b")[0]["checksum"]
            self.assertNotEqual(first, second)

    def test_unknown_identity_fails_loud(self):
        with TemporaryDirectory() as tmp:
            f = _Fixture(tmp)
            with self.assertRaises(cbd.ChartBundleError):
                cbd.build_selected_bundles(f.run_dir, ["nope:default"], f.work_dir)

    def test_missing_image_fails_loud(self):
        """圖檔不在＝資料包不成立，不得只給數據矇混。"""
        with TemporaryDirectory() as tmp:
            f = _Fixture(tmp)
            (f.run_dir / "applicant_ranking.svg").unlink()
            with self.assertRaises(cbd.ChartBundleError):
                cbd.build_selected_bundles(
                    f.run_dir, ["applicant_ranking:default"], f.work_dir)

    def test_bundles_pass_contract_validation(self):
        """產出必須直接通過第 1 節的契約驗證（兩節不得各有一套形狀）。"""
        from backend.app.reports.planning_contracts import validate_chart_bundle

        with TemporaryDirectory() as tmp:
            f = _Fixture(tmp)
            for b in cbd.build_selected_bundles(
                    f.run_dir, ["applicant_ranking:default", "lifecycle:default"],
                    f.work_dir):
                self.assertEqual(validate_chart_bundle(b), [])


class WorkDirIsolationTests(unittest.TestCase):
    def test_manifest_lists_exactly_the_materialized_files(self):
        """manifest 是 CLI 可見檔案的唯一清單。"""
        with TemporaryDirectory() as tmp:
            f = _Fixture(tmp)
            bundles = cbd.build_selected_bundles(
                f.run_dir, ["applicant_ranking:default"], f.work_dir)
            manifest = json.loads((f.work_dir / "bundle_manifest.json").read_text("utf-8"))
            listed = {Path(item["image_path"]).name for item in manifest["charts"]}
            on_disk = {p.name for p in f.work_dir.rglob("*")
                       if p.is_file() and p.suffix in {".svg", ".png"}}
            self.assertEqual(listed, on_disk)
            self.assertEqual(len(bundles), len(manifest["charts"]))


if __name__ == "__main__":
    unittest.main()
