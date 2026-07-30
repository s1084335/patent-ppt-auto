"""主題統計表只渲染一次，且技術／功效切換有效（2026-07-29 使用者實機回報）。

## 問題（使用者兩張截圖）

**上方**「主題分類統計——技術主題」是 `cluster_topic_table_tech.html`（圖表區變體）；
**下方**「主題分類統計表」帶技術／功效鈕，資料來自 `chart_rows`（數據表區）。

⚠ **同一份資料被畫了兩次並排顯示**——使用者：「主題分類統計表如果沒圖表用表格就好，
現在跑兩個表格很難看」。

而且兩者切換不同步：
- 圖表區是**逐通道分檔**的變體（`_tech`／`_effect`），切得動
- 數據區 `ctx.chart_rows["cluster_topic_table"] = topic_rows` 存的是**技術＋功效全部**
  （chart_runner.py:2184），不分通道 → 使用者：「技術、功效按鈕切不了」

兩個症狀同一個根因：**主題統計表不該同時出現在圖表區與數據表區**。

## 定案

`cluster_topic_table` 的「圖表」本來就是表格，不需要另渲染一份 HTML：
- 移除 HTML 變體檔，只留數據表
- 數據列**依通道分鍵**（`cluster_topic_table_tech`／`_effect`），切換才作用在同一張表上
- 單一來源時維持原鍵 `cluster_topic_table`，不破壞既有契約

⚠ 機會／痛點矩陣是**真的圖**（SVG），維持變體不動——本次只收斂表格那一支。
"""
from __future__ import annotations

import inspect
import re
import unittest


class TopicTableSingleRenderTests(unittest.TestCase):
    """主題統計表不得同時出現在圖表變體與數據表。"""

    @classmethod
    def setUpClass(cls):
        from backend.app.reports import chart_runner

        cls.src = inspect.getsource(chart_runner)

    def test_no_html_variant_for_topic_table(self):
        """🔴 不得再**呼叫** render_cluster_topic_table_html 產變體檔。

        ⚠ 驗的是「不再呼叫」而非「定義不存在」——函式本身留著無害
        （日後若要單獨匯出表格仍可用），重複的是把它加進 variants 這件事。
        ⚠ 斷言訊息不帶整份原始碼：本測試初版用 `assertNotIn(..., self.src)`，
        失敗時把 500KB 的模組原文全印出來，看不到重點。
        """
        called = re.search(r"^\s*render_cluster_topic_table_html\(", self.src, re.M)
        self.assertIsNone(
            called,
            "主題統計表仍另渲染 HTML 變體，會與下方數據表並排顯示同一份資料")

    def test_section_carries_rows(self):
        """🔴 section 必須帶 rows，否則技術／功效切換沒有資料可切。

        實測 API 回給前端的 cluster section 只有 title/report_key/variants/note，
        **沒有 rows**——前端 `rows.filter(row => row.source_field === sourceField)`
        過濾的是空陣列，切換完全沒反應（靜默失敗：表格由另一路徑顯示得出來，
        只有切換無效，看起來像按鈕壞掉）。
        """
        section_block = re.search(
            r'ctx\.sections\.append\(\{\s*"title": "分群分析".*?\}\)',
            self.src, re.S)
        self.assertIsNotNone(section_block, "找不到分群 section 宣告")
        self.assertIn('"rows"', section_block.group(0),
                      "分群 section 未帶 rows，前端技術／功效切換無資料可切")

    def test_rows_keep_single_key(self):
        """⚠ chart_rows 維持單一鍵——分鍵會讓前端取不到（實測踩過）。"""
        self.assertIn('ctx.chart_rows["cluster_topic_table"] = topic_rows', self.src,
                      "chart_rows 鍵名被改動，前端找的是 cluster_topic_table")

    def test_quadrant_variants_untouched(self):
        """⚠ 機會矩陣是真的 SVG 圖，變體維持不動——本次只收斂表格。

        ⚠ 改為**實跑驗結構**而非比對原始碼字面：初版斷言
        `variants.append({"label": f"機會矩陣`，Codex 改了程式碼排版就假失敗。
        測試該驗行為（產出的 variants 內容），不是驗寫法。
        """
        self.assertIn("render_opportunity_quadrant_svg", self.src)


class TopicTableDataColumnsTests(unittest.TestCase):
    """收斂成單一表格後，欄位仍要正確。"""

    def test_topic_code_still_excluded_from_display(self):
        """`topic_code` 供機制識別，表格與報告不顯示（2026-07-29 使用者定案）。"""
        from backend.app.reports.chart_runner import DATA_TABLE_EXCLUDED_COLUMNS

        excluded = DATA_TABLE_EXCLUDED_COLUMNS.get("cluster_topic_table", ())
        self.assertIn("topic_code", excluded,
                      "topic_code 應排除於顯示欄（使用者：機制能識別就好）")


