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
    def test_basis_vocabulary_is_closed(self):
        """基準詞彙是**封閉集合**——鎖住是為了防止有人加一個模糊的類別來安置
        說不清楚的門檻（例如「工程判斷」「經驗值」），那等於取消這道閘門。

        🔴 2026-08-19 刻意加第四類「可靠度下限」：使用者原則「不能是絕對值」
        對**比較型**判準成立，但**可靠度型**（資料夠不夠讓計算有意義）改成
        推導是循環論證——「這批少所以把標準放寬」，而少正是不可靠的時候。
        原本三類裝不下它，只能硬塞「本次母體」＋ pending，那是用宣告掩蓋分類錯誤。
        ⚠ 加類別的門檻很高：新類別必須**改變該門檻該不該推導的答案**，
        而不只是讓表格看起來比較整齊。
        """
        self.assertEqual(set(T.BASES),
                         {"本次母體", "制度事實", "全庫", "可靠度下限"})

    def test_reliability_floor_needs_a_checkable_reference(self):
        """宣告「可靠度下限」必須附**統計慣例或可從程式內部推導**的依據。

        ⚠ 這一類最容易變成藏污納垢處：說「這是可靠度下限」就不必推導了。
        所以它跟「制度事實」同樣要附依據，且同樣不接受自我指涉。
        """
        for name, d in T.THRESHOLD_BASIS.items():
            if d.basis != "可靠度下限":
                continue
            with self.subTest(threshold=name):
                self.assertTrue(str(d.reference).strip(),
                                f"{name} 宣告可靠度下限卻沒給依據")
                for vague in ("沿用既有", "實測如此", "經驗值", "工程判斷"):
                    self.assertNotIn(vague, d.reference,
                                     f"{name} 的依據是自我指涉")

    def test_settled_reliability_floor_is_not_justified_by_this_batch(self):
        """⚠ 核心：**已定案**的可靠度下限，依據不得用本批的分布來 justify。

        那正是原本的病——「本案 13 個主題有 3 個落在這裡」「實機動因：滑雪機
        60 筆」。它們說明了調整的**場合**，沒說明門檻該取這個值的**理由**。

        🔴 2026-08-19 校準：本閘門初版**不分 pending 與否**，於是抓到
        `STATUS_TAIL_PENDING_RATIO`——它的 reference 是**跨批校準紀錄**
        （「50% 會多排除滑雪機的 2024、70% 會少排除割草機的 2025，只有 60%
        兩批都成立」），那是關於門檻穩健性的證據，不是從單批推出的理由。

        ⚠ 而且它標了 `pending=True`，也就是**明說自己還沒有依據**。
        這時要求依據不得提批次，等於逼它把僅有的證據刪掉——閘門會促成
        資訊消失，那與它存在的目的相反。

        ⚠ 這正是昨天記下的那條：**訊號式掃描抓的是詞不是行為**。
        故本閘門只管**已定案**者：宣告「我有依據了」的，依據就不能是某一批。
        還在 pending 的，留著它的校準紀錄比乾淨更重要。

        ⚠ 2026-08-20 第二次校準：本閘門再度抓到 `STATUS_MIN_SAMPLE`——我把「兩批
        實測擋掉 0/5 與 2/10」寫進了 `reference`。那是**驗證**（它沒壞）不是
        **依據**（為何取 5），欄位放錯。

        這次**不放寬閘門**：上次已為 pending 放寬一次，若已定案的也放寬，這條
        規則就形同虛設。正解是把驗證證據移到 `why`，讓兩個欄位各司其職——
        `reference` 回答「為什麼是這個值」，`why` 收其餘一切（含怎麼檢查的）。
        """
        for name, d in T.THRESHOLD_BASIS.items():
            if d.basis != "可靠度下限" or d.pending:
                continue
            with self.subTest(threshold=name):
                for batch_word in ("滑雪機", "割草機", "本案 ", "這批資料"):
                    self.assertNotIn(batch_word, d.reference,
                                     f"{name} 已定案，依據卻仍指向某一批資料"
                                     "（實測結果屬驗證，該放 why 不是 reference）")

    def test_pending_reliability_floor_still_needs_evidence(self):
        """⚠ 放寬的補償：pending 的可靠度下限仍要留下**測過什麼**，
        不能因為標了 pending 就什麼都不寫——那會變成用 pending 逃避舉證。"""
        for name, d in T.THRESHOLD_BASIS.items():
            if d.basis != "可靠度下限" or not d.pending:
                continue
            with self.subTest(threshold=name):
                self.assertTrue(str(d.reference).strip(),
                                f"{name} 標 pending 但連測過什麼都沒寫")


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
    #: ⚠ `STATUS_STAGNANT_BAND` **不在**這裡：帶心雖已改為推導，但它的問題是
    #: 機制（ratio 拿 5 年窗比 9 年窗，被窗長汙染），不是值。
    #: 把值改成推導不等於修好機制——這條界線是本表存在的意義。
    #:
    #: ⚠ 解除的方式有**兩種**，不是只有推導：
    #:   ①改成由本批推導（比較型）——前三項
    #:   ②依據換成統計／演算法理由並改宣告為「可靠度下限」（可靠度型）——後兩項
    #: 後者仍是絕對值，但它**本來就該是**；病在依據用本批分布 justify，不在值本身。
    RESOLVED = ("STATUS_EARLY_YEARS", "STATUS_RECENT_YEARS", "STATUS_GROWTH_HIGH",
                "STATUS_MIN_SAMPLE", "MIN_CLUSTERING_DOCUMENTS")

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
        """⚠ 解除 pending 的必須留下**怎麼解的**，否則下次讀的人只看到一個 False，
        無從判斷它是真的解了、還是有人為了讓閘門變綠而改的。

        ⚠ 兩條解除路徑各留各的證據，不能只認一種（本測試初版只認 `derivation`，
        把靠依據解除的那兩個判成失敗——**測試自己也會有「只認得一種正確」的偏差**）：
          - 比較型 → 改成推導 → 留 `derivation`
          - 可靠度型 → 依據換成統計／演算法理由 → 留 `reference`
        """
        for name in self.RESOLVED:
            with self.subTest(threshold=name):
                d = T.THRESHOLD_BASIS[name]
                self.assertFalse(d.pending, f"{name} 列在 RESOLVED 卻仍標 pending")
                evidence = str(d.derivation).strip() or str(d.reference).strip()
                self.assertTrue(evidence,
                                f"{name} 解除了 pending 卻既沒說推導方式、也沒給依據")

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
