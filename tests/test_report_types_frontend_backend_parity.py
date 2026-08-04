"""前端報表清單必須與後端定義一致（2026-07-29 使用者實機回報）。

## 問題

使用者按「產製選定報表」，畫面跳：

    建立報表工作失敗：unknown report_names: recent_assignee_ranking

一張報表都產不出來——送出是整批，一個名字不認得就整批 400，
而該項預設是勾選的，等於「全選」必定失敗。

## 根因

使用者定案「最新受讓人數據整合到主要申請人排名去」後，後端移除了
`recent_assignee_ranking` 定義、資料併進 `applicant_ranking`，
**但前端 `REPORT_TYPES` 那一行沒刪**。

同一份清單前後端各維護一份——本專案反覆出現的「同一概念兩處實作」，
且失敗是靜默的（前端照樣渲染勾選框，直到送出才炸）。

## 本測試鎖什麼

前端 `REPORT_TYPES` 的 key 集合 ⊆ 後端定義。前端多出來的 key＝
使用者看得到但按下去會 400 的死選項。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"


def _frontend_report_names() -> list[str]:
    """取前端 REPORT_TYPES 的 key（第一個字串）。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(r"const REPORT_TYPES = \[(.*?)\];", html, re.S)
    assert match, "找不到 REPORT_TYPES 宣告"
    return re.findall(r"\['([a-z_]+)'", match.group(1))


def _backend_report_names() -> set[str]:
    from backend.app.reports.report_definitions import REPORT_DEFINITIONS

    if isinstance(REPORT_DEFINITIONS, dict):
        return set(REPORT_DEFINITIONS)
    return {definition.name for definition in REPORT_DEFINITIONS}


class ReportTypeParityTests(unittest.TestCase):
    def test_no_frontend_only_report(self):
        """🔴 前端多出來的報表名＝畫面上按了會 400 的死選項。"""
        backend = _backend_report_names()
        orphans = [name for name in _frontend_report_names() if name not in backend]
        self.assertEqual(
            orphans, [],
            f"前端 REPORT_TYPES 有後端未定義的報表：{orphans}——"
            "使用者會看到勾選框，送出時整批 400")

    def test_removed_report_is_gone(self):
        """`recent_assignee_ranking` 已整合進 applicant_ranking，不得再出現。"""
        self.assertNotIn(
            "recent_assignee_ranking", _frontend_report_names(),
            "最新受讓人排名已整合進主要申請人排名（使用者定案），前端不應再列")

    def test_backend_reports_all_reachable(self):
        """反向：後端定義的報表前端都要列得出來，否則使用者選不到。

        ⚠ 例外＝`requires_market_data` 者：市場線尚未實作，這些報表刻意不給選
        （見 MarketDataReportHiddenTests），故不計入「使用者無從選取」的缺漏。
        """
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        front = set(_frontend_report_names())
        unreachable = sorted(
            name for name in _backend_report_names()
            if name not in front and not REPORT_DEFINITIONS[name].requires_market_data)
        self.assertEqual(
            unreachable, [],
            f"後端有定義但前端沒列出的報表：{unreachable}——使用者無從選取")


class MarketDataReportHiddenTests(unittest.TestCase):
    """需市場資料的報表在市場線實作前整個藏起來（2026-07-29 使用者定案）。

    使用者原話：「現在就直接把痛點四象限整個藏起來（等市場線做好再放出來），
    那就沒有備案需要了」。

    ## 為什麼是「藏起來」不是「產出灰帶」

    原設計缺市場資料時仍產圖，痛點軸全標 unknown（待調查）。問題在於**產出的圖
    看不出它不完整**——匯進 PPT 後會被讀成「痛點都很低」，比不產更糟。

    ## 兩個落點都要擋

    1. 前端 `REPORT_TYPES`：不列出＝使用者勾不到
    2. 後端 `DEFAULT_REPORT_NAMES`：預設批次排除＝「全選」或未指定時不產

    ⚠ 只擋前端不夠：DEFAULT_REPORT_NAMES 是後端的預設批次，API 直呼仍會產。
    """

    def test_market_data_reports_not_in_frontend(self):
        """需市場資料的報表不得出現在前端勾選清單。"""
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        front = set(_frontend_report_names())
        exposed = sorted(
            name for name, definition in REPORT_DEFINITIONS.items()
            if definition.requires_market_data and name in front)
        self.assertEqual(
            exposed, [],
            f"需市場資料的報表仍可勾選：{exposed}——市場線未實作，產出的圖痛點軸全是待調查")

    def test_market_data_reports_not_in_defaults(self):
        """需市場資料的報表不得進預設批次（否則『全選』仍會產）。"""
        from backend.app.reports.report_definitions import (
            DEFAULT_REPORT_NAMES, REPORT_DEFINITIONS)

        leaked = sorted(
            name for name in DEFAULT_REPORT_NAMES
            if REPORT_DEFINITIONS[name].requires_market_data)
        self.assertEqual(
            leaked, [],
            f"預設批次含需市場資料的報表：{leaked}")

    # 🔴 2026-08-04：test_definition_still_exists 已刪除——痛點板定義已刪，「定義保留待市場線」的前提不存在（市場線也已定案移除）

if __name__ == "__main__":
    unittest.main()
