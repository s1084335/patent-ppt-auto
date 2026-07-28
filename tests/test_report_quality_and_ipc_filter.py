"""兩件呈現正確性（2026-07-28 使用者指出）。

## 一、洛迦諾分類不得混進 IPC 圖

CN 11 筆外觀設計的 `Orig. IPC(Main)` 是 `21-02`／`19-07`——那是**洛迦諾分類**
（Locarno，外觀設計專用），不是 IPC。WIPS 把它塞進同一個欄位，報表沒分辨就一起統計：

    Level 4 圖：A63B 47、**2102 10**、F03G 2、**1907 1**   ← 兩個假 subclass
    Level 5 圖：**21-02**、**19-07** 混在真 IPC 主組之間

後果不只是多兩根長條：60 筆裡有 11 筆不是發明專利的 IPC，**集中度佔比被稀釋**。
使用者原話：「洛迦諾分類不要混進 IPC 圖」。

IPC 格式：一律 `[A-H]` 開頭（八大部）。洛迦諾是 `NN-NN` 純數字，可穩定辨別。

## 二、家族品質不能只換位置

原本 52 列品質資料掛在 links 當 JSON 下載。我提議「提升為卡片 variant」，
使用者直接點破：**「就算做成卡片，內容跟 json 一樣，那還是不會被看」**。

實查那 52 列：**只有 3 列有事**（都是 US 應有 2 筆、實際只撈到 1 筆），
其餘 49 列完全正常。整包列出來當然沒人看。

正解＝**把異常摘要提到卡片 note 上**（不必主動點就看得到），明細仍留 JSON。
沒有異常時明講「家族資料完整」，不要沉默——沉默無法區分「沒問題」與「沒檢查」。
"""
from __future__ import annotations

import unittest


class LocarnoExcludedFromIpcTests(unittest.TestCase):
    """IPC／CPC 報表只收真正的 IPC 代碼。"""

    def test_ipc_definition_filters_non_ipc(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        d = REPORT_DEFINITIONS["ipc_main_distribution"]
        patterns = dict(getattr(d, "value_pattern_columns", ()) or ())
        self.assertIn(
            "Orig. IPC(Main)", patterns,
            "IPC 報表沒有格式過濾——洛迦諾分類（21-02／19-07）會混進來當假 subclass")

    def test_cpc_definition_filters_non_cpc(self):
        """CPC 同樣是 [A-H] 開頭（含 Y 部）。"""
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        d = REPORT_DEFINITIONS["cpc_main_distribution"]
        patterns = dict(getattr(d, "value_pattern_columns", ()) or ())
        self.assertIn("Orig. CPC(Main)", patterns, "CPC 報表缺格式過濾")

    def test_engine_applies_the_pattern(self):
        """定義有宣告還不夠——引擎要真的把它組進 WHERE，否則仍會混入。"""
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS
        from backend.app.reports.report_engine import build_report_sql

        sql, params = build_report_sql(
            REPORT_DEFINITIONS["ipc_main_distribution"], None, None)
        self.assertIn("~", sql, "SQL 沒有正規式比對——過濾沒生效")
        self.assertTrue(
            any("^[A-HY]" in str(v) for v in params.values()),
            "正規式沒進參數（應走參數綁定，不拼字串）")

    def test_helper_recognises_locarno(self):
        """辨別函式：洛迦諾是 NN-NN 純數字，IPC 是 [A-H] 開頭。"""
        from backend.app.reports.report_definitions import is_ipc_like

        for bad in ("21-02", "19-07", "2102", ""):
            with self.subTest(value=bad):
                self.assertFalse(is_ipc_like(bad), f"{bad!r} 不該被當成 IPC")
        for good in ("A63B", "A63B-069/18", "F03G", "H01M"):
            with self.subTest(value=good):
                self.assertTrue(is_ipc_like(good))


class FamilyQualitySurfacedTests(unittest.TestCase):
    """家族品質異常要提到卡片上，不是換個位置繼續藏。"""

    def test_summary_builder_exists(self):
        from backend.app.reports.chart_runner import family_quality_note

        self.assertTrue(callable(family_quality_note))

    def test_flags_incomplete_families(self):
        """有異常時要講清楚幾件、什麼異常。"""
        from backend.app.reports.chart_runner import family_quality_note

        rows = [
            {"family_id": "A", "family_incomplete": True, "is_surrogate_family": False},
            {"family_id": "B", "family_incomplete": False, "is_surrogate_family": False},
            {"family_id": "C", "family_incomplete": True, "is_surrogate_family": True},
        ]
        note = family_quality_note(rows)
        self.assertIn("2", note, "沒點出不完整家族數")
        self.assertIn("3", note, "沒交代總家族數作分母")

    def test_says_ok_when_clean(self):
        """沒異常也要明講——沉默無法區分「沒問題」與「沒檢查」。"""
        from backend.app.reports.chart_runner import family_quality_note

        rows = [{"family_id": "A", "family_incomplete": False, "is_surrogate_family": False}]
        note = family_quality_note(rows)
        self.assertTrue(note.strip(), "無異常時回空字串——使用者無從得知已檢查過")

    def test_note_attached_to_section(self):
        """摘要要真的掛進 section 的 note，不是只有函式存在。"""
        import inspect
        from backend.app.reports import chart_runner

        src = inspect.getsource(chart_runner._build_family_layout_section)
        self.assertIn("family_quality_note", src,
                      "算了摘要卻沒掛上卡片——等於仍然沒人看得到")


if __name__ == "__main__":
    unittest.main()
