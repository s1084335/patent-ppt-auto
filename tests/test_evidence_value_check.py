"""直接引用的數字要對得上引擎數據；衍生數字不擋（2026-08-10 使用者裁決）。

## 為什麼需要這一層

到此為止的檢查擋得住「**沒查**就寫」（`validate_research_effort`）與「**沒用素材**
就寫」（narrative 溯源），但擋不住「**查了，但數字寫錯**」——evidence 原本只記來源
*類型*（`source` / `chart_identity` / `report_key`），不記值，所以簡報上寫「83%」
時，程式無從判斷它對不對。

`content_standard.md` 第三節早就規定「標題級統計一律以 report_data 為準，不得自己
重算後改寫」，但那條規則只活在提示裡，沒有任何程式在驗。

## 判準（使用者裁決）：只驗直接引用，衍生不擋

| 數字類型 | 例 | 處置 |
|---|---|---|
| **直接引用** | 「風磁 11 件」——這個 11 就在 `report_data` 的某一列 | ✅ 拿去對；對不上就擋 |
| **衍生** | 「帝瑪斯占 83%」——由兩個數相除算出 | ⚠ **不擋**：算式來源難以機械核對，硬擋會逼 CLI 為了過關而不敢寫比例 |

⚠ 取捨已知且刻意：CLI 可以不填 `value` 來規避檢查。但「刻意規避」與「寫錯」是
不同性質的問題——後者是本層要解的，前者靠 prompt 與人工抽驗。用一層擋不住所有事，
不代表這一層沒有價值。
"""
from __future__ import annotations

import unittest

from backend.app.reports.planning_contracts import validate_evidence

SNAP = "v1"
REPORT_DATA = {
    "chart_rows": {
        "applicant_ranking": [
            {"applicant_display_name": "帝瑪斯", "patent_count": 11},
            {"applicant_display_name": "祺驊", "patent_count": 6},
        ],
    },
}


def _plan(ref="e1", text="風磁 11 件"):
    return {"slides": [{"slide_id": "s1",
                        "narrative": [{"text": text, "evidence_ref": ref}]}]}


def _ev(**extra):
    return {"e1": {"source": "selected_chart", "snapshot_id": SNAP,
                   "chart_identity": "applicant_ranking:default", **extra}}


class EvidenceValueTests(unittest.TestCase):
    """帶 value 就要對得上引擎數據。"""

    def test_matching_value_passes(self):
        """value 出現在該報表的數值欄位裡 → 通過。"""
        self.assertEqual(
            validate_evidence(_plan(), _ev(value=11), snapshot_id=SNAP,
                              report_data=REPORT_DATA),
            [],
        )

    def test_mismatched_value_is_rejected(self):
        """value 對不上 → 擋。這就是「查了但數字寫錯」的攔截點。"""
        errors = validate_evidence(_plan(), _ev(value=99), snapshot_id=SNAP,
                                   report_data=REPORT_DATA)
        self.assertTrue(errors)
        self.assertIn("99", errors[0])
        self.assertIn("applicant_ranking", errors[0])

    def test_derived_value_not_checked(self):
        """標了 derived 的衍生數字不驗——使用者定案：衍生不拿來擋。"""
        self.assertEqual(
            validate_evidence(_plan(text="帝瑪斯占 83%"), _ev(value=83, derived=True),
                              snapshot_id=SNAP, report_data=REPORT_DATA),
            [],
        )

    def test_no_value_not_checked(self):
        """沒填 value 就不驗——敘述性要點本來就沒有可對照的單一數字。"""
        self.assertEqual(
            validate_evidence(_plan(), _ev(), snapshot_id=SNAP, report_data=REPORT_DATA),
            [],
        )

    def test_without_report_data_skips_check(self):
        """沒給 report_data 時不驗（向後相容，呼叫端沒改到的地方不受影響）。"""
        self.assertEqual(
            validate_evidence(_plan(), _ev(value=99), snapshot_id=SNAP),
            [],
        )

    def test_unknown_report_key_is_reported(self):
        """引用了 report_data 裡沒有的報表 → 擋（指向不存在的來源等於沒來源）。"""
        manifest = {"e1": {"source": "selected_chart", "snapshot_id": SNAP,
                           "chart_identity": "not_a_report:default", "value": 11}}
        errors = validate_evidence(_plan(), manifest, snapshot_id=SNAP,
                                   report_data=REPORT_DATA)
        self.assertTrue(errors)
        self.assertIn("not_a_report", errors[0])

    def test_float_and_string_values_compare_loosely(self):
        """11 / 11.0 / "11" 視為同一個值——型別差異不是數字錯誤。"""
        for value in (11.0, "11"):
            with self.subTest(value=value):
                self.assertEqual(
                    validate_evidence(_plan(), _ev(value=value), snapshot_id=SNAP,
                                      report_data=REPORT_DATA),
                    [],
                )


if __name__ == "__main__":
    unittest.main()
