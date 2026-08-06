"""報表 catalog 刪除契約（openspec `improve-report-professionalism` RPT-011）。

## 定案（2026-08-05 使用者裁決，留痕在 `report-professionalism-spec.md`「刪除留痕」節）

| 刪除 | 為何刪 | 原本的問題由誰承接 |
|---|---|---|
| `owner_ranking` | 母體僅 36/55（19 件尚無專利權人，8 件審查中） | 「已轉讓」由申請人排名的斜紋段承接 |
| `owner_year_matrix` | 與 `applicant_year_matrix` 重疊 58%（19/33 格相同） | 年度布局由申請人年度矩陣承接 |
| `family_quality_detail` | 已在 EXCLUDED_FROM_PPT，資料品質稽核不是給決策者看的 | 家族完整性併入國家佈局頁註記 |

## RPT-011 的同步刪除範圍

registry、前端、report metadata、PPT 與測試 SHALL 同步刪除——
⚠ 只刪 registry 不刪前端，前端勾選會整批 400（07-29 的「最新受讓人排名」
就是這樣炸的：後端刪了、前端 `REPORT_TYPES` 漏刪 → 全選一張都產不出來）。

⚠ 歷史報表版本仍含這三張的資料——渲染舊版本不得因 registry 缺定義而炸，
本檔不驗舊版相容（由既有「選擇驅動出頁」機制涵蓋）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REMOVED = ("owner_ranking", "owner_year_matrix", "family_quality_detail")


class RegistryRemovalTests(unittest.TestCase):
    def test_removed_reports_not_in_definitions(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        for name in REMOVED:
            with self.subTest(report=name):
                self.assertNotIn(name, REPORT_DEFINITIONS,
                                 f"{name} 仍在 registry——定案已刪")

    def test_removed_reports_not_in_default_names(self):
        from backend.app.reports import report_definitions as rd

        defaults = getattr(rd, "DEFAULT_REPORT_NAMES", ())
        for name in REMOVED:
            with self.subTest(report=name):
                self.assertNotIn(name, defaults)


class FrontendRemovalTests(unittest.TestCase):
    """🔴 前端與後端同步刪——只刪一邊會讓「全選」整批 400（07-29 實績）。"""

    def test_removed_reports_not_in_frontend_report_types(self):
        html = (PROJECT_ROOT / "backend" / "app" / "static" / "index.html").read_text(
            encoding="utf-8")
        for name in REMOVED:
            with self.subTest(report=name):
                self.assertNotIn(f'"{name}"', html,
                                 f"前端仍引用 {name}——勾選後產製會 400")


class EngineRemovalTests(unittest.TestCase):
    def test_population_reasons_dropped(self):
        from backend.app.reports import population

        self.assertNotIn("owner_ranking", population.POPULATION_REASONS)

    def test_chart_runner_has_no_removed_sections(self):
        """引擎不得再為三張報表出圖／建 section。

        ⚠ 允許出現在註解與 docstring（刪除留痕就寫在那裡）；只擋**帶引號的
        程式引用**（SectionSpec、dict 鍵、ctx.report(...) 都長這樣）。
        """
        src = (PROJECT_ROOT / "backend" / "app" / "reports" / "chart_runner.py").read_text(
            encoding="utf-8")
        for name in REMOVED:
            with self.subTest(report=name):
                hits = [
                    line for line in src.splitlines()
                    if (f'"{name}"' in line or f"'{name}'" in line)
                    and not line.strip().startswith(("#", "不得", "「"))
                ]
                self.assertEqual(hits, [],
                                 f"chart_runner 仍有 {name} 的執行碼：{hits[:3]}")


class PptRemovalTests(unittest.TestCase):
    def test_build_ppt_no_removed_reports(self):
        """PPT 端不得再排版三張報表。

        ⚠ 例外：`EXCLUDED_FROM_PPT` **必須**保留 `family_quality_detail`——
        舊報表版本的 report_data 還帶著這鍵，拿掉這行它會被「動態插頁」撿回簡報。
        這不是殘留，是向後相容的守門；等舊版本全數過期才可清。
        """
        src = (PROJECT_ROOT / "skills" / "patent-report-ppt" / "scripts"
               / "build_ppt.py").read_text(encoding="utf-8")
        for name in REMOVED:
            with self.subTest(report=name):
                hits = [
                    line for line in src.splitlines()
                    if re.search(rf'"{name}"', line)
                    and not line.strip().startswith("#")
                    and "EXCLUDED_FROM_PPT" not in line
                ]
                self.assertEqual(hits, [],
                                 f"build_ppt 仍引用 {name}：{hits[:3]}")

    def test_excluded_from_ppt_keeps_family_quality_guard(self):
        """🔴 反向鎖：EXCLUDED_FROM_PPT 的 family_quality_detail 不得被「順手清掉」。"""
        import importlib.util
        import sys

        path = (PROJECT_ROOT / "skills" / "patent-report-ppt" / "scripts" / "build_ppt.py")
        if "build_ppt_for_removals" not in sys.modules:
            spec = importlib.util.spec_from_file_location("build_ppt_for_removals", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["build_ppt_for_removals"] = module
            spec.loader.exec_module(module)
        bp = sys.modules["build_ppt_for_removals"]
        self.assertIn("family_quality_detail", bp.EXCLUDED_FROM_PPT,
                      "舊報表版本會把家族品質明細當動態插頁撿回簡報")


if __name__ == "__main__":
    unittest.main()
