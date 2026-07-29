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


class VersionExpandDrivesContentTests(unittest.TestCase):
    """展開／收合版本列要連動報表本體。"""

    def test_expanding_old_version_switches_content(self):
        """🔴 展開舊版時要把該版內容餵給檢視區。

        原本 `if (reportViewContent === null)` 只在首次載入時餵，
        展開舊版不搶——使用者看到容器展開了卻沒有報表。
        """
        body = _fn("loadReportVersionContent")
        self.assertNotIn(
            "if (reportViewContent === null) fillReportViewSelect(content)", body,
            "仍只在首次載入餵選單，展開舊版看不到該版報表")
        self.assertIn("fillReportViewSelect(content)", body,
                      "未把展開版本的內容餵給檢視區")

    def test_collapse_clears_content(self):
        """收合時報表本體要跟著收起，不留在畫面上。"""
        body = _fn("toggleReportVersion")
        self.assertRegex(
            body, r"clearReportInlineView|report-inline-view",
            "收合未連動報表本體，畫面仍留著已收合版本的內容")

    def test_only_one_version_open(self):
        """⚠ 同時只能展開一版。

        兩版同時展開＝兩份報表混在畫面上，正是使用者先前說的
        「不同報表混在一起」（R9 改成選單制的動因）。
        """
        body = _fn("toggleReportVersion")
        self.assertRegex(
            body, r"報表版本|report-version-item|closeOtherReportVersions",
            "未限制同時只開一版")

    def test_lazy_load_still_avoids_refetch(self):
        """⚠ 已載過的版本不重抓（`reportVersions.loaded` 機制保留）。"""
        body = _fn("loadReportVersionContent")
        self.assertIn("reportVersions.loaded", body,
                      "lazy 載入機制被移除，每次展開都會重打 API")


if __name__ == "__main__":
    unittest.main()