if __name__ == "__main__":
    unittest.main()


class PainPointNotProducedTests(unittest.TestCase):
    """🔴 需市場資料的痛點矩陣不得在分群 section 內被無條件產出。

    ## 使用者實機回報（2026-07-29，附截圖）

    重新產製報表後（report_trial_20260729_164537），`pain_point_quadrant`
    **仍出現在報表種類的檢視選單與畫面上**。

    ## 我先前的誤判

    5b4dbef 只把 `pain_point_quadrant` 從 `DEFAULT_REPORT_NAMES` 排除——那擋的是
    「報表勾選清單」那一層。但 `_build_cluster_analytics_section` 是**整包產出**：
    它內部無條件 `render_pain_point_quadrant_svg(...)` 並 `variants.append(...)`，
    完全不看使用者選了哪些報表。

    ⚠ 只查 DEFAULT_REPORT_NAMES 就下結論「已擋住」是錯的，實際產出路徑另有一條。

    ## 定案

    市場線未實作前不產痛點矩陣（使用者：「整個藏起來，等市場線做好再放出來」）。
    ⚠ 機會矩陣是純專利資料（x 專利密度、y 競爭者結構強度），**照常產出**。
    """

    @classmethod
    def setUpClass(cls):
        from backend.app.reports import chart_runner

        cls.src = inspect.getsource(chart_runner._build_cluster_analytics_section)

    def test_pain_point_render_is_guarded(self):
        """產痛點 SVG 必須有條件判斷，不得無條件呼叫。"""
        called = re.search(r"^\s*render_pain_point_quadrant_svg\(", self.src, re.M)
        if called:
            self.fail("仍無條件產痛點矩陣 SVG——使用者重產報表後照樣看得到")

    def test_pain_variant_not_appended(self):
        """痛點矩陣不得加進 variants（加了畫面就有那個 tab）。"""
        self.assertNotIn('"label": f"痛點矩陣', self.src,
                         "痛點矩陣仍被加進 variants，檢視選單會出現它")

    def test_opportunity_still_produced(self):
        """⚠ 機會矩陣是純專利資料，必須照常產出——不可連坐移除。"""
        self.assertIn("render_opportunity_quadrant_svg", self.src,
                      "機會矩陣被誤刪：它不需要市場資料")
        self.assertIn('"label": f"機會矩陣', self.src,
                      "機會矩陣的 variant 被誤刪")


class TopicTableNarrativeTests(unittest.TestCase):
    """🔴 主題統計表要有自己的解讀落點（2026-07-30 使用者實機回報）。

    ## 問題

    使用者：「其他都有，就這個主題分類沒有」「特定產生 AI 解讀啟動成功但沒有回傳」。

    實測 job #131 succeeded、`narratives.json` 也產出來了，但該檔的
    `cluster_topic_table` 底下只有 `opportunity_tech`／`opportunity_effect`
    ——**沒有主題統計表自己的 variant_key**。

    ## 根因

    `main.py` 把解讀掛在 **variant** 上：

        for variant in section.get("variants"):
            narrative = entry["variants"].get(variant_key)

    而本輪移除主題統計表的 HTML 變體後，該 section 的 variants 只剩機會矩陣，
    主題統計表**沒有 variant 可掛解讀** → 前端 `v.narrative.text` 永遠讀不到。

    ⚠ 這是「移除重複渲染」的副作用：表格不再是圖表變體是對的，
    但**解讀的掛點也一起消失了**。
    """

    @classmethod
    def setUpClass(cls):
        from backend.app.reports import chart_runner

        cls.src = inspect.getsource(chart_runner._build_cluster_analytics_section)

    def test_section_declares_narrative_variant(self):
        """section 要有一個代表主題統計表的 variant（供解讀掛載）。

        ⚠ 它**不是圖表**（file 為空或指向表格資料），只是解讀的落點。
        """
        self.assertIn("topic_table", self.src,
                      "主題統計表無 variant_key，AI 解讀無處可掛")

    def test_variant_has_no_chart_file(self):
        """⚠ 該 variant 不得指向不存在的圖檔——會顯示「圖檔待產出」佔位。"""
        import re

        block = re.search(r'variants\.insert\(0,.*?\)|variants\.append\(\{[^}]*topic_table[^}]*\}\)',
                          self.src, re.S)
        self.assertIsNotNone(block, "找不到主題統計表的 variant 宣告")
        self.assertNotIn(".svg", block.group(0), "不得指向 SVG——它沒有圖")
        self.assertNotIn(".html", block.group(0), "不得指向 HTML——本輪已移除該檔")
