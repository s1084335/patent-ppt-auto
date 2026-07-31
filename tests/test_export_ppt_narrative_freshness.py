"""產生 PPT 前的 narrative 判斷不得靠過期快取（2026-07-31 實機 #149）。

## 問題

使用者按「產生 PPT」，前端卻先跑了一次 `ai:narrative`（#149）——⚠ 但該版本
兩分半鐘前才由 #148 產完解讀（API 實測回傳 19 個變體都有 narrative、
`narratives_expired: false`）。等於白等 8 分鐘＋多花一次 AI 額度。

## 根因：兩層落點沒連動

`exportReportHasNarratives()` 讀的是 `exportPreview.content`——**匯出頁載入當下
的快取**。而 `maybeRefreshReportNarratives()`（narrative 成功後的刷新）只呼叫
`reloadCurrentReportContentOnly()`，那支刷的是**報表種類頁**的 `#report-inline-view`，
完全沒碰匯出頁的快取。於是匯出頁的 content 永遠停在「解讀還沒產」的那一刻。

⚠ 同型於 2026-07-30 的「版本列管展開、#report-inline-view 管內容，兩個落點沒連動」。

## 定案：決策點一律用即時資料

「要不要跑 narrative」是**會花 8 分鐘與 AI 額度的決策**，不能建立在可能過期的
快取上。`requestExportPpt` 決策前重抓一次該版本 content（content 端點已於
本日修為 ~1 秒），依即時結果判斷；順帶把重抓結果寫回快取。

⚠ 並補上 narrative 成功後刷新匯出頁快取（顯示面同步，避免使用者看到舊狀態）。
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


class DecideOnFreshDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_request_ppt_refetches_before_deciding(self):
        """🔴 決策前要重抓 content，不得只讀快取。"""
        body = _js_function(self.html, "requestExportPpt")
        code = "\n".join(l for l in body.split("\n") if not l.strip().startswith("//"))
        self.assertIn("await refreshExportContent", code,
                      "requestExportPpt 未重抓即時 content——會依過期快取誤跑 narrative")
        refresh_at = code.find("refreshExportContent")
        check_at = code.find("exportReportHasNarratives")
        self.assertLess(refresh_at, check_at, "重抓必須在判斷之前")

    def test_refresh_helper_exists_and_writes_back(self):
        """重抓函式要更新快取（後續渲染才不會又用到舊的）。"""
        body = _js_function(self.html, "refreshExportContent")
        self.assertIn("/reports/versions/", body, "未打 content 端點")
        self.assertIn("exportPreview.content =", body, "重抓結果未寫回快取")

    def test_narrative_success_refreshes_export_cache(self):
        """⚠ narrative 完成後也要刷匯出頁快取（顯示面同步）。"""
        body = _js_function(self.html, "maybeRefreshReportNarratives")
        self.assertIn("refreshExportContent", body,
                      "narrative 成功後沒刷匯出頁——使用者仍看到舊狀態")


if __name__ == "__main__":
    unittest.main()
