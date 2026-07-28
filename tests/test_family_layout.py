"""family_layout（家族×國家展開）純函式的契約測試。

涵蓋：EPC 清單/同族明細解析、EP 三規則邊界、同族同國去重、
surrogate 家族、完整性核對、pending/unknown/非國家列的現形計數。
規則②（生效程序進行中）在真實樣本命中 0 筆，用合成資料驗證。
"""
from __future__ import annotations

import unittest

from backend.app.transforms.family_layout import (
    EP_TRANSITION_THRESHOLD,
    build_family_country_dataset,
    classify_ep_contribution,
    parse_family_country_counts,
    split_pipe_codes,
)


def make_row(**overrides) -> dict:
    """建一列 canonical 輸入，預設為存活的美國件。"""
    row = {
        "patent_id": 1,
        "family_id": "F1",
        "country_code": "US",
        "legal_status": "授权",
        "family_counts": None,
        "epc_valid": None,
        "epc_invalid": None,
    }
    row.update(overrides)
    return row


class SplitPipeCodesTests(unittest.TestCase):
    """EPC 國家清單解析契約。"""

    def test_basic_split(self) -> None:
        """豎線分隔清單切成大寫代碼，保序。"""
        self.assertEqual(split_pipe_codes("DE | ES | FR | GB"), ["DE", "ES", "FR", "GB"])

    def test_blank_inputs(self) -> None:
        """None/空字串/純空白（407 檔實測 EPC 欄是 " "）回空 list。"""
        for text in (None, "", "   "):
            self.assertEqual(split_pipe_codes(text), [])

    def test_dedupe_and_case(self) -> None:
        """小寫轉大寫、重複代碼去重。"""
        self.assertEqual(split_pipe_codes("de | DE | fr"), ["DE", "FR"])


class ParseFamilyCountryCountsTests(unittest.TestCase):
    """同族明細字串解析契約。"""

    def test_fixed_seven_buckets(self) -> None:
        """固定 7 桶含 0 的實際格式正確解析。"""
        counts, bad = parse_family_country_counts(
            "US-1 | EP-1 | PCT-0 | JP-0 | KR-0 | CN-0 | etc-3"
        )
        self.assertEqual(bad, [])
        self.assertEqual(
            counts, {"US": 1, "EP": 1, "PCT": 0, "JP": 0, "KR": 0, "CN": 0, "etc": 3}
        )

    def test_bad_tokens_reported(self) -> None:
        """解析失敗的 token 回報，不吞掉、不影響其他桶。"""
        counts, bad = parse_family_country_counts("US-2 | ??? | EP-1")
        self.assertEqual(counts, {"US": 2, "EP": 1})
        self.assertEqual(bad, ["???"])

    def test_blank_input(self) -> None:
        """空值回空 dict。"""
        self.assertEqual(parse_family_country_counts(None), ({}, []))


class ClassifyEpContributionTests(unittest.TestCase):
    """EP 三規則判定契約（門檻 EP_TRANSITION_THRESHOLD=30）。"""

    def test_rule2_in_transition(self) -> None:
        """規則②：無效空 且 有效 >= 30 → 隔離。"""
        codes = [f"C{i}" for i in range(EP_TRANSITION_THRESHOLD)]
        kind, contributed = classify_ep_contribution(codes, invalid_is_blank=True)
        self.assertEqual(kind, "in_transition")
        self.assertEqual(contributed, [])

    def test_boundary_29_not_isolated(self) -> None:
        """邊界：無效空但有效只有 29 國 → 照常展開，不隔離。"""
        codes = [f"C{i}" for i in range(EP_TRANSITION_THRESHOLD - 1)]
        kind, contributed = classify_ep_contribution(codes, invalid_is_blank=True)
        self.assertEqual(kind, "countries")
        self.assertEqual(contributed, codes)

    def test_invalid_nonblank_with_many_valid_not_isolated(self) -> None:
        """邊界：無效非空＋有效 >= 30 → 是真實驗證結果，照常展開（850 樣本有 2 筆）。"""
        codes = [f"C{i}" for i in range(EP_TRANSITION_THRESHOLD + 2)]
        kind, contributed = classify_ep_contribution(codes, invalid_is_blank=False)
        self.assertEqual(kind, "countries")
        self.assertEqual(contributed, codes)

    def test_mature_grant(self) -> None:
        """規則①：成熟件無效非空、有效少數國 → 用有效國。"""
        kind, contributed = classify_ep_contribution(["DE", "FR", "GB"], invalid_is_blank=False)
        self.assertEqual(kind, "countries")
        self.assertEqual(contributed, ["DE", "FR", "GB"])

    def test_both_blank_is_missing(self) -> None:
        """有效與無效皆空 → 資料缺（精簡匯出），標 missing 現形。"""
        kind, contributed = classify_ep_contribution([], invalid_is_blank=True)
        self.assertEqual(kind, "missing")
        self.assertEqual(contributed, [])


