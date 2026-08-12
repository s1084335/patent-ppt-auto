"""交付用 HTML 報表（版本目錄 `index.html`）的章節式結構契約。

## 為什麼有這支

匯出的 HTML 檔自 2026-08-10 起是唯一交付物，但版面一直沿用「報表引擎順手產的
索引頁」：實測 9 章 8080px（8.1 螢幕）、**完全沒有導覽**、圖**原尺寸顯示**
（1180×560，圖內字 15.1px＝與正文 16px 同級）、順序是「表→圖→解讀」把唯一的
分析產出排在最後。2026-08-12 使用者定案改為章節式（change:
restructure-html-report-export，版面示意已確認）。

## 契約（本檔守的四件事）

1. 頂部章節導覽：項數＝章節數，錨點對得上每章 `id="sec-{report_key}"`
   ——⚠ 章節名直接取 `section["title"]`，不得另建第二份對照表。
2. 每章 DOM 順序：**圖 → 數據表 → 解讀**（2026-08-12 使用者定案；現行為 表→圖→解讀）。
3. 圖以固定高度縮圖呈現（CSS `height:400px`），可展開原尺寸。
4. 數據表預設 5 列（現行 20 列全展開）；⚠ 展開上限仍是 20 列
   ——2026-07-21 定案「不讓人看百筆數據」不變，本次只改**預設密度**。

另守「離線可開」：檔內不得引用任何外部資源（自包單檔匯出的前提）。
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner


def _sections() -> list[dict]:
    """兩章 fixture：一章單變體有 12 列數據，一章雙變體無數據（涵蓋 toggle 路徑）。"""
    return [
        {
            "title": "專利申請趨勢與專利授權公告趨勢",
            "report_key": "annual_trend",
            "note": "測試註記",
            "rows": [{"application_year": 2010 + i, "patent_count": i} for i in range(12)],
            "variants": [
                {"label": "預設", "variant_key": "default", "file": "annual_trend.svg"},
            ],
        },
        {
            "title": "IPC 主分類分布",
            "report_key": "ipc_main_distribution",
            "rows": [],
            "variants": [
                {"label": "4 階", "variant_key": "L4", "file": "ipc_L4.svg"},
                {"label": "5 階", "variant_key": "L5", "file": "ipc_L5.svg"},
            ],
        },
    ]


def _render(meta: dict | None = None) -> str:
    """在暫存版本目錄渲染一次 index.html 並回傳內容。"""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "report_trial_20260812_000000"
        run_dir.mkdir()
        for name in ("annual_trend.svg", "ipc_L4.svg", "ipc_L5.svg"):
            (run_dir / name).write_text("<svg xmlns='http://www.w3.org/2000/svg'/>",
                                        encoding="utf-8")
        path = run_dir / "index.html"
        chart_runner.render_index(path, _sections(), meta or {"workspace": "滑雪機"})
        return path.read_text(encoding="utf-8")


class ChapterNavigationTests(unittest.TestCase):
    """① 章節導覽——現行完全沒有，讀者只能一路捲。"""

    def test_nav_covers_every_section(self):
        html = _render()
        hrefs = re.findall(r'<a[^>]+href="#(sec-[^"]+)"', html)
        self.assertEqual(hrefs, ["sec-annual_trend", "sec-ipc_main_distribution"],
                         "導覽項應涵蓋全部章節且順序一致")

    def test_every_anchor_target_exists(self):
        html = _render()
        for key in ("annual_trend", "ipc_main_distribution"):
            with self.subTest(key=key):
                self.assertIn(f'id="sec-{key}"', html, "錨點目標必須存在，否則點了不動")

    def test_nav_label_reuses_section_title(self):
        """⚠ 章節名取自 section['title']，不得另建第二份對照表（同一知識單一落點）。"""
        html = _render()
        nav = re.search(r'<nav[^>]*>.*?</nav>', html, re.S)
        self.assertIsNotNone(nav, "應有 <nav> 導覽區塊")
        self.assertIn("專利申請趨勢與專利授權公告趨勢", nav.group(0))
        self.assertIn("IPC 主分類分布", nav.group(0))

    def test_sticky_nav_does_not_cover_heading(self):
        """導覽常駐時，跳轉後標題不得被蓋住——靠 scroll-margin-top，不是靠運氣。

        ⚠ 斷言必須綁在 `.report-section` 的**規則內**：first pass 只斷言
        「html 裡有 scroll-margin-top」，結果抓到 CSS 註解裡的同名字串而**假綠**，
        實物驗收才發現跳轉後標題仍被導覽蓋住（章節頂端 y=0）。
        """
        html = _render()
        rule = re.search(r"\.report-section\s*\{[^}]*\}", html)
        self.assertIsNotNone(rule, "找不到 .report-section 樣式")
        self.assertIn("scroll-margin-top", rule.group(0),
                      "scroll-margin-top 必須寫在 .report-section 規則內（註解不算）")
        # ⚠ 偏移量必須動態：導覽列高度隨章節數與視窗寬度變（chip 換行）。
        # 章節導覽改 16px 後實測 42px → 102px，寫死 56px 就再度被蓋住。
        self.assertIn("--nav-h", rule.group(0), "偏移量應綁動態變數而非寫死")
        script = re.search(r"<script>.*?</script>", html, re.S).group(0)
        self.assertIn("--nav-h", script, "JS 必須量實際導覽高度寫回變數")
        self.assertIn("resize", script, "視窗改變寬度時導覽會換行，偏移要跟著更新")


class ChapterOrderTests(unittest.TestCase):
    """② 每章順序：**圖 → 數據表 → 解讀**（2026-08-12 使用者定案）。

    現行是 表 → 圖 → 解讀。改動＝把數據表移到圖之後、解讀之前。
    ⚠ 解讀原本掛在圖的 chart-panel **內**（逐變體）；移到表之後就離開了 panel，
    因此必須自帶同一組 `data-group`，才能隨 L4／L5 切換鈕連動
    ——否則會出現「切到 L5、讀著 L4 解讀」的靜默錯配。
    """

    def test_chart_then_table_then_reading(self):
        html = _render()
        chapter = re.search(
            r'id="sec-annual_trend".*?(?=id="sec-ipc_main_distribution"|</body>)', html, re.S)
        self.assertIsNotNone(chapter, "找不到第一章區塊")
        body = chapter.group(0)
        i_chart = body.find("chart-media")
        i_table = body.find("data-table-wrap")
        i_read = body.find("explanation")
        self.assertTrue(0 <= i_chart < i_table < i_read,
                        f"順序應為 圖<表<解讀，實得 {i_chart},{i_table},{i_read}")

    def test_reading_still_switches_with_variant(self):
        """多變體章節：解讀離開 panel 後仍須隨切換鈕連動。"""
        html = _render()
        chapter = re.search(r'id="sec-ipc_main_distribution".*?</body>', html, re.S).group(0)
        exps = re.findall(r'<div class="explanation[^"]*"[^>]*data-group="([^"]+)"', chapter)
        self.assertEqual(len(exps), 2, "雙變體章節應有兩段各自的解讀")
        self.assertEqual(len(set(exps)), 1, "兩段解讀應屬同一個切換群組")

    def test_title_is_not_hardcoded_english(self):
        """標題應反映實際報表；現行寫死 'Patent Report'。"""
        html = _render(meta={"workspace": "滑雪機"})
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        self.assertIsNotNone(h1)
        self.assertNotEqual(h1.group(1).strip(), "Patent Report")
        self.assertIn("滑雪機", h1.group(1))


class ChartAsEvidenceTests(unittest.TestCase):
    """③ 圖降為證據：固定高度縮圖＋可展開原尺寸。"""

    def test_chart_is_width_capped_not_height_forced(self):
        """🔴 圖以**固定寬度**縮圖，高度自動——不得強制高度。

        first pass 用 `height:340px; max-width:100%`，實測對扁圖
        （IPC L4 = 1180×210）兩條規則同時觸發而**縱向拉伸變形**（1490×340，
        比例 5.62:1 壓成 4.38:1），且比原尺寸放大 26%、圖內字 19.1px 反而大於正文。
        限寬才能讓所有圖縮放比一致、扁圖自然變矮。
        """
        html = _render()
        css = re.search(r"\.chart-media\s*\{[^}]*\}", html)
        self.assertIsNotNone(css, "找不到 .chart-media 樣式")
        rule = re.sub(r"\s+", " ", css.group(0))
        self.assertRegex(rule, r"max-width:\s*860px", "圖應限寬 860px（＝圖內字 11px）")
        self.assertRegex(rule, r"height:\s*auto", "高度必須自動，否則扁圖會被拉伸變形")
        self.assertNotRegex(rule, r"height:\s*\d+px", "不得強制固定高度")

    def test_font_scale_matches_spec(self):
        """字級（2026-08-12 使用者指定）：正文與表格 14px、章節導覽 16px。

        ⚠ 圖內字（11px）不在 CSS 裡——SVG 內寫死 15.1px，顯示字級由
        `.chart-media` 的寬度決定，見上一支測試。
        """
        html = _render()
        for selector, size in (("body", "14px"), (r"\.data-table-wrap table", "14px"),
                               (r"\.navchip", "16px")):
            with self.subTest(selector=selector):
                rule = re.search(selector + r"\s*\{[^}]*\}", html)
                self.assertIsNotNone(rule, f"找不到 {selector} 樣式")
                self.assertRegex(re.sub(r"\s+", " ", rule.group(0)),
                                 r"font-size:\s*" + size)

    def test_chart_is_centered(self):
        """縮圖比版面窄，靠左會讓右側空一大塊（2026-08-12 使用者「放正中間」）。"""
        css = re.search(r"\.chart-media\s*\{[^}]*\}", _render())
        self.assertRegex(css.group(0), r"margin:\s*0 auto", "縮圖應水平置中")

    def test_chart_can_expand_to_full_size(self):
        html = _render()
        self.assertRegex(html, r"\.chart-media\.zoom\s*\{", "應有展開原尺寸的樣式")
        self.assertIn("chart-media", re.search(r"<script>.*?</script>", html, re.S).group(0),
                      "展開需就地綁定，且 JS 必須內嵌（單檔離線可開）")


class DataTableDensityTests(unittest.TestCase):
    """④ 數據表預設 5 列；⚠ 展開上限仍 20 列（2026-07-21 定案不變）。"""

    def test_default_shows_five_rows(self):
        html = _render()
        chapter = re.search(r'id="sec-annual_trend".*?(?=id="sec-ipc)', html, re.S).group(0)
        visible = re.findall(r"<tr(?![^>]*folded)[^>]*>", chapter)
        # 表頭 1 列 ＋ 資料 5 列 ＋ 總計 1 列
        self.assertEqual(len(visible), 7,
                         f"預設應只顯示 5 筆資料列（＋表頭＋總計），實得 {len(visible)} 個 <tr>")

    def test_remaining_rows_are_foldable(self):
        html = _render()
        self.assertIn("folded", html, "第 6 列起應可收合")
        self.assertRegex(html, r"共\s*12\s*列|總列數\s*12", "summary 應標示總列數")


class OfflineSelfContainedTests(unittest.TestCase):
    """離線可開：自包單檔匯出的前提，不得引用任何外部資源。"""

    def test_no_external_resource_reference(self):
        html = _render()
        externals = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
        self.assertEqual(externals, [], f"不得引用外部資源：{externals[:3]}")


if __name__ == "__main__":
    unittest.main()
