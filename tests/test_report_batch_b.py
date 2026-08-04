"""批 B：受讓取得欄／移除最新受讓人排名／痛點條件化／左右 45-55（2026-07-29）。

## B1 受讓取得欄（A 方案）

使用者選 A：「排名表加受讓取得欄」。

現況 `applicant_ranking` 已有 `recent_assignee_count`（**轉出**幾件）與
`recent_assignee_display_names`（轉給誰），但**沒有反向**——YIXUAN 那列
看不到它「受讓取得 2 件」。實測資料：

    MARIO CONTENTI  專利 2  轉出 2 → YIXUAN
    YIXUAN          專利 1  轉出 0        ← 缺「受讓取得 2」

需新的聚合函式：反向計數（有多少列的 recent_assignee 等於本組分組鍵）。
現有 `_excl_group` 變體查的是「自己這列的欄位」，方向相反，做不到。

## B2 移除「最新受讓人排名」報表

使用者：「報表選項就不用有最新受讓人排名」。實測該報表只有 6 筆有值，
其中 3 筆是同公司大小寫不同（非真轉讓）——資訊量太低，已由 B1 涵蓋。

## B3 痛點四象限需市場資料

使用者：「有市場資料才能選分群相關三張報表，沒有就是備案」，後續修正為
「主題統計表與機會四象限可以選」——**只有痛點四象限**需要市場資料
（Y 軸是痛點嚴重度，無市場資料時全 unknown）。

## B4 左右 45/55

使用者：「可以恢復左數據右圖表的格式」「表 45%／圖 55%」。
⚠ 推翻本日稍早的 R3（圖滿寬、表移下方）——使用者看過實機後改變決定。
年度矩陣例外（layout="stacked"，見 test_year_matrix_pivot）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"


class B1AcquiredColumnTests(unittest.TestCase):
    """反向聚合：受讓取得。"""

    def test_aggregate_function_registered(self):
        from backend.app.reports.report_engine import AGGREGATE_FUNCTIONS

        self.assertIn("count_as_value_of", AGGREGATE_FUNCTIONS,
                      "缺反向計數聚合——受讓取得算不出來")

    def test_applicant_ranking_has_acquired_column(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        aliases = {a[2] for a in REPORT_DEFINITIONS["applicant_ranking"].aggregates}
        self.assertIn("acquired_count", aliases, "排名表沒有受讓取得欄")

    def test_label_is_chinese(self):
        from backend.app.reports.chart_runner import DATA_COLUMN_LABELS

        self.assertEqual(DATA_COLUMN_LABELS.get("acquired_count"), "受讓取得")


class B2RemoveAssigneeRankingTests(unittest.TestCase):
    """最新受讓人排名報表移除。"""

    def test_report_removed(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        self.assertNotIn("recent_assignee_ranking", REPORT_DEFINITIONS,
                         "使用者定案移除該報表（資訊量已由受讓取得欄涵蓋）")

    def test_no_dangling_reference(self):
        """移除後不得有殘留引用——否則 resolve_sections 會 fail loud。"""
        from backend.app.reports import chart_runner

        covered = {n for spec in chart_runner.SECTION_SPECS for n in spec.reports}
        self.assertNotIn("recent_assignee_ranking", covered,
                         "section registry 仍引用已移除的報表")


class B3PainPointRequiresMarketTests(unittest.TestCase):
    """痛點四象限需市場資料才能選；另兩張分群報表不受限。"""

    def test_definition_declares_requirement(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        # 🔴 2026-08-04：pain_point_quadrant 已整個刪除（使用者定案）。
        self.assertNotIn("pain_point_quadrant", REPORT_DEFINITIONS)
        for name in ("cluster_topic_table", "opportunity_quadrant"):
            with self.subTest(report=name):
                self.assertFalse(
                    getattr(REPORT_DEFINITIONS[name], "requires_market_data", False),
                    f"{name} 不需市場資料（使用者定案可以選）")

    def test_api_exposes_requirement(self):
        """前端要能據此禁用選項——欄位必須出現在 report-definitions 回應。"""
        src = (PROJECT_ROOT / "backend" / "app" / "api" / "reports.py").read_text(encoding="utf-8")
        self.assertIn("requires_market_data", src,
                      "API 沒輸出該旗標，前端無從判斷能不能選")


class B4SideBySideLayoutTests(unittest.TestCase):
    """左右分欄 45/55（2026-07-29 推翻稍早的圖滿寬）。

    🔴 2026-08-03 **再次翻回圖滿寬**：4 列的扁圖在 55% 欄裡被縮到軸標籤只剩 7.6px，
    實測看不清楚。⚠ 兩次的前提不同——07-29 時圖上的字是 13px，本次是 7.6px。
    測試改為條件式而非刪除：只要有人改回兩欄，45/55 這個比例就必須重新成立。
    """

    def test_ratio_45_55_when_side_by_side(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        container = " ".join(re.findall(r"\.report-single\s*\{([^}]*)\}", html))
        if "flex-direction: column" in container:
            self.skipTest("目前是單欄版面（圖滿寬、表格排下方），45/55 不適用")
        for sel, pct in ((".report-single-data", "45%"), (".report-single-chart", "55%")):
            m = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", html)
            self.assertIsNotNone(m, f"找不到 {sel}")
            with self.subTest(sel=sel):
                self.assertIn(pct, m.group(1), f"{sel} 應為 {pct}")

    def test_stacked_layout_supported(self):
        """年度矩陣要能走上下排列——前端需依 layout 切換。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("report-single-stacked", html,
                      "缺 stacked 版面樣式，年度矩陣的交叉表會被擠在 45% 欄")


