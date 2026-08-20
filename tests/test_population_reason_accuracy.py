"""母體註記的**理由**必須與實際排除機制相符（2026-08-20 驗收發現）。

## 症狀

割草機 226 件報表：

    cluster_topic_table:tech = 「母體 216/226 件（10 件無分群來源文本）」
    design_protection_detail = 「母體 0/226 件（…）」  ← 該區實際顯示 5 家、10 件設計案

第一條的**數字對、理由錯**：那 10 件是設計案，由 `DESIGN_DOCUMENT_KINDS` 規則
排除，不是因為沒有文本——實測其中 1 件（patent_id 452）確實有獨立項文字，
先前正是靠「有沒有文本」這個代理指標把它漏進技術分群。理由留著舊說法，
下次有人依它判斷「補文本就能進分群」就會做錯事。

第二條的**數字是 0**：`_sum_patent_count` 找不到 `patent_count` 欄（該報表一列
一件專利、沒有件數欄），於是回 0。讀者看到「母體 0/226」只會認為整區壞了。

## 契約

1. 理由只能陳述**查得到的事實**：設計案件數由 `transforms/patent_kind` 判定。
2. 判不出來時退回中性句，**不得沿用舊理由**——錯的理由比沒有理由更糟。
3. 母體算法不是「patent_count 加總」的報表，要有自己的計數器，不得靜默回 0。
"""
from __future__ import annotations

import unittest

TOTAL = 226
# 226 件裡 10 件設計案：`document_kind='S'` 是唯一判定欄（見 transforms/patent_kind）。
DESIGN_ROWS = [{"patent_id": i, "patent_type": "P", "document_kind": "S"}
               for i in range(10)]
TECH_ROWS = [{"patent_id": 100 + i, "patent_type": "P", "document_kind": "B2"}
             for i in range(216)]


def _reports(**extra):
    return {
        "application_trend": {"rows": [{"patent_count": TOTAL}]},
        "design_protection_detail": {"rows": DESIGN_ROWS + TECH_ROWS},
        **extra,
    }


def _cluster_rows(covered: int, source_field: str = "wips_independent_claims"):
    return [{"source_field": source_field, "patent_count": covered}]


class ClusterReasonTests(unittest.TestCase):
    """技術通道排除理由：設計案規則 vs 缺文本，兩者不得混為一談。"""

    def _notes(self, reports):
        from backend.app.reports.population import population_notes

        return population_notes(reports)

    def test_exclusion_equal_to_design_count_is_named_as_the_design_rule(self):
        """🔴 排除數＝設計案數時，理由必須指向設計案規則，不是「無來源文本」。"""
        notes = self._notes(_reports(
            cluster_topic_table={"rows": _cluster_rows(216)}))
        note = notes["cluster_topic_table:tech"]
        self.assertIn("母體 216/226 件", note)
        self.assertIn("外觀設計", note, f"沒說是設計案被排除：{note}")
        self.assertNotIn("無分群來源文本", note,
                         f"仍沿用代理指標的舊理由：{note}")

    def test_extra_shortfall_beyond_design_is_reported_separately(self):
        """設計案以外還少的件數要分開講——那才是真的缺文本。"""
        notes = self._notes(_reports(
            cluster_topic_table={"rows": _cluster_rows(210)}))
        note = notes["cluster_topic_table:tech"]
        self.assertIn("外觀設計 10", note, note)
        self.assertIn("6", note, f"多排除的 6 件沒交代：{note}")
        self.assertIn("無分群來源文本", note, note)

    def test_without_kind_information_no_cause_is_invented(self):
        """⚠ 判不出設計案時只印數字，不得猜一個理由。"""
        from backend.app.reports.population import population_notes

        notes = population_notes({
            "application_trend": {"rows": [{"patent_count": TOTAL}]},
            "cluster_topic_table": {"rows": _cluster_rows(216)},
        })
        note = notes["cluster_topic_table:tech"]
        self.assertIn("母體 216/226 件", note)
        self.assertNotIn("外觀設計", note, f"沒有依據卻聲稱是設計案：{note}")
        self.assertNotIn("無分群來源文本", note, f"沒有依據卻聲稱缺文本：{note}")


class DesignProtectionPopulationTests(unittest.TestCase):
    """設計保護策略區的母體＝設計案件數，不是 0。"""

    def _notes(self):
        from backend.app.reports.population import population_notes

        return population_notes(_reports())

    def test_covered_counts_design_patents_not_zero(self):
        """🔴 該區一列一件專利、無 patent_count 欄，不得因此算成 0。"""
        note = self._notes()["design_protection_detail"]
        self.assertIn("母體 10/226 件", note,
                      f"母體算錯（原本會是 0/226）：{note}")

    def test_reason_says_it_covers_all_design_patents(self):
        note = self._notes()["design_protection_detail"]
        self.assertIn("外觀設計", note, note)


class DesignExclusionNoteTests(unittest.TestCase):
    """`patent_kind.design_exclusion_note`：同一個舊理由的第二個落點。

    ⚠ 修 bug 時必問「這個錯誤假設還有誰也在用」——「設計案沒有技術請求項」
    在 `population.py` 與此處各寫了一次，只改一處會留下矛盾的兩句話。
    """

    def test_note_does_not_claim_design_patents_lack_claims(self):
        """實測 10 件設計案中有 1 件帶獨立項文字，「無技術請求項」不成立。"""
        from backend.app.transforms.patent_kind import design_exclusion_note

        note = design_exclusion_note(DESIGN_ROWS + TECH_ROWS)
        self.assertIn("設計 10 件", note)
        self.assertNotIn("無技術請求項", note,
                         f"仍以「有沒有請求項」當設計案的排除依據：{note}")

    def test_note_is_empty_without_design_patents(self):
        from backend.app.transforms.patent_kind import design_exclusion_note

        self.assertEqual(design_exclusion_note(TECH_ROWS), "")


if __name__ == "__main__":
    unittest.main()
