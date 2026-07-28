"""狀態未知／審查中也要納入家族國別分析（2026-07-28 使用者定案）。

實機發現：國家佈局圖完全看不到 TW。查清結果——**不是 bug，是資料來源沒有 TW 狀態**：
WIPS 匯出的狀態欄名為 `状态[US,JP,KR,CN,EP,CA,AU]`，欄名本身就列明涵蓋範圍不含 TW。
實測 CN 39／US 9／EP 3 全部有值，TW 9 筆全空。

於是 `normalize_legal_status` 一律回 unknown，而 `build_family_layout` 對 unknown 與
pending 都 `continue` 跳過，不進 coverage → TW 那 9 筆（同族 ID 100% 齊全、其中 6 筆是
自家 M 開頭新型）在佈局圖上完全消失，報表少了本國市場。

使用者定案：**有同族 ID 的都要能納入分析，不分國家**。狀態未知不等於沒有這件專利。

⚠ 但不得污染「現有保護」語意：`family_country_layout` 報表的定義是**現有保護國家佈局**
（按家族去重、只算存活）。若把 unknown 直接加進 direct_patent_count，等於宣稱那些
狀態不明的專利「確定還有保護」——那是捏造。

故採第三槽：新增 `unknown_status_count` 欄，與 direct／via_ep 並列但**分開計數**，
讓呈現層能顯示「CN 25 家族（另 2 家族狀態未知）」這種誠實的資訊。
"""
from __future__ import annotations

import unittest

from backend.app.transforms.family_layout import build_family_country_dataset as build_family_layout


def _row(family_id, country, status, **kw):
    base = {
        "family_id": family_id,
        "country_code": country,
        "legal_status": status,
        "epc_valid": None,
        "epc_invalid": None,
        "family_counts": None,
    }
    base.update(kw)
    return base


class UnknownStatusEnteredCoverageTests(unittest.TestCase):
    """狀態未知的專利要出現在國別列，而不是整筆消失。"""

    def test_unknown_status_country_appears(self):
        """TW 情境：同族 ID 有、狀態欄空 → 仍要在國別佈局中看得到 TW。"""
        rows = [
            _row("F1", "CN", "授权"),
            _row("F1", "TW", None),          # WIPS 不提供 TW 狀態
        ]
        result = build_family_layout(rows)
        countries = {r.country_code for r in result.country_rows}
        self.assertIn(
            "TW", countries,
            "狀態未知的 TW 整筆從佈局消失——使用者定案要求有同族 ID 就納入分析")

    def test_unknown_counted_separately_from_alive(self):
        """不得混進 direct_patent_count——那欄語意是「確定還有保護」。"""
        rows = [
            _row("F1", "CN", "授权"),
            _row("F1", "TW", None),
        ]
        result = build_family_layout(rows)
        tw = next(r for r in result.country_rows if r.country_code == "TW")
        cn = next(r for r in result.country_rows if r.country_code == "CN")
        self.assertEqual(cn.direct_patent_count, 1, "存活件仍走 direct")
        self.assertEqual(
            tw.direct_patent_count, 0,
            "狀態未知不得計入 direct_patent_count——那等於宣稱它確定還有保護")
        self.assertEqual(
            getattr(tw, "unknown_status_count", None), 1,
            "缺少 unknown_status_count 欄：無法誠實區分「存活」與「狀態不明」")

    def test_pending_also_included(self):
        """審查中同理納入（其他國家也一樣，非 TW 專屬）。"""
        rows = [_row("F1", "US", "审查中")]
        result = build_family_layout(rows)
        us = next((r for r in result.country_rows if r.country_code == "US"), None)
        self.assertIsNotNone(us, "審查中整筆消失")
        self.assertEqual(us.direct_patent_count, 0)
        self.assertEqual(getattr(us, "pending_status_count", None), 1)

    def test_dead_still_excluded(self):
        """失效／到期仍不計入——那是明確的「已無保護」，與未知不同。"""
        rows = [_row("F1", "CN", "到期(Expiration of the term)")]
        result = build_family_layout(rows)
        cn = next((r for r in result.country_rows if r.country_code == "CN"), None)
        if cn is not None:
            self.assertEqual(cn.direct_patent_count, 0)
            self.assertEqual(getattr(cn, "unknown_status_count", 0), 0)
            self.assertEqual(getattr(cn, "pending_status_count", 0), 0)

    def test_quality_counters_unchanged(self):
        """品質表的 unknown/pending 計數仍要照舊（那是可信度儀表板，不受本次改動影響）。"""
        rows = [
            _row("F1", "CN", "授权"),
            _row("F1", "TW", None),
            _row("F1", "US", "审查中"),
        ]
        result = build_family_layout(rows)
        q = result.quality_rows[0]
        self.assertEqual(q.unknown_status_count, 1)
        self.assertEqual(q.pending_status_count, 1)


if __name__ == "__main__":
    unittest.main()
