"""母體對帳器（A3，2026-08-06）：每頁頁尾印「母體 X/55 件（原因）」。

## 為什麼要有這個

實測 15 張報表有 **6 張母體 ≠ 55**：IPC 44／CPC 5／權人 36／家族 48／
功效分群 44／技術分群 35。封面寫 55、各頁各說各話，而**沒有一頁解釋**——
讀者只會認為資料錯誤（問題 2 的原話）。

## 判準（動工前寫下）

1. **母體＝總數時也要印**——省略會讓讀者分不出「這頁是全量」與「這頁忘了標」
2. **字串排在最前**——`_fit_text` 截斷是砍尾巴，母體排頭才砍不到
3. 濃縮到 `母體 X/Y 件（原因）`，⚠ 括號內**不重複單位**（前面 `件` 已定調）
4. 同族合併後**仍是件**（2026-08-05 單位定案），故家族頁也用同一句型
"""
from __future__ import annotations

import unittest


class PatentTotalTests(unittest.TestCase):
    """總數的單一定義處。"""

    def test_total_from_application_trend(self):
        """總數由申請趨勢逐年件數加總——每件專利恰好落在一個申請年。"""
        from backend.app.reports.population import patent_total

        report_data = {"reports": {"application_trend": {"rows": [
            {"application_year": 2022, "patent_count": 15},
            {"application_year": 2024, "patent_count": 11},
        ]}}}
        self.assertEqual(patent_total(report_data), 26)

    def test_total_is_zero_when_trend_missing(self):
        """⚠ 拿不到總數時回 0，由呼叫端決定不印——不得猜一個數字。"""
        from backend.app.reports.population import patent_total

        self.assertEqual(patent_total({"reports": {}}), 0)


class PopulationNoteTests(unittest.TestCase):
    """母體字串：內容、句型、與已知報表的對應。"""

    def _note(self, report_key, covered, total=55):
        from backend.app.reports.population import population_note

        rows = [{"patent_count": covered}] if covered else []
        return population_note(report_key, rows, total)

    def test_equal_population_still_prints(self):
        """🔴 母體＝總數時也要印，否則分不出「全量」與「忘了標」。"""
        self.assertEqual(self._note("application_trend", 55), "母體 55/55 件")

    def test_ipc_reason_names_design_patents(self):
        """IPC 少 11 件是排除外觀設計，原因要寫出來。"""
        note = self._note("ipc_main_distribution", 44)
        self.assertIn("母體 44/55 件", note)
        self.assertIn("外觀設計", note)
        self.assertIn("11", note)

    def test_family_layout_uses_same_sentence_pattern(self):
        """⚠ 同族合併後仍是「件」（單位定案），家族頁不另立句型。"""
        note = self._note("family_country_layout", 48)
        self.assertTrue(note.startswith("母體 48/55 件"), note)
        self.assertIn("同族合併後", note)
        self.assertNotIn("家族 48 個", note)

    def test_unknown_report_prints_count_without_inventing_a_reason(self):
        """⚠ 沒登記原因的報表只印數字——不得編一個理由。"""
        note = self._note("some_new_report", 40)
        self.assertEqual(note, "母體 40/55 件")

    def test_no_note_when_total_unknown(self):
        """總數拿不到就不印（印 `X/0` 比不印更誤導）。"""
        from backend.app.reports.population import population_note

        self.assertEqual(population_note("ipc_main_distribution", [{"patent_count": 44}], 0), "")

    def test_reason_does_not_repeat_the_unit(self):
        """濃縮判準：括號內不重複「件」以外的贅字。"""
        note = self._note("cpc_main_distribution", 5)
        self.assertIn("母體 5/55 件", note)
        self.assertNotIn("件）件", note)


class DirtyValueTests(unittest.TestCase):
    """⚠ 髒值不得把整頁弄掛，也不得被誤算進母體。"""

    def _sum(self, rows):
        from backend.app.reports.population import _sum_patent_count

        return _sum_patent_count(rows)

    def test_bool_is_not_counted_as_one(self):
        """⚠ Python 的 bool 是 int 子型別——不擋掉的話 True 會被算成 1 件。"""
        self.assertEqual(self._sum([{"patent_count": True}, {"patent_count": 3}]), 3)

    def test_numeric_string_is_counted(self):
        """引擎某些路徑回字串數字，要算得到。"""
        self.assertEqual(self._sum([{"patent_count": "7"}, {"patent_count": " 5 "}]), 12)

    def test_non_numeric_is_ignored_not_crash(self):
        self.assertEqual(self._sum([{"patent_count": "N/A"}, {"patent_count": None},
                                    {}, {"patent_count": 2}]), 2)

    def test_over_counting_reports_get_the_joint_note(self):
        """展開 VIEW 的三張報表：總和大於件數是刻意的，必須加註。"""
        from backend.app.reports.population import population_note

        note = population_note("applicant_ranking", [{"patent_count": 68}], 55)
        self.assertIn("母體 68/55 件", note)
        self.assertIn("含共同申請", note)


class PopulationNotesBatchTests(unittest.TestCase):
    """`population_notes()`：落進 report_data 的那一份。"""

    def _reports(self):
        return {
            "application_trend": {"rows": [{"patent_count": 55}]},
            "ipc_main_distribution": {"rows": [{"patent_count": 44}]},
            "applicant_ranking": {"rows": [{"patent_count": 68}]},
        }

    def test_produces_note_for_every_report(self):
        from backend.app.reports.population import population_notes

        notes = population_notes(self._reports())
        self.assertEqual(notes["application_trend"], "母體 55/55 件")
        self.assertIn("排除外觀設計 11", notes["ipc_main_distribution"])
        self.assertIn("含共同申請", notes["applicant_ranking"])

    def test_empty_when_total_unknown(self):
        """⚠ 拿不到總數時回空 dict——不得產出 `X/0` 這種誤導字串。"""
        from backend.app.reports.population import population_notes

        self.assertEqual(population_notes({"ipc_main_distribution": {"rows": [{"patent_count": 44}]}}), {})


class FootnoteCompositionTests(unittest.TestCase):
    """頁尾組字串：母體必須排最前，否則截斷時最先被砍。"""

    def test_population_comes_first(self):
        from backend.app.reports.population import compose_footnote

        text = compose_footnote("母體 44/55 件（排除外觀設計 11）",
                                sources="IPC 主分類分布", period="2011-2026")
        self.assertTrue(text.startswith("母體 44/55 件"),
                        f"母體沒排最前，截斷時會最先被砍：{text}")
        self.assertIn("來源：IPC 主分類分布", text)
        self.assertIn("期間 2011-2026", text)

    def test_condensed_wording(self):
        """濃縮：`資料來源：`→`來源：`、`統計期間：`→`期間`（實測 55 字→41 字）。"""
        from backend.app.reports.population import compose_footnote

        text = compose_footnote("母體 44/55 件（排除外觀設計 11）",
                                sources="IPC 主分類分布", period="2011-2026")
        self.assertNotIn("資料來源", text)
        self.assertNotIn("統計期間", text)
        self.assertLessEqual(len(text), 72, f"超出頁尾單行容量（約 72 中文字）：{text}")

    def test_still_works_without_population(self):
        """⚠ 母體拿不到時頁尾仍要能組（退回原本的來源／期間）。"""
        from backend.app.reports.population import compose_footnote

        text = compose_footnote("", sources="IPC 主分類分布", period="2011-2026")
        self.assertTrue(text.startswith("來源："), text)


if __name__ == "__main__":
    unittest.main()
