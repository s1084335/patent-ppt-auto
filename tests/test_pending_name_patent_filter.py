"""待補標籤依欄位篩專利（2026-07-29 使用者需求）。

## 問題

使用者：「如果專利權人標籤拿去搜尋，但這間公司的欄位，在前端沒有出現，這樣我無法篩選」。

待補清單的標籤知道自己來自哪個欄位（後端已回 `source_fields`），但前端只拿它當
tooltip；點標籤只會「填入變體格」，沒有「先看看這是哪些專利」的路。

而現有的專利搜尋（`_LIST_WHERE`）只吃三欄：

    patent_number / title / applicant_display_name

⚠ 兩個致命點：
1. **專利權人與受讓人不在搜尋範圍**——搜這兩欄來的名稱一律 0 筆。
2. 待補清單是從**原始欄位**（WIPS 原文如「最近專利權人[US,JP,...]」）算的，
   而搜尋比對的是 `report_patent_base` 的**收斂顯示名**——兩者對不上，
   就算把欄位加進 `_LIST_WHERE` 也查不到原文標籤。

失敗是靜默的：回 0 筆看起來像「這家公司沒有專利」，實際是搜尋沒看那一欄。

## 定案（使用者 2026-07-29）

- 用**標籤自己的來源欄位**精準篩，不丟進全文搜尋。
- **任一欄命中就列出**（`source_fields` 可能有多個，不必分開列）。
- 前端**欄位不變動**（不動 `PATENT_COLUMNS`）。
- **篩選結果跳到完整專利表格**（使用者 2026-07-29 改定；原議「就地展開」已否決）——
  完整表格才看得到全部欄位與「詳細查看連結」，使用者點連結去 WIPS 判斷是不是同一家。
- 標籤保持「點擊＝填入變體格」不變，另加放大鏡圖示做篩選（使用者選 C 案）。

⚠ 跳頁會離開正在編輯的代碼組。`companyCodeGroups` 是 JS 變數不是 DOM，
切頁不會遺失；但**必須確認回來時內容還在**（見 UI 測試）。

## 契約

`GET /company-codes/pending/{lookup_key}/patents` → `{items: [{patent_id, patent_number,
title, detail_url, matched_fields}], total}`

⚠ 比對走 `lookup_key`（normalize 後的小寫、空白收斂），與待補清單同一把 key，
不是原字面——否則大小寫或多空白就對不上。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_PATH = PROJECT_ROOT / "backend" / "app" / "api" / "company_aliases.py"
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"


class PendingPatentsEndpointTests(unittest.TestCase):
    """後端：依待補名稱查專利的端點。"""

    @classmethod
    def setUpClass(cls):
        cls.src = API_PATH.read_text(encoding="utf-8")

    def test_endpoint_registered(self):
        """端點要存在——現有搜尋對不上原文標籤，必須另開一支。"""
        self.assertRegex(
            self.src, r'@router\.get\(\s*["\'][^"\']*pending/\{[^}]+\}/patents',
            "缺「依待補名稱查專利」端點")

    def test_queries_all_three_source_columns(self):
        """三個原始欄位都要查（申請人／最近專利權人／最近受讓人）。

        ⚠ 必須用**原始 WIPS 欄位**，與待補清單的來源一致；
        用 report_patent_base 的收斂顯示名會對不上原文標籤。
        """
        block = self._patents_sql()
        for col in ("申請人", "最近專利權人", "最近受讓人"):
            with self.subTest(column=col):
                self.assertIn(col, block, f"查詢未涵蓋原始欄位 {col}")

    def test_splits_multi_value_names(self):
        """`A | B` 要拆開比對，否則共同持有的第二方永遠查不到。"""
        block = self._patents_sql()
        self.assertIn("regexp_split_to_table", block,
                      "未拆分 `|` 多值——共同申請／共同持有的第二方會漏")

    def test_matches_on_lookup_key_not_raw(self):
        """比對走 normalize 後的 lookup_key，與待補清單同一把 key。"""
        block = self._patents_sql()
        self.assertIn("lower(", block, "未 normalize，大小寫不同就對不上")
        self.assertIn("regexp_replace", block, "未收斂空白，多一個空格就對不上")

    def test_returns_detail_url(self):
        """使用者靠「詳細查看連結」去 WIPS 判斷，此欄必須回傳。"""
        block = self._patents_sql()
        self.assertIn("詳細查看連結", block,
                      "缺 detail_url——使用者無從判斷這是不是同一家公司")

    def _patents_sql(self) -> str:
        """取出該端點使用的 SQL 常數內容。"""
        match = re.search(
            r"_PENDING_NAME_PATENTS_SQL\s*=\s*(?:f?\"\"\"|f?''')(.*?)(?:\"\"\"|''')",
            self.src, re.S)
        self.assertIsNotNone(match, "找不到 _PENDING_NAME_PATENTS_SQL")
        return match.group(1)


class PendingChipFilterUiTests(unittest.TestCase):
    """前端：標籤旁的放大鏡入口（使用者選 C 案）。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_filter_function_exists(self):
        self.assertIn("function showPendingNamePatents", self.html,
                      "缺篩選函式")

    def test_chip_keeps_fill_on_click(self):
        """⚠ 標籤本體點擊仍是「填入變體格」——使用者定「先試試 C」，
        既有操作不變，篩選另給入口。"""
        self.assertIn("fillVariantFromPending(", self.html,
                      "填入變體的既有行為不得移除")

    def test_chip_has_filter_entry(self):
        """放大鏡圖示：點它才篩選，不影響原本點標籤填入。"""
        self.assertIn("showPendingNamePatents(", self.html, "標籤缺篩選入口")

    def test_navigates_to_full_table(self):
        """跳到完整專利表格（使用者 2026-07-29 定：「篩選用跳到完整表格」）。

        完整表格才看得到全部欄位與「詳細查看連結」——使用者靠那個連結去 WIPS
        判斷是不是同一家公司。
        2026-08-11 契約補充：navTo 降為「不在瀏覽頁」的 fallback（🔍 本來就住在
        瀏覽頁的資料維護區，正常路徑見下一測試），但入口必須保留。
        """
        body = self._filter_fn()
        self.assertIn("navTo(", body, "未跳頁——使用者看不到完整欄位與查看連結")

    def test_reload_only_patent_block_when_on_browse(self):
        """🔴 2026-08-11 使用者裁決：「按了搜尋鏡後不要整頁載入，就載入專利那區塊」。

        原本一律 navTo('browse') → renderBrowse 整頁重繪，資料維護／待補代碼
        details 全部收合、代碼區重載——「每次這樣都被收起來很痛苦」。
        已在瀏覽頁時必須只重載 #browse-body（loadBrowsePatents），維護區 DOM 不動。
        """
        body = self._filter_fn()
        self.assertIn("loadBrowsePatents(", body,
                      "應直接重載專利區塊，而不是整頁重繪")
        self.assertRegex(body, r"state\.nav\s*===\s*'browse'",
                         "缺『已在瀏覽頁』判斷——仍會整頁重繪把維護區收合")

    def test_filter_state_carried(self):
        """篩選條件要帶過去，不是只跳頁然後顯示全部 60 筆。"""
        body = self._filter_fn()
        self.assertRegex(
            body, r"state\.\w*[Pp]ending\w*|pendingNameFilter",
            "未帶篩選條件，跳過去會是未篩選的全庫清單")

    def test_code_groups_survive_navigation(self):
        """⚠ 跳頁後回來，填到一半的代碼組必須還在。

        `companyCodeGroups` 是模組層變數（非 DOM），切頁不會被清空——
        本測試鎖住它不被誰在跳頁路徑上重置。
        """
        body = self._filter_fn()
        self.assertNotIn("companyCodeGroups = [", body,
                         "跳頁時重置了代碼組——使用者填到一半的內容會消失")

    def test_filter_is_clearable(self):
        """要能清掉篩選回到全部——否則使用者被困在篩選狀態。"""
        self.assertIn("clearPendingNameFilter", self.html,
                      "缺清除篩選的入口")

    def _filter_fn(self) -> str:
        match = re.search(
            r"function showPendingNamePatents\(.*?\n\}", self.html, re.S)
        self.assertIsNotNone(match, "找不到 showPendingNamePatents")
        return match.group(0)


if __name__ == "__main__":
    unittest.main()
