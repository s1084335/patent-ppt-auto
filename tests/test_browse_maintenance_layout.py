"""瀏覽專利頁的維護區排版整理（2026-07-28 使用者定案「太亂了」）。

## 整理前的問題

    瀏覽專利
      [搜尋框][搜尋][產文獻備註] 只補空白…      ← 維護作業混進搜尋列
      [產生公司中文名草稿] 目前沒有待確認…       ← 公司名第一塊
      ▼ 專利權人代碼（正規化與中文名的基礎）
          33 個 chip 佔掉半個畫面
          第 1 組…
          [＋新增一組][AI 翻譯中文名][確定寫入]  ← 公司名第二塊，且鈕重複
      專利表…                                  ← 被推到很下面

三個毛病：
1. 維護作業與瀏覽混在一起，進頁面看不到專利表
2. **公司名的事被拆成兩塊**（草稿在上、代碼在下）——使用者因此問「有沒有連通」
3. **兩顆同功能按鈕**：`triggerZhNameDrafts()` 與 `translateCompanyNames()` 打的是
   **同一支** `POST /company-zh-drafts/generate`。下方那顆位置更誤導——看起來像
   「翻譯我現在填的這組」，實際是掃全庫已 confirmed 的代碼產草稿，與當前編輯的組無關。

## 四個改動（使用者確認）

1. 維護作業全收進「資料維護」details，平時收合
2. 公司名兩塊合併成一個框（代碼組 → 中文名草稿 → 既有代碼）
3. 移除下方重複的 AI 鈕，只留一顆
4. 待補清單 chip 收進 details

另加：流程說明（①建代碼組 → ②AI 翻中文 → ③裁決）——兩段式流程不直覺，
使用者今天就是因此才問。
"""
from __future__ import annotations

import re
import unittest

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


class MaintenanceCollapsedTests(unittest.TestCase):
    """① 維護作業收進可收合區，進頁面先看到專利表。"""

    @classmethod
    def setUpClass(cls):
        cls.html = client.get("/").text

    def test_maintenance_details_exists(self):
        self.assertIn("browse-maintenance", self.html)

    def test_note_button_inside_maintenance(self):
        """產文獻備註不得再留在搜尋列。"""
        body = re.search(r"async function renderBrowse\s*\([^)]*\)\s*\{(.*?)\n\}",
                         self.html, re.S)
        self.assertIsNotNone(body)
        src = body.group(1)
        toolbar = src[src.find('<div class="toolbar">'):src.find("</div>")]
        self.assertNotIn(
            "btn-patent-note", toolbar,
            "產文獻備註仍在搜尋列——維護作業應收進資料維護區")


class CompanySectionMergedTests(unittest.TestCase):
    """② 公司名兩塊合併成一個框。"""

    @classmethod
    def setUpClass(cls):
        cls.html = client.get("/").text

    def test_single_company_block(self):
        """代碼組與中文名草稿在同一個容器內。"""
        self.assertIn("company-name-block", self.html)

    def test_zh_drafts_inside_company_block(self):
        """草稿區掛點在合併後的框內，不另起一塊。"""
        block = re.search(r'id="company-name-block".*?</div>', self.html, re.S)
        self.assertIsNotNone(block, "找不到 company-name-block")

    def test_flow_hint_present(self):
        """③ 流程說明：兩段式不直覺，使用者今天因此發問。"""
        # 說明由多段字串 + 串接組成，故逐段檢查而非單一正規式。
        for phrase in ("建代碼組", "產生草稿", "裁決", "WIPS"):
            with self.subTest(phrase=phrase):
                self.assertIn(
                    phrase, self.html,
                    "缺流程說明——使用者不會知道要先寫入才按 AI，且結果出現在草稿區")


class SingleAiButtonTests(unittest.TestCase):
    """③ 只留一顆 AI 草稿按鈕。"""

    @classmethod
    def setUpClass(cls):
        cls.html = client.get("/").text

    def test_generate_endpoint_called_once(self):
        """/company-zh-drafts/generate 只能有一處**呼叫**。

        ⚠ 不能直接數整份 HTML 的出現次數——說明為何移除重複鈕的註解裡也有這個路徑，
        會被計進去（本測試初版即如此）。只數實際的 fetch 呼叫。
        """
        calls = re.findall(r"fetch\(API \+ '/company-zh-drafts/generate'", self.html)
        self.assertEqual(
            len(calls), 1,
            "有兩顆同功能按鈕——使用者會困惑差在哪，且下方那顆位置誤導")

    def test_duplicate_function_removed(self):
        """translateCompanyNames 已移除（功能與 triggerZhNameDrafts 完全重複）。"""
        self.assertNotIn("function translateCompanyNames", self.html)

    def test_remaining_button_kept(self):
        """保留的是語意正確的那顆（緊鄰草稿確認區）。"""
        self.assertIn("triggerZhNameDrafts", self.html)


class PendingChipsCollapsedTests(unittest.TestCase):
    """④ 待補清單收合——33 個 chip 佔掉半個畫面，只在填變體時才需要。"""

    @classmethod
    def setUpClass(cls):
        cls.html = client.get("/").text

    def test_pending_details_exists(self):
        self.assertIn("pending-codes-details", self.html)


class NoteCoverageHintTests(unittest.TestCase):
    """順帶：文獻備註顯示還缺幾筆，不然看不出要不要按。"""

    @classmethod
    def setUpClass(cls):
        cls.html = client.get("/").text

    def test_coverage_hint_rendered(self):
        self.assertRegex(self.html, r"function\s+renderNoteCoverage\s*\(")


if __name__ == "__main__":
    unittest.main()
