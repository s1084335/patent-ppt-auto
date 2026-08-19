"""判準必須宣告基準來源（tasks §9.9）。

## 使用者指出的問題（2026-08-19）

> 「分類器在這環節之前就要依據建立在 workspace 的資料才有意義，這應該去掃程式。」
> 「workspace 是會變的，你也不能只用全庫的當依據。」

掃描結果：分析路徑下 50 個模組層數值常數，**4 個註解自承出自單一資料集**
（`STATUS_GROWTH_HIGH = 0.70` 註解寫「全庫基準 R＝38/55」——55 就是滑雪機的
workspace 成員數）。

🔴 **這與第 1 段的母體閘門是同一種病**：那個 0.70 套到割草機上，
就是**不同 workspace 的資料混進來**，只是混的是門檻不是件數，且偽裝成常數。

## 為什麼判準是「宣告基準來源」而不是「不得出現字面常數」

⚠ 「原始碼沒有裸數字」可以用**把常數搬進設定檔**滿足——閘門會綠、行為完全沒變，
而且可追溯性**反而下降**（從「有註解說明它出自滑雪機」變成「設定檔裡一個沒有
來歷的值」）。那正是 v5／v7／v9 形式鎖的死法重演：**為了過鎖而搬家**。

改成正面表述：**每個判準必須宣告 `基準來源 ∈ {本次母體, 制度事實, 全庫}`，
沒有宣告即紅。** 搬家不會產生宣告，改寫算式也不會。

## ⚠ 這道閘門守不住什麼

宣告制擋得了「沒說」，擋不了「說謊」——宣告 `本次母體` 但實際算錯母體，
閘門看不出來。那一層靠 §1 的母體閘門與兩個 workspace 的數字對帳。
**兩道必須都在**，少一道另一道就會被當成「已經檢查過了」。
"""
from __future__ import annotations

import unittest

from backend.app.reports import threshold_basis as T


class BasisVocabularyTests(unittest.TestCase):
    def test_three_bases_only(self):
        self.assertEqual(set(T.BASES), {"本次母體", "制度事實", "全庫"})


class DeclarationTests(unittest.TestCase):
    """每個判準都要有宣告，且宣告內容要完整。"""

    def test_every_threshold_is_declared(self):
        for name, d in T.THRESHOLD_BASIS.items():
            with self.subTest(threshold=name):
                self.assertIn(d.basis, T.BASES, f"{name} 的基準來源不合法")
                self.assertTrue(str(d.why).strip(), f"{name} 沒寫理由")

    def test_institutional_basis_needs_external_reference(self):
        """§9.9g-2：宣告「制度事實」必須附**可外部查證**的依據。

        ⚠ 且不得是「沿用既有」「實測如此」這類自我指涉——那是把「我們一直
        這樣做」當成理由，等於沒有理由（比照 §1.3 白名單的空理由不算）。
        """
        for name, d in T.THRESHOLD_BASIS.items():
            if d.basis != "制度事實":
                continue
            with self.subTest(threshold=name):
                self.assertTrue(str(d.reference).strip(),
                                f"{name} 宣告制度事實卻沒給依據")
                for vague in ("沿用既有", "實測如此", "慣例"):
                    self.assertNotIn(vague, d.reference,
                                     f"{name} 的依據是自我指涉")

    def test_population_basis_names_the_derivation(self):
        """宣告「本次母體」要說出**怎麼推導**，否則無法反查算得對不對。"""
        for name, d in T.THRESHOLD_BASIS.items():
            if d.basis != "本次母體":
                continue
            with self.subTest(threshold=name):
                self.assertTrue(str(d.derivation).strip(),
                                f"{name} 沒寫推導方式")


class KnownOffendersAreTrackedTests(unittest.TestCase):
    """🔴 掃描抓到的「出自單一資料集」必須在表上，未解的要標為待修。"""

    #: 全部被抓到過的門檻——**只增不減**：這份清單是歷史事實，
    #: 記錄「哪些東西曾經綁死單一資料集」。解掉的移到 RESOLVED，不是從這裡刪。
    OFFENDERS = ("STATUS_EARLY_YEARS", "STATUS_RECENT_YEARS",
                 "STATUS_GROWTH_HIGH", "STATUS_MIN_SAMPLE",
                 "MIN_CLUSTERING_DOCUMENTS")

    #: 已解除資料綁定者。⚠ 移進來要有**可指的證據**，不是「看起來修好了」：
    #: 2026-08-19 兩個時間窗改由 `derive_status_windows` 從本批資料推導，
    #: 且實測滑雪機那批推導結果與原常數逐字相同、13 個主題狀態一個都沒變
    #: （見 `test_status_windows_relative`）。
    RESOLVED = ("STATUS_EARLY_YEARS", "STATUS_RECENT_YEARS")

    def test_all_offenders_are_declared(self):
        for name in self.OFFENDERS:
            with self.subTest(threshold=name):
                self.assertIn(name, T.THRESHOLD_BASIS,
                              f"{name} 沒有宣告基準來源——它出自單一資料集")

    def test_unresolved_offenders_are_marked_as_pending(self):
        """⚠ 未解的必須標 pending：宣告表要誠實反映現況，

        不得為了讓表看起來乾淨而硬塞一個基準——那就變成用宣告掩蓋問題。
        """
        for name in self.OFFENDERS:
            if name in self.RESOLVED:
                continue
            with self.subTest(threshold=name):
                self.assertTrue(T.THRESHOLD_BASIS[name].pending,
                                f"{name} 沒被標為待修")

    def test_resolved_ones_say_how_they_were_resolved(self):
        """⚠ 解除 pending 的必須留下推導方式，否則下次讀的人只看到一個 False，
        無從判斷它是真的解了、還是有人為了讓閘門變綠而改的。"""
        for name in self.RESOLVED:
            with self.subTest(threshold=name):
                d = T.THRESHOLD_BASIS[name]
                self.assertFalse(d.pending, f"{name} 列在 RESOLVED 卻仍標 pending")
                self.assertTrue(str(d.derivation).strip(),
                                f"{name} 解除了 pending 卻沒說推導方式")

    def test_pending_ones_carry_the_evidence(self):
        for name in self.OFFENDERS:
            with self.subTest(threshold=name):
                self.assertTrue(str(T.THRESHOLD_BASIS[name].why).strip())


class MechanismLockIsRecordedTests(unittest.TestCase):
    """§9.9e-6：「機制寫死」比「值寫死」難看見，要單獨記。"""

    def test_stagnant_band_records_its_assumption(self):
        d = T.THRESHOLD_BASIS["STATUS_STAGNANT_BAND"]
        self.assertIn("假設", d.why,
                      "沒寫出它假設了資料的什麼形狀——把值改成 per-run 推導"
                      "不會修好機制寫死")


class ScannerContractTests(unittest.TestCase):
    """宣告表與實際程式不得分岔。"""

    def test_declared_names_exist_in_code(self):
        """⚠ 宣告一個不存在的常數＝這張表在騙人，而且不會有東西報錯。"""
        missing = T.undeclared_or_missing()["declared_but_missing"]
        self.assertEqual(missing, [], f"宣告了不存在的常數：{missing}")

    def test_no_analysis_threshold_is_undeclared(self):
        """⚠ 反向：程式裡有、表上沒有＝漏網（缺席型偏差）。"""
        missing = T.undeclared_or_missing()["in_code_but_undeclared"]
        self.assertEqual(missing, [], f"這些判準沒有宣告基準來源：{missing}")


if __name__ == "__main__":
    unittest.main()
