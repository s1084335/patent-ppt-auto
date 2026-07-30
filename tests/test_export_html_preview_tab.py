"""單頁 HTML 改為分頁預覽（2026-07-31 使用者定案）。

## 定案

「不要按下去就下載，而是出現一個分頁讓使用者看，真的要下載再按下載就好。」

| 舊流程 | 新流程 |
|---|---|
| 按鈕 → 確認框（統計） → 確認並下載 → 直接落檔 | 按鈕 → **開新分頁顯示完整報告** → 分頁內工具列「下載 HTML／列印」 |

## 實作要點

- ⚠ **先同步 `window.open` 再組 HTML**：`buildExportHtml` 要 fetch 圖檔轉 data URI
  （非同步），await 之後才開分頁會被瀏覽器彈窗攔截擋掉（不在使用者手勢內）。
  先開空分頁佔位，組完再寫入。
- 分頁內工具列自足（不依賴母頁）：下載＝序列化自身 DOM、剔除工具列後存檔；
  列印走 `window.print()`。`@media print` 隱藏工具列。
- 確認框流程（`confirmExportOutput`／`cancelExportOutput`）退場——預覽分頁本身
  就是確認。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parents[1] / "backend" / "app" / "static" / "index.html"


def _js_function(html: str, name: str) -> str:
    m = re.search(rf'(async\s+)?function {re.escape(name)}\([^)]*\) \{{.*?\n\}}', html, re.S)
    assert m, f"找不到 function {name}"
    return m.group(0)


class PreviewTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")
        cls.review = _js_function(cls.html, "reviewExportOutput")

    def test_opens_tab_before_async_build(self):
        """🔴 window.open 必須在 await buildExportHtml **之前**（彈窗攔截）。

        ⚠ 量**程式碼**的順序，先剔除註解行——說明註解裡也會提到這兩個名字。
        """
        code = "\n".join(l for l in self.review.split("\n")
                         if not l.strip().startswith("//"))
        open_at = code.find("window.open")
        build_at = code.find("buildExportHtml")
        self.assertGreater(open_at, -1, "reviewExportOutput 未開新分頁")
        self.assertGreater(build_at, -1, "reviewExportOutput 未組 HTML")
        self.assertLess(open_at, build_at,
                        "window.open 在 await 之後——會被彈窗攔截擋掉")

    def test_no_auto_download(self):
        """🔴 按下去不得直接落檔。"""
        self.assertNotIn("a.download", self.review, "仍在自動下載")
        self.assertNotIn("確認並下載", self.html, "舊確認框文案仍在")

    def test_confirm_dialog_retired(self):
        for gone in ("confirmExportOutput", "cancelExportOutput"):
            with self.subTest(gone=gone):
                self.assertNotIn(f"function {gone}", self.html,
                                 f"確認框流程 {gone} 未退場")

    def test_tab_has_download_toolbar(self):
        """分頁內要有自足的下載／列印工具列，且列印時隱藏。"""
        self.assertIn("下載 HTML", self.html)
        self.assertIn("window.print()", self.html)
        self.assertIn("export-toolbar-bar", self.html, "缺工具列掛點")
        self.assertRegex(self.html, r"@media print[^}]*#export-toolbar-bar",
                         "列印未隱藏工具列")

    def test_button_relabeled(self):
        """按鈕改名反映新行為（不再是直接匯出）。"""
        self.assertIn("單頁 HTML 預覽", self.html)


if __name__ == "__main__":
    unittest.main()
