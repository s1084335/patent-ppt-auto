"""報表內容要跟著版本列展開／收合（2026-07-30 使用者實機回報）。

## 使用者原話

「我重產報告後，舊的報表無法再出現」「報表要能跟隨這裡收起來或出現」（附版本列表截圖）。

## 查證結果

資料與 API 都正常——DB 有兩版，`GET /reports/versions` 也回兩筆：

    report_trial_20260729_171147  （最新）
    report_trial_20260729_164537  （含 AI 解讀）

問題在**前端有兩個落點**：

| 落點 | 內容 |
|---|---|
| `#report-version-body-<版本>` | 版本列展開的容器——**只放 PPT 清單** |
| `#report-inline-view` | **報表本體**，由上方檢視選單驅動 |

`loadReportVersionContent` 的 `if (reportViewContent === null) fillReportViewSelect(content)`
——⚠ **只有第一次載入才餵選單**，展開舊版時刻意不搶。

於是使用者點舊版：容器展開了、但裡面只有 PPT 清單，報表本體仍停在最新版，
看起來就是「舊的報表出不來」。收合亦然：報表本體不受影響。

## 定案

展開某版＝**該版的報表本體要跟著出現**；收合＝跟著收起。
⚠ 同時只能有一版展開（否則兩份報表同時顯示，回到使用者說過的「不同報表混在一起」）。

## 🔴 2026-08-12 契約更新：機制換成下拉選單，需求不變

使用者定案「版本做成選單列表放在檢視跟產生全部解讀中間」——縱向版本列表
（含同日稍早的緊湊列）把報表本體擠到畫面下方，改為標頭下拉。

| 原需求 | 新機制下的落實 |
|---|---|
| 展開某版 → 該版內容出現 | 選單切換 → `loadReportVersionContent`／快取餵 `fillReportViewSelect`（**不變**） |
| 收合 → 內容收起 | **不再適用**：下拉沒有「收合」語意，永遠顯示選中的那一版 |
| 同時只能開一版 | 由 `<select>` **單選天然保證**，不需 `closeOtherReportVersions` |
| 已載過不重抓 | `reportVersions.loaded` 快取（**不變**） |

⚠ 本檔原斷言 `toggleReportVersion`／`clearReportInlineView` 兩個函式，
它們隨列表機制一起移除；斷言改綁新機制。⚠ 這兩支是 2026-08-12 改版當下的
**範圍漏網**（當時只跑 `test_api_frontend`／`test_frontend_js_syntax`），
在 `restructure-html-report-export` 分支上一併補正。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"


def _fn(name: str) -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(
        r"(?:async )?function " + re.escape(name) + r"\(.*?\n\}", html, re.S)
    assert match, f"找不到 {name}"
    return match.group(0)


class VersionSelectionDrivesContentTests(unittest.TestCase):
    """選中哪一版，畫面就顯示哪一版（原「展開／收合連動」的現行形）。"""

    def test_selecting_version_switches_content(self):
        """🔴 切到某版時要把該版內容餵給檢視區。

        原始缺陷：`if (reportViewContent === null)` 只在首次載入時餵，
        切舊版不搶——使用者看到容器展開了卻沒有報表。這條守到今天。
        """
        body = _fn("loadReportVersionContent")
        self.assertNotIn(
            "if (reportViewContent === null) fillReportViewSelect(content)", body,
            "仍只在首次載入餵選單，切到舊版看不到該版報表")
        self.assertIn("fillReportViewSelect(content)", body,
                      "未把選中版本的內容餵給檢視區")

    def test_switch_handler_loads_or_reuses(self):
        """選單切換入口：已載過走快取、沒載過打 API——兩條路都要在。"""
        body = _fn("onReportVersionChange")
        self.assertIn("showLoadedReportVersion", body, "已載過的版本應走快取")
        self.assertIn("loadReportVersionContent", body, "未載過的版本應打 API")

    def test_only_one_version_shown(self):
        """⚠ 同時只能顯示一版——兩份報表混在畫面上是原始痛點。

        現行由 `<select>` 單選天然保證（不再需要 closeOtherReportVersions）；
        本測試守住「版本入口是單選控制項」這個前提。
        """
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            html, r'<select id="report-version-select"',
            "版本入口應為單選 select——換成多開的控制項就會回到「報表混在一起」")
        for gone in ("toggleReportVersion", "closeOtherReportVersions"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, html, "逐列展開機制已退場，不應復活")

    def test_lazy_load_still_avoids_refetch(self):
        """⚠ 已載過的版本不重抓（`reportVersions.loaded` 機制保留）。"""
        body = _fn("loadReportVersionContent")
        self.assertIn("reportVersions.loaded", body,
                      "lazy 載入機制被移除，每次切換都會重打 API")


if __name__ == "__main__":
    unittest.main()
