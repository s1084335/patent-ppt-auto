"""證據鏈與可反駁性引用（tasks §9.5／§9.8）。

## 使用者的完整性判準（2026-08-19）

> 不要求固定「至少 N 項」，而是要求每個專利行動建議都必須由分析資料推導，
> 且能反向追溯：**分析結果 → 證據 → 結論 → 專利行動**

現行結論列是 `{topic, finding, reading, action}`——`finding` 是分析結果（引擎、
逐字閘門）、`reading` 是結論、`action` 是行動。**四段裡缺「證據」那一段**。

## §9.8 的原則：可反駁性

> 每一句你寫的判斷，都要指得出「如果哪一個引擎欄位是別的值，這句話就不成立」。

⚠ 不是新發明——「發現欄逐字引用引擎字串」之所以守得住，正因為它的反駁條件是
機械的（字串不符即紅）。把同一個形狀推廣到所有判斷。

給 CLI 的操作句：**不要說「規則錯了」，要說「規則沒看 X 欄位，而這裡 X = Y」。**

## ⚠ 這套機制守不住什麼（誠實寫在這裡）

三問的第二問**不過**：CLI 可以引用一個**真實存在**的欄位值，而那個值跟它的主張
毫無關係。這套機制驗的是**形狀**，不是**推理**。
它比純自由敘述強的地方在於：偏離是「與某一條具名規則不同意」，**累積上可稽核**
——同一條規則反覆被同一種引用推翻，不是規則錯就是 CLI 在鑽同一個洞。
單次守不住，這點必須明講，不得宣稱閘門擋得住亂寫。
"""
from __future__ import annotations

import unittest

from backend.app.reports import evidence_ref as E


class CitationFormatTests(unittest.TestCase):
    """§9.8b-1：三個場合共用同一種引用格式（唯一定義處）。"""

    def test_parses_field_value(self):
        self.assertEqual(E.parse_citations("依據 [status=成長] 判斷"),
                         [("status", "成長")])

    def test_parses_multiple(self):
        self.assertEqual(
            E.parse_citations("[status=成長] 且 [pending_count=3]"),
            [("status", "成長"), ("pending_count", "3")])

    def test_ignores_plain_brackets(self):
        """⚠ 一般方括號不是引用——誤判會把正常文字當成引用去驗，全部紅。"""
        self.assertEqual(E.parse_citations("見附錄 [1] 與 [表 2]"), [])

    def test_format_is_symmetric(self):
        """產生與解析必須互為反向，否則兩邊會各自演進。"""
        text = E.cite("max_share", 88)
        self.assertEqual(E.parse_citations(text), [("max_share", "88")])


class VerifyAgainstEngineTests(unittest.TestCase):
    """§9.8b-2：引用的欄位要在該主題的引擎輸出裡存在，值要逐字相符。"""

    ROW = {"label": "拉繩滑雪", "status": "成長", "pending_count": 3,
           "max_share": 20}

    def test_matching_citation_passes(self):
        self.assertEqual(E.verify_citations("[status=成長]", self.ROW), [])

    def test_unknown_field_is_rejected(self):
        bad = E.verify_citations("[nosuch=1]", self.ROW)
        self.assertTrue(bad)
        self.assertIn("nosuch", bad[0])

    def test_wrong_value_is_rejected(self):
        """⚠ 欄位存在但值寫錯——這是最容易溜過去的一種。"""
        bad = E.verify_citations("[status=衰退]", self.ROW)
        self.assertTrue(bad)
        self.assertIn("衰退", bad[0])

    def test_numeric_value_compares_as_text(self):
        """引擎給 int、CLI 寫字串，兩邊要對得上（不然合法引用會被誤擋）。"""
        self.assertEqual(E.verify_citations("[pending_count=3]", self.ROW), [])

    def test_no_citation_is_reported(self):
        """判讀必須至少帶一個引用——沒有引用就沒有反駁條件。"""
        bad = E.verify_citations("這個方向很有潛力。", self.ROW)
        self.assertTrue(any("沒有引用" in b for b in bad), bad)


class DeviationNeedsCitationTests(unittest.TestCase):
    """§9.7e-1：CLI 的偏離要附引用並留痕，**閘門不擋**。"""

    ROW = {"label": "拉繩滑雪", "status": "成長", "pending_count": 3}

    def test_override_without_citation_is_flagged(self):
        bad = E.check_deviations(
            [{"action": "降低投入", "kind": "add", "reason": "我覺得該收"}],
            self.ROW)
        self.assertTrue(bad, "沒有引用的偏離沒被指出")

    def test_override_with_citation_is_accepted(self):
        self.assertEqual(
            E.check_deviations(
                [{"action": "降低投入", "kind": "add",
                  "reason": "規則沒看 pending_count，而這裡 [pending_count=3]"}],
                self.ROW),
            [])

    def test_deviation_is_recorded_not_blocked(self):
        """🔴 擋了就變回天花板——規則沒涵蓋的真實機會永遠不會出現。

        ⚠ 這條測的是**設計意圖**：`check_deviations` 回的是「要記下來的問題」，
        呼叫端據此決定擋或不擋；本函式本身不做拒絕。
        """
        out = E.check_deviations(
            [{"action": "降低投入", "kind": "add", "reason": "沒有引用"}], self.ROW)
        self.assertIsInstance(out, list, "偏離檢查回的應是清單不是例外")


if __name__ == "__main__":
    unittest.main()
