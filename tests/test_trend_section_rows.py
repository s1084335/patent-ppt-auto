"""趨勢 section 必須帶合併後的六欄 rows（2026-08-17 實機發現）。

## 症狀

「專利申請與授權公告趨勢」的表格只有三欄（申請年份／專利件數／家族數），
**授權公告件數不見了**。

## 根因

`merge_annual_trend_rows` 產的是六欄（`year`／`application_count`／`授權公告件數`
／`family_count`／`topic_count`／`new_topic_count`），存進 `chart_rows.annual_trend`。
但顯示層自 2026-08-11 起**優先吃 `section["rows"]`**（`SECTION_PERSIST_KEYS` 的
註記寫明，起因是受理局交叉表），而趨勢 section 從未給 `rows`——於是退回
`report_key: application_trend` 的原始三欄報表。

⚠ 同型風險：任何「chart_rows 有、section 沒有」的表都會靜默少欄。
"""
from __future__ import annotations

import unittest
import unittest.mock

from backend.app.reports import chart_runner


class TrendSectionRowsTests(unittest.TestCase):
    def _section(self, *, with_topics: bool):
        """跑 `_build_trend_section`，回傳它 append 的 section。"""
        app_rows = [{"application_year": 2022, "patent_count": 61, "family_count": 47},
                    {"application_year": 2023, "patent_count": 60, "family_count": 52}]
        pub_rows = [{"授權公告年": 2022, "patent_count": 30}]

        class Ctx:
            def __init__(self):
                self.run_dir = __import__("pathlib").Path(
                    __import__("tempfile").mkdtemp())
                self.chart_rows = {}
                self.sections = []
                self.cluster_data = {"x": 1} if with_topics else None

            def report(self, name):
                if name == "application_trend":
                    return {"label_zh": "專利申請趨勢", "rows": app_rows}
                return {"label_zh": "專利授權公告趨勢", "rows": pub_rows}

        ctx = Ctx()
        # 圖不是本測標的，替換掉避免依賴渲染
        # （原本還 patch 了 `annual_topic_columns`，該函式已於 2026-08-18 隨
        #  「趨勢表不放技術主題」整段移除，故不再需要）
        with unittest.mock.patch.object(chart_runner, "render_line_chart") as render:
            chart_runner._build_trend_section(ctx)
        # 把 mock 掛回 ctx，讓標題類測試能取到實際傳給繪圖的參數
        # （本函式內部已經 patch 過，外層再 patch 一次拿不到呼叫紀錄）。
        ctx.render_line_chart_mock = render
        return ctx

    def test_section_carries_merged_rows(self):
        """🔴 核心斷言：section 要帶合併表，不能讓顯示層退回三欄原始報表。"""
        ctx = self._section(with_topics=False)
        section = ctx.sections[0]
        self.assertIn("rows", section, "趨勢 section 沒有 rows——顯示層會退回三欄原始報表")
        self.assertEqual(section["rows"], ctx.chart_rows["annual_trend"],
                         "section rows 必須與 chart_rows.annual_trend 同一份，不得各算一次")

    def test_publication_column_present(self):
        """授權公告件數必須在表裡（本 bug 使用者直接看到的症狀）。"""
        section = self._section(with_topics=False).sections[0]
        self.assertIn("授權公告件數", section["rows"][0])

    def test_family_column_present(self):
        section = self._section(with_topics=False).sections[0]
        self.assertIn("family_count", section["rows"][0])


class TrendSectionTitleTests(TrendSectionRowsTests):
    """合併章節有自己的名稱，不是兩個報表名用「與」串起來（使用者 2026-08-21）。

    ⚠ 原本標題是 `f'{申請 label_zh}與{公告 label_zh}'`，串出
    「專利申請趨勢與專利授權公告趨勢」——「趨勢」出現兩次。
    使用者要的是「專利申請與授權公告趨勢」。

    🔴 但**不得在 `chart_runner` 裡硬寫標題字串**：該檔註解已明訂
    「標題一律取自報表引擎定義的 label_zh（唯一來源）」。合併標題屬報表定義的知識，
    落點在 `report_definitions`，`chart_runner` 只消費。
    """

    def test_section_title_is_the_merged_name(self):
        from backend.app.reports.report_definitions import MERGED_TREND_LABEL_ZH

        section = self._section(with_topics=False).sections[0]
        self.assertEqual(section["title"], MERGED_TREND_LABEL_ZH)
        self.assertEqual(MERGED_TREND_LABEL_ZH, "專利申請與授權公告趨勢")

    def test_title_not_composed_from_two_labels(self):
        """回歸守門：不得再退回「A與B」的串接寫法。"""
        import inspect

        src = inspect.getsource(chart_runner._build_trend_section)
        self.assertNotIn(
            '["label_zh"]}與{', src,
            "標題又用兩個 label_zh 串接了——合併名稱有自己的定義處")

    def test_chart_title_matches_section_title(self):
        """圖內標題與卡片標題同一份，不得各寫一次。"""
        from backend.app.reports.report_definitions import MERGED_TREND_LABEL_ZH

        ctx = self._section(with_topics=False)
        render = ctx.render_line_chart_mock
        self.assertTrue(render.called, "沒有畫圖")
        chart_title = render.call_args[0][1]
        self.assertEqual(chart_title, MERGED_TREND_LABEL_ZH)
        self.assertEqual(chart_title, ctx.sections[0]["title"])


if __name__ == "__main__":
    import unittest.mock  # noqa: F401
    unittest.main()
