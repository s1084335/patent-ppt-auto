"""產圖流程要為 web 與 PPT 各出一份（P3，openspec separate-web-and-ppt-chart-profiles）。

在此之前 `chart_profiles` 的契約與 `chart_sizing` 的 WEB／PPT 兩組數字都已就位，
但**產圖流程只跑一次**——`DEFAULT_PROFILE="ppt"` 讓 PPT 端先受益，網頁端仍在看
PPT 尺寸的圖。本檔驗的是「真的產了兩份」與「兩份只差呈現」。

⚠ 不驗「web 圖比較小」這種方向性：兩個 profile 的差異由 `chart_sizing` 定義，
在這裡重述一次就是同一份知識的第二個落點。這裡只驗**產出存在、內容不同、
資料相同**。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.reports import chart_profiles as cp
from backend.app.reports import chart_runner as cr


class ProfileAwareWriteTests(unittest.TestCase):
    """寫檔路徑依作用中的 profile 決定，renderer 不必各自知道這件事。"""

    ROWS = [{"applicant_display_name": "甲公司", "patent_count": 5},
            {"applicant_display_name": "乙公司", "patent_count": 3}]

    def test_ppt_profile_writes_original_name(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "applicant_ranking.svg"
            with cp.profile_context("ppt"):
                cr.render_bar_chart(target, "主要申請人排名", self.ROWS,
                                    "applicant_display_name")
            self.assertTrue(target.exists(), "PPT profile 應寫入原檔名")

    def test_web_profile_redirects_to_web_name(self):
        """⚠ 呼叫端**仍傳原路徑**——profile 分流不該讓每個 renderer 都改介面。"""
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "applicant_ranking.svg"
            with cp.profile_context("web"):
                cr.render_bar_chart(target, "主要申請人排名", self.ROWS,
                                    "applicant_display_name")
            self.assertFalse(target.exists(), "web profile 不該覆蓋 PPT 檔")
            self.assertTrue((Path(tmp) / "applicant_ranking.web.svg").exists())

    def test_both_profiles_coexist_with_same_data(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "applicant_ranking.svg"
            for profile in ("ppt", "web"):
                with cp.profile_context(profile):
                    cr.render_bar_chart(target, "主要申請人排名", self.ROWS,
                                        "applicant_display_name")
            ppt = target.read_text(encoding="utf-8")
            web = (Path(tmp) / "applicant_ranking.web.svg").read_text(encoding="utf-8")
            self.assertNotEqual(ppt, web, "兩 profile 應有呈現差異")
            for svg in (ppt, web):
                self.assertLess(svg.index("甲公司"), svg.index("乙公司"),
                                "兩 profile 的資料與排序必須一致")


class SectionsRunTwiceTests(unittest.TestCase):
    """主流程要在兩個 profile 下各跑一次 section builders。

    ⚠ 第二輪只該重出圖：sections／chart_rows 是給 report_data.json 的資料，
    重複 append 會讓網頁報表出現重複卡片。
    """

    def test_builders_run_once_per_profile(self):
        seen: list[str] = []

        def fake_build(ctx):
            seen.append(cp.active_profile_name())
            ctx.sections.append({"title": "假卡片"})

        with TemporaryDirectory() as tmp:
            ctx = cr.ChartContext(
                run_dir=Path(tmp), ranking_limit=10, ipc_levels=(4,), cpc_levels=(4,),
                patent_ids=None, filters=None, analysis_id=None)
            specs = (cr.SectionSpec("fake", ("lifecycle",), fake_build),)
            cr.render_sections_all_profiles(ctx, specs)

        self.assertEqual(sorted(seen), ["ppt", "web"], f"應兩個 profile 各跑一次：{seen}")
        self.assertEqual(len(ctx.sections), 1,
                         "第二輪不得重複 append section（網頁會出現重複卡片）")

    def test_ppt_profile_runs_first(self):
        """PPT 先跑：它是既有行為，出錯時第二輪不該掩蓋第一輪的結果。"""
        seen: list[str] = []
        with TemporaryDirectory() as tmp:
            ctx = cr.ChartContext(
                run_dir=Path(tmp), ranking_limit=10, ipc_levels=(4,), cpc_levels=(4,),
                patent_ids=None, filters=None, analysis_id=None)
            specs = (cr.SectionSpec("fake", ("lifecycle",),
                                    lambda c: seen.append(cp.active_profile_name())),)
            cr.render_sections_all_profiles(ctx, specs)
        self.assertEqual(seen[0], "ppt")


if __name__ == "__main__":
    unittest.main()


class WebAssetResolutionTests(unittest.TestCase):
    """網頁報表要拿 web profile 的圖；舊版本沒有時退回原圖。

    ⚠ 退回**不是**可有可無的寬容：`.web.svg` 是 2026-08-09 才開始產的，
    在那之前的每一個報表版本都只有 PPT 尺寸的圖。不退回就是舊版本網頁全空。
    """

    def test_prefers_web_profile_when_present(self):
        self.assertEqual(
            cp.resolve_web_asset("lifecycle.svg", {"lifecycle.svg", "lifecycle.web.svg"}.__contains__),
            "lifecycle.web.svg")

    def test_falls_back_for_legacy_versions(self):
        self.assertEqual(cp.resolve_web_asset("lifecycle.svg", {"lifecycle.svg"}.__contains__),
                         "lifecycle.svg")

    def test_non_svg_untouched(self):
        """分群主題表等 HTML 產物沒有 profile 之分。"""
        self.assertEqual(cp.resolve_web_asset("cluster_topic_table.html",
                                              {"cluster_topic_table.html"}.__contains__),
                         "cluster_topic_table.html")

    def test_missing_file_returns_original(self):
        """兩個都不存在時原樣回傳，由呼叫端決定顯不顯示（不在這裡吞掉）。"""
        self.assertEqual(cp.resolve_web_asset("gone.svg", set().__contains__), "gone.svg")


class SingleWriteExitTests(unittest.TestCase):
    """SVG 只能從 `_write_svg` 出去。

    🔴 動因（2026-08-09 實測）：首版收斂漏了三支 renderer（matrix、
    year_bubble_matrix、opportunity_quadrant），它們仍直接 `write_text` 到原
    路徑——於是**第二輪的 web 內容覆寫了第一輪的 PPT 檔**。症狀是 PPT 圖的
    字級變成網頁的目標值，只有一支剛好在量字級的測試抓得到。

    ⚠ 新增 renderer 時忘記走這個出口不會有任何錯誤訊息，所以用原始碼掃描守。
    """

    SOURCE = (Path(__file__).resolve().parents[1] / "backend" / "app" / "reports"
              / "chart_runner.py").read_text(encoding="utf-8")

    def test_no_renderer_writes_svg_directly(self):
        import re

        offenders = []
        for line_no, line in enumerate(self.SOURCE.splitlines(), start=1):
            if "write_text" not in line or "json.dumps" in line:
                continue
            if "HTML" in line or "html_text" in line:      # HTML 產物無 profile 之分
                continue
            if re.search(r"\btarget\.write_text", line):   # _write_svg 自己
                continue
            offenders.append(f"{line_no}: {line.strip()}")
        self.assertEqual(offenders, [],
                         "有 renderer 繞過 _write_svg 直接寫檔——web 輪會覆寫 PPT 檔")


class WebAssetsStayOutOfPptIndexTests(unittest.TestCase):
    """web profile 的圖不得被登記成任何 report_key 的 artifact。

    🔴 動因：artifact_manifest 的 report_key 反查有一條前綴規則
    （`opportunity_quadrant_*` → `opportunity_quadrant`），`.web.svg` 一樣命中
    ——PPT 端的 ChartIndex 於是拿得到網頁尺寸的圖，可能直接放進簡報。

    ⚠ profile manifest 不受影響：它先用 `parse_profile_filename` 還原成原檔名
    再反查，傳進去的本來就不含 `.web`。
    """

    def test_web_variants_map_to_no_report(self):
        for name in ("annual_trend.web.svg", "lifecycle.web.svg",
                     "opportunity_quadrant_tech.web.svg",
                     "cluster_topic_table_effect.web.svg"):
            with self.subTest(name=name):
                self.assertEqual(cr.report_names_for_artifact(name), [],
                                 f"{name} 不該被登記成 PPT 可用的圖")

    def test_ppt_variants_still_map(self):
        """⚠ 對照組：原檔名的對應不得因此被改壞。"""
        self.assertEqual(cr.report_names_for_artifact("opportunity_quadrant_tech.svg"),
                         ["opportunity_quadrant"])
        self.assertEqual(cr.report_names_for_artifact("lifecycle.svg"), ["lifecycle"])
