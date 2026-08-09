"""P3：web／PPT 雙 rendering profile（openspec separate-web-and-ppt-chart-profiles）。

同一 chart identity、同一份資料與色彩語意，**只允許尺寸／DPI／字級／必要邊距不同**。

⚠ 為什麼不做兩套 engine（Non-goals 明列）：兩套必然漂移——同一張圖在網頁與
PPT 說不同的話，是本專案已重複踩過四次的「同一份知識兩處落點」。
⚠ 缺少或 identity 不符的 PPT profile 一律 fail loud，不得退回舊圖或讓 CLI 自選。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.reports import chart_profiles as cp


class ProfileSpecTests(unittest.TestCase):
    def test_two_profiles_only(self):
        self.assertEqual(sorted(cp.PROFILES), ["ppt", "web"])

    def test_profiles_differ_only_in_presentation(self):
        """兩個 profile 的差異只能是尺寸／DPI／字級／邊距——不得有資料或色彩欄。"""
        allowed = {"width_px", "scale", "min_font_pt", "dpi", "margin_px", "label"}
        for name, spec in cp.PROFILES.items():
            with self.subTest(profile=name):
                self.assertTrue(set(spec) <= allowed,
                                f"{name} profile 出現非呈現層欄位：{set(spec) - allowed}")

    def test_ppt_profile_has_larger_min_font(self):
        """PPT 圖會被縮進圖框（縮兩次），最小字級門檻必須比 web 高。"""
        self.assertGreater(cp.PROFILES["ppt"]["min_font_pt"],
                           cp.PROFILES["web"]["min_font_pt"])


class ArtifactNamingTests(unittest.TestCase):
    """檔名規則（2026-08-09 契約回寫）。

    原契約是 `report_key__variant.profile.svg`（identity 寫在檔名裡），被現實
    推翻兩點：

    1. `annual_trend.svg` **同時**是 `application_trend` 與 `publication_trend`
       兩個 report_key 的圖——一檔一 identity 的模型表達不了。
    2. 既有檔名與 report_key 本來就不同名（`country_distribution` 的圖叫
       `jurisdiction_distribution.svg`），改名會波及 artifact_manifest、
       ChartIndex、build_ppt 與所有既有報表版本。

    ⇒ 改為「**PPT profile 沿用既有檔名，web profile 加 `.web` 中綴**」。
    identity 仍是 `report_key:variant`（chart_bundle 選圖用的就是它），
    但由 manifest 維護 identity→path 的對應，不寫進檔名。
    """

    def test_ppt_profile_keeps_existing_filename(self):
        """⚠ PPT 檔名不得改變：既有報表版本與 build_ppt 的 ChartIndex 都靠它。"""
        self.assertEqual(cp.profile_filename("jurisdiction_distribution.svg", "ppt"),
                         "jurisdiction_distribution.svg")

    def test_web_profile_adds_infix(self):
        self.assertEqual(cp.profile_filename("jurisdiction_distribution.svg", "web"),
                         "jurisdiction_distribution.web.svg")

    def test_web_and_ppt_names_differ(self):
        self.assertNotEqual(cp.profile_filename("lifecycle.svg", "web"),
                            cp.profile_filename("lifecycle.svg", "ppt"))

    def test_parse_roundtrip(self):
        for profile in ("web", "ppt"):
            with self.subTest(profile=profile):
                name = cp.profile_filename("cpc_main_distribution_L4.svg", profile)
                self.assertEqual(cp.parse_profile_filename(name),
                                 ("cpc_main_distribution_L4.svg", profile))

    def test_unknown_profile_fails_loud(self):
        with self.assertRaises(cp.ChartProfileError):
            cp.profile_filename("lifecycle.svg", "print")


class ManifestTests(unittest.TestCase):
    """identity → 各 profile 的對應。

    ⚠ manifest 建在 `chart_runner`：identity 要靠「檔名 → report_names」對照表
    （`ARTIFACT_REPORT_NAMES`）反查，那張表是 chart_runner 的；放這裡會反向
    相依而循環 import。
    """

    def test_manifest_binds_identity_profile_and_checksum(self):
        # ⚠ 2026-08-09 契約變更：原本取樣用 `lifecycle.svg`，`lifecycle` 報表已由
        # 使用者裁決刪除、同時從 CHART_FILE_REPORTS 移除，identity 反查會落空。
        # 改用仍在對照表裡的 `applicant_ranking.svg`（同樣是一檔對一報表）。
        from backend.app.reports.chart_runner import build_profile_manifest

        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            for profile in ("web", "ppt"):
                (run_dir / cp.profile_filename("applicant_ranking.svg", profile)).write_text(
                    f"<svg>{profile}</svg>", encoding="utf-8")
            manifest = build_profile_manifest(run_dir, version="v1")
            entry = manifest["charts"]["applicant_ranking:default"]
            self.assertEqual(sorted(entry["profiles"]), ["ppt", "web"])
            self.assertEqual(entry["version"], "v1")
            self.assertEqual(entry["profiles"]["ppt"]["path"], "applicant_ranking.svg")
            self.assertEqual(entry["profiles"]["web"]["path"], "applicant_ranking.web.svg")
            self.assertTrue(entry["profiles"]["ppt"]["checksum"])
            self.assertNotEqual(entry["profiles"]["ppt"]["checksum"],
                                entry["profiles"]["web"]["checksum"])

    def test_one_file_serving_two_report_keys_appears_under_both(self):
        """⚠ `annual_trend.svg` 同時是申請趨勢與公告趨勢的圖——兩個 identity
        都要指得到它。這正是原「一檔一 identity」命名契約表達不了的情形。"""
        from backend.app.reports.chart_runner import build_profile_manifest

        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            for profile in ("web", "ppt"):
                (run_dir / cp.profile_filename("annual_trend.svg", profile)).write_text(
                    f"<svg>{profile}</svg>", encoding="utf-8")
            charts = build_profile_manifest(run_dir, version="v1")["charts"]
            for identity in ("application_trend:default", "publication_trend:default"):
                with self.subTest(identity=identity):
                    self.assertEqual(charts[identity]["profiles"]["ppt"]["path"],
                                     "annual_trend.svg")

    def test_variant_suffix_becomes_variant_key(self):
        """`ipc_main_distribution_L4.svg` → identity `ipc_main_distribution:L4`。"""
        from backend.app.reports.chart_runner import build_profile_manifest

        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "ipc_main_distribution_L4.svg").write_text("<svg/>", encoding="utf-8")
            charts = build_profile_manifest(run_dir, version="v1")["charts"]
            self.assertIn("ipc_main_distribution:L4", charts)


class ResolutionTests(unittest.TestCase):
    MANIFEST = {"version": "v1", "charts": {
        "lifecycle:default": {"version": "v1", "profiles": {
            "web": {"path": "lifecycle.web.svg", "checksum": "w1"},
            "ppt": {"path": "lifecycle.ppt.svg", "checksum": "p1"},
        }}}}

    def test_selected_web_chart_resolves_to_ppt_asset(self):
        """使用者在網頁選圖 → 交給 CLI 的是同 identity 的 PPT profile。"""
        asset = cp.resolve_ppt_asset("lifecycle:default", self.MANIFEST)
        self.assertEqual(asset["path"], "lifecycle.ppt.svg")

    def test_missing_ppt_profile_fails_loud(self):
        manifest = {"version": "v1", "charts": {"lifecycle:default": {
            "version": "v1", "profiles": {"web": {"path": "a.svg", "checksum": "w"}}}}}
        with self.assertRaises(cp.ChartProfileError):
            cp.resolve_ppt_asset("lifecycle:default", manifest)

    def test_unknown_identity_fails_loud(self):
        with self.assertRaises(cp.ChartProfileError):
            cp.resolve_ppt_asset("nope:default", self.MANIFEST)

    def test_version_mismatch_fails_loud(self):
        """過期 profile 不得混用（identity 對但版本不符）。"""
        with self.assertRaises(cp.ChartProfileError):
            cp.resolve_ppt_asset("lifecycle:default", self.MANIFEST, expect_version="v2")


class SemanticParityTests(unittest.TestCase):
    """同一份資料與色彩語意——兩 profile 的差異只在呈現。"""

    # ⚠ 名稱不用單字母：SVG 屬性裡到處是 A/B，index() 會誤中（首版踩到）。
    ROWS = [{"applicant_display_name": "甲公司", "patent_count": 5},
            {"applicant_display_name": "乙公司", "patent_count": 3}]

    def _render(self, profile: str, tmp: Path) -> str:
        from backend.app.reports.chart_runner import render_bar_chart

        # ⚠ 一律傳**原路徑**：profile 分流由 chart_runner 的寫檔出口負責，
        # 呼叫端再自己套一次 profile_filename 會寫成 `.web.web.svg`。
        with cp.profile_context(profile):
            render_bar_chart(tmp / "applicant_ranking.svg", "主要申請人排名",
                             self.ROWS, "applicant_display_name")
        return (tmp / cp.profile_filename("applicant_ranking.svg", profile)).read_text(
            encoding="utf-8")

    def test_same_data_and_order_in_both_profiles(self):
        with TemporaryDirectory() as tmp:
            web = self._render("web", Path(tmp))
            ppt = self._render("ppt", Path(tmp))
        for svg in (web, ppt):
            self.assertLess(svg.index("甲公司"), svg.index("乙公司"), "兩 profile 排序必須一致")
            self.assertIn("5", svg)
            self.assertIn("3", svg)

    def test_profiles_differ_in_size(self):
        with TemporaryDirectory() as tmp:
            web = self._render("web", Path(tmp))
            ppt = self._render("ppt", Path(tmp))
        self.assertNotEqual(web, ppt, "兩 profile 應有尺寸／字級差異")


if __name__ == "__main__":
    unittest.main()