if __name__ == "__main__":
    unittest.main()


class B1AcquiredCountCorrectnessTests(unittest.TestCase):
    """🔴 反向子查詢必須真的關聯外層分組（2026-07-29 實測抓到）。

    初版模板寫 `... IS NOT DISTINCT FROM NULLIF(BTRIM({group_col}::text), '')`，
    裸欄名在子查詢裡被 PostgreSQL 解析成 `_rev` 自己的欄位（最內層作用域優先），
    相關子查詢退化成**無關聯常數**——實測 37 列全部回同一個數字 2。

    這是最惡劣的一類 bug：SQL 不報錯、數字看起來合理（2 確實是某個真實數量），
    只有逐列核對才發現「每個人都受讓取得 2 件」不可能。

    正解（2026-07-29 實測）只有 5 家有受讓紀錄：
        DMASTER 運動 2、YIXUAN 2、SKI-ROW 1、OXEFIT 1、MOTIOFY 1

    ⚠ **來源表已於 2026-07-31 改變**：當時實測跑在
    `report_patent_applicant_expanded`（多申請人展開）上，
    但同日使用者定案「分析只計第一順位申請人（瀏覽顯示仍完整）」，
    `applicant_ranking.source_table` 因此改回 `REPORT_SOURCE_TABLE`
    ＝`derived_layer.report_patent_base`（見 report_definitions 該欄註解）。

    🔴 本檔下面那支測試沒跟著改，從 07-31 起就一直紅——**它紅的是自己過期，
    不是程式壞掉**。2026-08-03 排查技術債時一度被我判成「SQL 範圍缺陷」，
    追到定義才發現是測試表達了被推翻的舊規格。
    ⚠ 教訓：長期紅的測試要當成「規格與實作不一致」來追，
    不能因為「它一直紅」就長期 deselect——那會讓它從警訊退化成背景雜訊。
    """

    def test_group_col_is_table_qualified(self):
        """模板必須用 {table}.{group_col}，不能是裸欄名。"""
        from backend.app.reports.report_engine import AGGREGATE_FUNCTIONS

        tpl = AGGREGATE_FUNCTIONS["count_as_value_of"]
        self.assertIn("{table}.{group_col}", tpl,
                      "外層分組欄未限定表名——子查詢會退化成無關聯常數")

    def test_rendered_sql_has_distinct_scopes(self):
        """組出來的 SQL：子查詢用 _rev、外層用完整表名，兩者不得同名裸引用。

        ⚠ 外層表名**取自 definition**，不寫死——寫死就會像 07-31 那樣，
        來源表一改測試就紅在無關的地方，真正的迴歸反而被雜訊蓋掉。
        """
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS
        from backend.app.reports.report_engine import (
            build_aggregate_columns, qualified_table_name,
        )

        definition = REPORT_DEFINITIONS["applicant_ranking"]
        sql = build_aggregate_columns(definition)
        self.assertIn("_rev.", sql, "子查詢應以 _rev 限定")
        table = qualified_table_name(definition.source_table)
        group_col = definition.group_by[0]
        self.assertIn(f'{table}."{group_col}"', sql,
                      "外層分組欄應以完整表名限定，否則相關子查詢會退化成常數")
