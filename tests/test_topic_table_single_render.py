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
        """⚠ 機會／痛點矩陣是真的 SVG 圖，變體維持不動——本次只收斂表格。"""
        self.assertIn("render_opportunity_quadrant_svg", self.src)
        self.assertIn('variants.append({"label": f"機會矩陣', self.src,
                      "機會矩陣的變體被誤刪——它是圖不是表")


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
