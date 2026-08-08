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
    def test_identity_and_profile_in_filename(self):
        name = cp.profile_filename("applicant_ranking", "default", "ppt")
        self.assertIn("applicant_ranking", name)
        self.assertTrue(name.endswith(".ppt.svg"), name)

    def test_web_and_ppt_names_differ(self):
        self.assertNotEqual(cp.profile_filename("lifecycle", "default", "web"),
                            cp.profile_filename("lifecycle", "default", "ppt"))

    def test_parse_roundtrip(self):
        name = cp.profile_filename("cpc_main_distribution", "L4", "ppt")
        self.assertEqual(cp.parse_profile_filename(name),
                         ("cpc_main_distribution", "L4", "ppt"))


class ManifestTests(unittest.TestCase):
    def test_manifest_binds_identity_profile_and_checksum(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            for profile in ("web", "ppt"):
                (run_dir / cp.profile_filename("lifecycle", "default", profile)).write_text(
                    f"<svg>{profile}</svg>", encoding="utf-8")
            manifest = cp.build_profile_manifest(run_dir, version="v1")
            entry = manifest["charts"]["lifecycle:default"]
            self.assertEqual(sorted(entry["profiles"]), ["ppt", "web"])
            self.assertEqual(entry["version"], "v1")
            self.assertTrue(entry["profiles"]["ppt"]["checksum"])
            self.assertNotEqual(entry["profiles"]["ppt"]["checksum"],
                                entry["profiles"]["web"]["checksum"])


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

        path = tmp / cp.profile_filename("applicant_ranking", "default", profile)
        with cp.profile_context(profile):
            render_bar_chart(path, "主要申請人排名", self.ROWS, "applicant_display_name")
        return path.read_text(encoding="utf-8")

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