class BuildFamilyCountryDatasetTests(unittest.TestCase):
    """家族×國家聚合的端到端純函式契約。"""

    def test_same_family_same_country_dedup(self) -> None:
        """同族同國兩件只產生一列，件數累計。"""
        result = build_family_country_dataset(
            [make_row(patent_id=1), make_row(patent_id=2)]
        )
        self.assertEqual(len(result.country_rows), 1)
        row = result.country_rows[0]
        self.assertEqual((row.family_id, row.country_code), ("F1", "US"))
        self.assertEqual(row.direct_patent_count, 2)
        self.assertEqual(row.via_ep_count, 0)

    def test_ep_default_contributes_as_bucket(self) -> None:
        """預設（申請國層級，expand_ep=False）：存活 EP 以「EP」桶直接貢獻，不展開。"""
        result = build_family_country_dataset(
            [make_row(country_code="EP", epc_valid="DE | FR", epc_invalid="AT")]
        )
        self.assertEqual(len(result.country_rows), 1)
        row = result.country_rows[0]
        self.assertEqual(row.country_code, "EP")
        self.assertEqual(row.direct_patent_count, 1)
        self.assertEqual(result.quality_rows[0].ep_in_transition_count, 0)
        self.assertEqual(result.quality_rows[0].ep_missing_epc_count, 0)

    def test_ep_expansion_merges_with_direct(self) -> None:
        """expand_ep=True：EP 生效國展開與非 EP 直接貢獻合併去重（DE 同時來自兩邊）。"""
        result = build_family_country_dataset(
            [
                make_row(patent_id=1, country_code="DE"),
                make_row(
                    patent_id=2,
                    country_code="EP",
                    epc_valid="DE | FR",
                    epc_invalid="AT | BE",
                ),
            ],
            expand_ep=True,
        )
        by_country = {r.country_code: r for r in result.country_rows}
        self.assertEqual(set(by_country), {"DE", "FR"})
        self.assertEqual(by_country["DE"].direct_patent_count, 1)
        self.assertEqual(by_country["DE"].via_ep_count, 1)
        self.assertEqual(by_country["FR"].via_ep_count, 1)

    def test_ep_in_transition_isolated_and_counted(self) -> None:
        """expand_ep=True：規則②的 EP 不進 country_rows，但 quality 計數現形。"""
        codes = " | ".join(f"C{i}" for i in range(35))
        result = build_family_country_dataset(
            [make_row(country_code="EP", epc_valid=codes, epc_invalid="  ")],
            expand_ep=True,
        )
        self.assertEqual(result.country_rows, [])
        self.assertEqual(result.quality_rows[0].ep_in_transition_count, 1)
        self.assertEqual(result.summary["ep_in_transition_count"], 1)

    def test_dead_excluded_pending_listed_separately(self) -> None:
        """dead 完全不列；pending 要列出國家但不計入 direct（2026-07-28 定案改版）。

        ⚠ 本測試前身斷言 `country_rows == []`（pending 也整筆消失）。使用者定案
        「有同族 ID 的都要能納入分析」後，pending／unknown 改為列出國家、以獨立欄計數
        ——原因是 WIPS 狀態欄名為 状态[US,JP,KR,CN,EP,CA,AU] 不含 TW，TW 恆為 unknown，
        舊行為讓本國市場整個從佈局圖消失。
        dead 維持排除：那是明確的「已無保護」，與「不知道」語意不同。
        """
        result = build_family_country_dataset(
            [
                make_row(patent_id=1, legal_status="到期(Expiration of the term)"),
                make_row(patent_id=2, legal_status="审查中"),
            ]
        )
        self.assertEqual(len(result.country_rows), 1, "pending 應列出、dead 不列")
        row = result.country_rows[0]
        self.assertEqual(row.direct_patent_count, 0, "pending 不得計入現有保護")
        self.assertEqual(row.pending_status_count, 1)
        self.assertEqual(result.quality_rows[0].pending_status_count, 1)
        self.assertEqual(result.summary["status_totals"]["dead"], 1)

    def test_unknown_status_surfaces(self) -> None:
        """未知状态要列出國家、以 unknown 欄計數，且不混入 direct。"""
        result = build_family_country_dataset([make_row(legal_status="沒看過的狀態")])
        self.assertEqual(len(result.country_rows), 1)
        self.assertEqual(result.country_rows[0].direct_patent_count, 0)
        self.assertEqual(result.country_rows[0].unknown_status_count, 1)
        self.assertEqual(result.quality_rows[0].unknown_status_count, 1)

    def test_surrogate_family_for_missing_family_id(self) -> None:
        """WIPS同族ID 空 → surrogate 單件家族，保護不丟且 flag 現形。"""
        result = build_family_country_dataset(
            [make_row(patent_id=99, family_id=None), make_row(patent_id=98, family_id="  ")]
        )
        family_ids = {r.family_id for r in result.country_rows}
        self.assertEqual(family_ids, {"P99", "P98"})
        self.assertTrue(all(r.is_surrogate_family for r in result.country_rows))
        self.assertEqual(result.summary["surrogate_family_count"], 2)

    def test_incomplete_family_detail(self) -> None:
        """明細 US-2 但實際只撈到 1 列 → incomplete＋逐桶 detail。"""
        counts = "US-2 | EP-0 | PCT-1 | JP-0 | KR-0 | CN-0 | etc-5"
        result = build_family_country_dataset([make_row(family_counts=counts)])
        qrow = result.quality_rows[0]
        self.assertTrue(qrow.family_incomplete)
        self.assertEqual(qrow.incomplete_detail, {"US": {"expected": 2, "actual": 1}})
        # PCT/etc 桶不比對，不出現在 detail。
        self.assertTrue(result.country_rows[0].family_incomplete)

    def test_complete_family_pct_etc_ignored(self) -> None:
        """五國桶對得上就完整；PCT/etc 差異不影響。"""
        counts = "US-1 | EP-0 | PCT-3 | JP-0 | KR-0 | CN-0 | etc-9"
        result = build_family_country_dataset([make_row(family_counts=counts)])
        self.assertFalse(result.quality_rows[0].family_incomplete)

    def test_completeness_counts_all_statuses(self) -> None:
        """完整性核對算的是「撈齊沒」，dead 列也算實際列數。"""
        counts = "US-2 | EP-0 | PCT-0 | JP-0 | KR-0 | CN-0 | etc-0"
        result = build_family_country_dataset(
            [
                make_row(patent_id=1, family_counts=counts),
                make_row(patent_id=2, legal_status="到期", family_counts=counts),
            ]
        )
        self.assertFalse(result.quality_rows[0].family_incomplete)

    def test_non_country_row_counted(self) -> None:
        """WO 等區域受理局（非 EP）不貢獻國家，計 non_country_row_count。"""
        result = build_family_country_dataset([make_row(country_code="WO")])
        self.assertEqual(result.country_rows, [])
        self.assertEqual(result.quality_rows[0].non_country_row_count, 1)

    def test_ep_missing_epc_surfaces(self) -> None:
        """expand_ep=True：存活 EP 但 EPC 兩欄皆空 → ep_missing_epc_count 現形（精簡匯出情境）。"""
        result = build_family_country_dataset(
            [make_row(country_code="EP", epc_valid=" ", epc_invalid=None)],
            expand_ep=True,
        )
        self.assertEqual(result.country_rows, [])
        self.assertEqual(result.quality_rows[0].ep_missing_epc_count, 1)


if __name__ == "__main__":
    unittest.main()
