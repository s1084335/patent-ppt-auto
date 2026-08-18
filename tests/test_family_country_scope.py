"""受理局頁的家族註記不得把「各國家族數」相加當成「家族總數」（tasks §1.5）。

## 實測推翻了 tasks 的記載

tasks 原本寫「受理局頁家族註記 187 vs 實際 48」，判定為「母體沒接」。
2026-08-18 實測**不是那樣**：

| workspace | 註記現在顯示（各國相加） | DISTINCT 家族 |
|---|---|---|
| 滑雪機 | **46** | **40** |
| 割草機 | 159 | 144 |

母體**本來就有接**——`ChartContext.report()` 一律傳 `patent_ids`，而
`supports_patent_ids=False` 的語意不是「忽略母體」，是「家族層報表：母體會被
`build_family_scope_clause` 翻譯成家族集合」（`report_engine.py` 353–360）。
⚠ `report_family_country` **沒有 `patent_id` 欄**，把該旗標改成 True 會產生
`patent_id = ANY(...)` 直接讓 SQL 壞掉。

真正的缺陷是**加總錯誤**：報表依國家 group by，每列是「該國有幾個家族」；
相加等於同一個家族跨幾國就算幾次。滑雪機 40 個家族分布在 4 國 → 相加得 46。

⚠ 這個錯數字已經傳進 deepen 的文件（記載「家族 48／存活 46」的那個 46）。

## 本節怎麼修

不在這裡決定家族口徑（那是 §2.2／§2.4 的事，滑雪機有 40／46／48 三個數字各有來源）。
本節只要求：**不得用相加的結果宣稱是家族總數**。註記要嘛講清楚它是「佈局點」，
要嘛不講總數。
"""
from __future__ import annotations

import inspect
import unittest

from backend.app.reports import chart_runner
from backend.app.reports.report_definitions import REPORT_DEFINITIONS


class FamilyReportStaysFamilyLevelTests(unittest.TestCase):
    """守住「不要把家族層報表誤改成 patent 層」——那會讓 SQL 壞掉。"""

    def test_family_country_layout_is_family_level(self):
        definition = REPORT_DEFINITIONS["family_country_layout"]
        self.assertFalse(
            definition.supports_patent_ids,
            "family_country_layout 被改成 patent 層了——"
            "來源表 report_family_country 沒有 patent_id 欄，SQL 會直接壞掉。"
            "母體是靠 build_family_scope_clause 翻譯成家族集合，不是靠這個旗標")

    def test_family_scope_is_applied_when_patent_ids_given(self):
        from backend.app.reports import report_engine

        definition = REPORT_DEFINITIONS["family_country_layout"]
        sql, params = report_engine.build_report_sql(
            definition, filters=None, limit=None, patent_ids=[11, 22])
        self.assertRegex(
            str(sql), r'(?i)"family_id"\s+IN\s*\(',
            "家族範圍子句不見了——報表會回全庫")
        self.assertIn("patent_ids", params)


class NoteDoesNotSumAcrossCountriesTests(unittest.TestCase):
    """🔴 核心：不得把各國家族數相加當成家族總數。"""

    @staticmethod
    def _executable_lines(src: str) -> str:   # noqa: D401 — 委派共用工具
        """剝掉 `#` 註解，只留會被執行的程式。

        ⚠ 2026-08-18：本測試第一版就被**自己的修正註解**餵飽——註解裡引述了舊字串
        「存活家族共」，測試照樣紅。與同日 migration 測試被 `-- UNION` 註解騙過
        是同一型：**只斷言字串出現在整份原始碼**，註解也算。
        """
        from tests.source_assertions import executable_source

        return executable_source(src)

    def test_note_does_not_claim_a_total_from_a_sum(self):
        src = self._executable_lines(inspect.getsource(chart_runner))
        marker = 'family_report["rows"]'
        self.assertIn(marker, src, "找不到家族註記那段（本測試需同步更新）")
        window_start = src.index(marker)
        window = src[max(0, window_start - 600): window_start + 600]
        self.assertNotRegex(
            window, r"存活家族共",
            "註記仍宣稱「存活家族共 N 個」，而 N 是各國相加的結果——"
            "同一家族跨幾國就被算幾次（滑雪機 40 個家族被算成 46）")

    def test_note_labels_the_number_as_cross_country(self):
        """若仍要呈現這個數字，必須講清楚它是佈局點不是家族總數。"""
        src = inspect.getsource(chart_runner)
        idx = src.index('family_report["rows"]')
        window = src[max(0, idx - 900): idx + 900]
        self.assertRegex(
            window, r"跨國|重複計入|佈局點",
            "註記沒有說明同一家族跨國會重複計入——讀者會把它當家族總數")


if __name__ == "__main__":
    unittest.main()
