"""圖表單一來源契約（unify-chart-source，2026-08-12 使用者定案）。

## 定案

「HTML 與 PPT 從同套來源接各自流程」：引擎只產**一份** SVG（WEB 尺寸、
既有原檔名），網頁原樣顯示、簡報端（deck skill）自行 refit 字級。
PPT 預放大鏈（雙輪渲染、`.web` 中綴、profile_manifest、resolve_ppt_asset）
全數退場——其消費者已隨 2026-08-10 PPT 交付線移除而消滅（實碼盤點零呼叫）。

## 本檔鎖的不復活契約

1. 預設 sizing＝WEB（15px＝11.25pt、canvas 1180），寫入**原檔名**。
2. 渲染單輪：不再產 `.web.svg`、不再產 `profile_manifest.json`。
3. `resolve_ppt_asset` 死碼移除。
4. `resolve_web_asset` 雙路徑保留：舊版本（原檔＝PPT 尺寸＋`.web.svg`）用
   `.web.svg`；新版本（僅原檔）退原檔——新舊版本零遷移都顯示正確。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class SingleProfileSizingTests(unittest.TestCase):
    def test_runner_binds_web_sizing(self):
        """唯一 sizing＝WEB：畫布 1180、資料字級 11.25pt（15px@96dpi）。

        （active_sizing 轉手層已併掉——chart_runner 直接綁 chart_sizing.WEB，
        本測驗綁定結果而非轉手函式。）
        """
        from backend.app.reports import chart_runner as cr
        from backend.app.reports.chart_sizing import WEB

        self.assertEqual(cr.CHART_CANVAS_WIDTH, WEB.canvas_width)
        self.assertEqual(cr.CHART_DATA_TARGET_PT, 11.25)
        self.assertEqual(cr.CHART_NOTE_TARGET_PT, 11.25)

    def test_chart_scale_is_identity(self):
        """單一來源不補償 PPT 圖框縮放——scale 恆 1.0（web 既有語意成為唯一語意）。"""
        from backend.app.reports.chart_runner import chart_scale

        self.assertEqual(chart_scale(949.0, 460.0), 1.0)
        self.assertEqual(chart_scale(1180.0, 560.0), 1.0)

    def test_rendered_chart_uses_web_canvas_and_font(self):
        """行為面：實際畫一張長條圖，SVG 落在**原檔名**、寬=1180、資料字級 15px。"""
        from backend.app.reports import chart_runner as cr

        rows = [{"name": f"公司{i:02d}", "patent_count": 30 - i} for i in range(5)]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ranking.svg"
            cr.render_bar_chart(p, "測試", rows, "name")
            self.assertTrue(p.exists(), "SVG 應寫入原檔名")
            self.assertFalse((Path(tmp) / "ranking.web.svg").exists(),
                             "不得再產 .web 中綴副本")
            svg = p.read_text(encoding="utf-8")
            self.assertIn('width="1180"', svg, "畫布應為 WEB 尺寸 1180")
            self.assertIn('font-size="15', svg, "資料字級應為 15px（11.25pt）")


class SingleRenderPassTests(unittest.TestCase):
    def test_multi_profile_render_loop_is_gone(self):
        """雙輪渲染退場——`render_sections_all_profiles` 不復活。"""
        from backend.app.reports import chart_runner

        self.assertFalse(hasattr(chart_runner, "render_sections_all_profiles"),
                         "雙 profile 渲染迴圈應隨單一來源退場")

    def test_profile_manifest_is_gone(self):
        """profile_manifest.json 零讀者（實碼盤點）——產生端一併退場。"""
        from backend.app.reports import chart_runner

        self.assertFalse(hasattr(chart_runner, "build_profile_manifest"))
        self.assertFalse(hasattr(chart_runner, "PROFILE_MANIFEST_NAME"))

    def test_resolve_ppt_asset_is_gone(self):
        """resolve_ppt_asset 全庫零呼叫（chart_bundle／build_ppt 已刪）——移除。"""
        from backend.app.reports import chart_profiles

        self.assertFalse(hasattr(chart_profiles, "resolve_ppt_asset"))


class ResolveWebAssetCompatTests(unittest.TestCase):
    """舊版本零遷移的關鍵：resolve_web_asset 的 fallback 語意原樣保留。"""

    def _resolve(self, file_name, existing):
        from backend.app.reports.chart_profiles import resolve_web_asset

        return resolve_web_asset(file_name, lambda f: f in existing)

    def test_old_version_prefers_web_infix(self):
        """舊版本目錄（原檔＝PPT 尺寸、另有 .web.svg）→ 用 .web.svg。"""
        self.assertEqual(
            self._resolve("ranking.svg", {"ranking.svg", "ranking.web.svg"}),
            "ranking.web.svg")

    def test_new_version_falls_back_to_plain(self):
        """新版本目錄（僅原檔，內容已是 WEB 尺寸）→ 退原檔。"""
        self.assertEqual(self._resolve("ranking.svg", {"ranking.svg"}), "ranking.svg")

    def test_non_svg_untouched(self):
        self.assertEqual(self._resolve("topics.html", {"topics.html"}), "topics.html")


if __name__ == "__main__":
    unittest.main()
