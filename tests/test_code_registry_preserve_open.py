"""代碼區重繪保留展開狀態（2026-08-12 使用者回饋）。

## 問題

「確定寫入資料庫」成功（以及標不歸戶／還原）後 `renderCompanyCodeRegistry()`
整區 innerHTML 重繪，所有 `<details>`——外框「專利權人代碼」、待補清單、
資料庫已有的代碼、各代碼組、不歸戶清單——全部回到預設收合。
使用者原話：「填變體那區塊，成功後 refresh 會整個展開的再收回去」。

## 契約

重繪前收集 `details[open]` 的穩定鍵（id 或 data-key），重繪後回開。
各代碼組的 details 原本無 id，補 `data-key`（以代碼為鍵——重繪後仍對得回同一組）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

INDEX_HTML = (Path(__file__).resolve().parents[1] / "backend" / "app"
              / "static" / "index.html")


class RegistryPreserveOpenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_helpers_exist(self):
        self.assertIn("function collectOpenDetailKeys", self.html,
                      "缺重繪前收集展開狀態的 helper")
        self.assertIn("function restoreOpenDetailKeys", self.html,
                      "缺重繪後回開的 helper")

    def test_registry_render_preserves_open_state(self):
        match = re.search(
            r"async function renderCompanyCodeRegistry\(.*?\n\}", self.html, re.DOTALL)
        self.assertIsNotNone(match)
        fn = match.group(0)
        self.assertIn("collectOpenDetailKeys", fn,
                      "重繪前未收集展開狀態——寫入成功後展開的區塊會全部收合")
        self.assertIn("restoreOpenDetailKeys", fn, "重繪後未回開")

    def test_existing_group_details_have_stable_key(self):
        """各代碼組 details 要帶 data-key（代碼為鍵）——沒有穩定鍵重繪後對不回同一組。"""
        match = re.search(
            r"function existingGroupHtml\(.*?\n\}", self.html, re.DOTALL)
        self.assertIsNotNone(match)
        self.assertRegex(match.group(0), r"<details[^>]*data-key=",
                         "代碼組 details 缺 data-key")

    def test_not_grouped_details_have_stable_key(self):
        match = re.search(
            r"function renderNotGroupedNames\(.*?\n\}", self.html, re.DOTALL)
        self.assertIsNotNone(match)
        self.assertRegex(match.group(0), r"<details[^>]*(id=|data-key=)",
                         "不歸戶清單 details 缺穩定鍵")


if __name__ == "__main__":
    unittest.main()
