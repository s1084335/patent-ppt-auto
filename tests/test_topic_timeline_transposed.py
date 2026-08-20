"""主題演進表轉置：年份為列、主題為欄。

🔴 2026-08-19 使用者裁決（實機看報表，表格橫向捲軸切掉一半年份）：
「如果主題標籤放 x 軸，時間放 y 軸，表格就塞得下了吧?」→「一律轉置成年份為列、
主題為欄」。

實測（report_trial_20260819_122745，技術通道）：
- 轉置前：5 列 × **14 欄**（12 個年份 ＋ 主題標籤 ＋ 總件數）→ 必須橫捲
- 轉置後：12 列 × **6 欄**（申請年份 ＋ 5 個主題）→ 塞得下

⚠ 使用者已知並接受代價：主題數多的 workspace 轉置後會比較寬
（13 主題 → 14 欄）。此為明示裁決「一律轉置」，不做條件判斷——
條件式會讓同一份報表在不同資料量下長得不一樣，兩次產出無法對照。

⚠ 順帶修掉的隱性缺陷：轉置前每列的鍵是 `{"label", "2011", "2013", …, "total"}`，
前端以 `Object.keys(rows[0])` 取欄序，而 ECMAScript 規定**整數樣字串鍵**
（"2011"）一律排在字串鍵之前並按數值升冪——所以後端明明把 `label` 排第一，
畫面上「主題標籤」卻跑到最右邊。轉置後鍵不再是整數樣，欄序自然正確。
⚠ 但同一支 `pivot_year_matrix` 也供申請人年度矩陣使用，那張表仍有此症狀
（見 `applicant_year_matrix`）——尚未處理，不在本次裁決範圍。
"""
from __future__ import annotations

import unittest

from backend.app.reports.chart_runner import (
    TOTAL_ROW_LABEL,
    pivot_year_matrix_by_year,
)


class TransposedYearMatrixTests(unittest.TestCase):
    """長格式（實體, 年, 件數）→ 年份為列的交叉表。"""

    ROWS = [
        {"label": "風磁複合阻力裝置", "application_year": 2020, "patent_count": 4},
        {"label": "風磁複合阻力裝置", "application_year": 2022, "patent_count": 3},
        {"label": "拉繩滑雪模擬機構", "application_year": 2022, "patent_count": 5},
        {"label": "拉繩滑雪模擬機構", "application_year": 2024, "patent_count": 2},
        {"label": "立柱滑輪訓練機構", "application_year": 2017, "patent_count": 1},
    ]

    def test_one_row_per_year_in_time_order(self):
        """每列一個申請年，由舊到新——時間軸就是列序；末列是總計列。"""
        out = pivot_year_matrix_by_year(self.ROWS, "label")
        self.assertEqual([r["application_year"] for r in out],
                         ["2017", "2020", "2022", "2024", TOTAL_ROW_LABEL])

    def test_each_topic_becomes_a_column(self):
        """主題成為欄位，欄序依該主題總件數降冪（重要的在左）。

        ⚠ 風磁（4+3）與拉繩（5+2）都是 7 件——**平手**，依名稱排序決定先後
        （拉 U+62C9 < 風 U+98A8）。順序必須是決定性的：靠 dict 插入序會讓
        同一份資料在不同輸入順序下產出不同欄序，兩次產出無法對照。
        """
        out = pivot_year_matrix_by_year(self.ROWS, "label")
        cols = [c for c in out[0] if c not in ("application_year", "total")]
        self.assertEqual(cols, ["拉繩滑雪模擬機構", "風磁複合阻力裝置", "立柱滑輪訓練機構"])
        # 反序輸入要得到同一組欄序（決定性，不吃輸入順序）
        again = pivot_year_matrix_by_year(list(reversed(self.ROWS)), "label")
        self.assertEqual([c for c in again[0] if c not in ("application_year", "total")],
                         cols)

    def test_cells_carry_the_counts(self):
        """格值＝該主題該年的件數。"""
        out = {r["application_year"]: r for r in
               pivot_year_matrix_by_year(self.ROWS, "label")}
        self.assertEqual(out["2022"]["風磁複合阻力裝置"], 3)
        self.assertEqual(out["2022"]["拉繩滑雪模擬機構"], 5)
        self.assertEqual(out["2017"]["立柱滑輪訓練機構"], 1)

    def test_absent_cell_is_blank_not_zero(self):
        """該年該主題無資料回空字串不是 0。

        ⚠ 沿用 `pivot_year_matrix` 的取捨：0 讀起來像「查過但沒有」，
        空白才是「無此資料」。兩支若在這點分岔，同一份報表兩張表會互相矛盾。
        """
        out = {r["application_year"]: r for r in
               pivot_year_matrix_by_year(self.ROWS, "label")}
        self.assertEqual(out["2017"]["風磁複合阻力裝置"], "")

    def test_total_is_per_topic_in_the_last_row(self):
        """總件數＝**各主題**跨年合計，放在末列。

        🔴 2026-08-19 使用者裁決：「總件數應該算主題的，不用算各年的」。
        轉置初版沿用了 `pivot_year_matrix` 的 `total` 欄，語意變成「該年跨主題
        合計」——那是年度趨勢圖已經在回答的問題，在主題演進表裡既重複又佔一欄。
        ⚠ 本測試同時鎖住「逐年合計不得復活」：`total` 欄一旦回來，兩張表會對同一
        個詞給出兩種數字，讀者無從判斷哪個才是「總件數」。
        """
        out = pivot_year_matrix_by_year(self.ROWS, "label")
        last = out[-1]
        self.assertEqual(last["application_year"], TOTAL_ROW_LABEL)
        self.assertEqual(last["風磁複合阻力裝置"], 7)    # 4 + 3
        self.assertEqual(last["拉繩滑雪模擬機構"], 7)    # 5 + 2
        self.assertEqual(last["立柱滑輪訓練機構"], 1)
        for row in out:
            self.assertNotIn("total", row, "逐年合計欄不得復活")

    def test_column_keys_are_not_integer_like(self):
        """欄鍵不得為整數樣字串——否則前端 Object.keys 會重排欄序。

        ⚠ 這是轉置真正解決的問題：年份從**鍵**變成**值**之後，
        JS 的 integer-like key 重排就咬不到這張表了。
        """
        out = pivot_year_matrix_by_year(self.ROWS, "label")
        for key in out[0]:
            self.assertFalse(
                key.isdigit(),
                f"欄鍵「{key}」是整數樣字串，前端會把它排到最前面")

    def test_empty_input(self):
        self.assertEqual(pivot_year_matrix_by_year([], "label"), [])

    def test_entity_named_like_a_reserved_column_does_not_overwrite_it(self):
        """主題名剛好叫「application_year」時不得吃掉年份欄。

        ⚠ 靜默覆蓋類的缺陷：不會報錯，只會讓某一列的年份變成該主題的件數。
        AI 產的主題標籤不受控，這個防線很便宜。
        ⚠ `total` 已不再是保留欄名（逐年合計欄退場），故叫 total 的主題現在是
        一般欄——本測試一併鎖住它不被誤加註。
        """
        rows = [{"label": "application_year", "application_year": 2022,
                 "patent_count": 7},
                {"label": "total", "application_year": 2022, "patent_count": 2}]
        out = pivot_year_matrix_by_year(rows, "label")
        self.assertEqual(out[0]["application_year"], "2022", "年份欄被同名主題覆蓋了")
        self.assertEqual(out[0]["application_year（主題）"], 7)
        self.assertEqual(out[0]["total"], 2, "total 已非保留欄名，不該被加註")


if __name__ == "__main__":
    unittest.main()
