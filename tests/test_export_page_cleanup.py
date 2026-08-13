"""「匯出報告」頁清空 PPT 線殘骸的契約（add-deck-delivery-line tasks 0）。

## 為什麼

PPT 交付線於 2026-08-10 整條移除後，這一頁只剩殘骸：
- 「產生 PPT」鈕打的 `ai:report_plan` 早已從 `AI_JOB_TYPES` 移除——**按下去必定 422**
- `exportPreview` 仍帶 PPT 狀態（`pptFiles`／`pptViewer`／`editMode`／`edits`）
- 編輯模式 08-10 已移除，localStorage 編輯稿機制成孤兒
- 版本下拉與整份預覽與「報表種類」頁重複
- `static/vendor/pptx-renderer/` 仍有 1.5 MB 的 pptx 瀏覽器渲染器

2026-08-12 使用者定案：報表種類頁＝報表工作介面、**匯出報告頁＝交付物中心**，
deck 要進駐這一頁。故先清空、保留頁面與導覽項，再由 deck 填入。

## ⚠ 三個共用函式不得誤刪

`readOnlyReportView`／`renderReportContentHtml` 被 `loadInlineReport`（報表種類頁）
呼叫，`refreshExportContent` 被 `maybeRefreshReportNarratives`（SSE 自動刷新）呼叫。
刪它們會弄壞剛驗收完的報表頁——本檔同時守這條界線。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"
VENDOR_DIR = PROJECT_ROOT / "backend" / "app" / "static" / "vendor" / "pptx-renderer"


def _code_only(html: str) -> str:
    """剔除註解，只留可執行的程式碼與標記。

    ⚠ 本檔驗的是「**沒有活的引用**」，不是「這個名字不准出現在檔案裡」。
    本庫的慣例是移除時留註解交代拿掉了什麼、為什麼——那些註解必然會提到被
    移除的函式名。若直接對全文做子字串比對，這些說明反而會讓測試永遠紅，
    逼人把說明刪掉，等於用測試消滅可維護性。

    做法只處理兩種形式：`/* … */` 區塊、整行 `//` 開頭。行尾註解不處理
    （保守：多留＝比較容易紅，不會造成假綠）。
    """
    without_blocks = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    return "\n".join(line for line in without_blocks.splitlines()
                     if not line.lstrip().startswith("//"))


class PptRemnantsRemovedTests(unittest.TestCase):
    """PPT 線殘骸必須清乾淨——留著的每一項都是「按了會壞」或「永遠不會動」。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")
        cls.code = _code_only(cls.html)

    def _absent(self, name: str) -> None:
        """⚠ 不用 assertNotIn：失敗時它會把整份 index.html 印進 traceback。"""
        self.assertFalse(name in self.code, f"殘骸仍在：{name}")

    def test_dead_ppt_entrypoints_are_gone(self):
        """入口與其下游：打的 job type 已不存在，按下去必定 422。"""
        for name in (
            "btn-export-ppt", "requestExportPpt", "ai:report_plan",
            "ppt-goal-input", "ppt-chart-picker",
            "collectPptPlanBrief", "exportPptApprovalOverrides",
            "runNarrativeThenExportPpt", "then_export_ppt",
        ):
            with self.subTest(name=name):
                self._absent(name)

    def test_pptx_browser_renderer_is_gone(self):
        """1.5 MB 的 pptx 瀏覽器渲染器：沒有 PPT 可預覽了。"""
        for name in ("PPTX_RENDERER_ASSET", "pptxRendererModule", "ensurePptxRenderer",
                     "renderRealPptPreview", "pptx-renderer"):
            with self.subTest(name=name):
                self._absent(name)
        self.assertFalse(VENDOR_DIR.exists(), f"vendor 資產仍在：{VENDOR_DIR}")

    def test_ppt_state_removed_from_export_preview(self):
        """`exportPreview` 狀態物件不得再帶 PPT 欄位——孤兒狀態會誤導後續維護。

        ⚠ 界線：這裡驗的是**狀態物件的欄位**，不是全域禁字。
        `readOnlyReportView()` 回傳的 view 仍帶 `editMode: false`——那是
        `renderReportContentHtml` 的**參數契約**（報表種類頁共用），不是殘骸。
        """
        m = re.search(r"const exportPreview = \{.*?\n\};", self.code, re.S)
        self.assertIsNotNone(m, "找不到 exportPreview 狀態物件")
        state = m.group(0)
        for field in ("pptFiles", "selectedPptFile", "pptViewer", "editMode", "edits"):
            with self.subTest(field=field):
                self.assertNotIn(field, state)

    def test_manual_edit_draft_mechanism_removed(self):
        """localStorage 人工編輯稿：編輯模式 2026-08-10 已移除，機制成孤兒。

        ⚠ `exportEditsDefault()` **保留**——`readOnlyReportView()` 用它提供
        空的 edits 結構給共用渲染函式。刪它會弄壞報表種類頁的內嵌顯示。
        （first pass 誤把它列入刪除清單，查呼叫圖後修正。）
        """
        for name in ("EXPORT_EDIT_KEY_PREFIX", "loadExportEdits", "saveExportEdits",
                     "saveCoverEdit", "saveNarrativeEdit", "resetNarrativeEdit",
                     "addExportNote", "removeExportNote", "updateExportNote"):
            with self.subTest(name=name):
                self._absent(name)
        self.assertIn("exportEditsDefault", self.html,
                      "readOnlyReportView 需要它提供空 edits 結構")

    def test_duplicated_version_and_preview_removed(self):
        """版本下拉與整份預覽在「報表種類」頁已有更好的版本，不留第二個入口。"""
        for name in ("export-version-select", "loadExportVersionOptions",
                     "export-preview", "loadExportPreview", "renderExportPreview",
                     "triggerExport"):
            with self.subTest(name=name):
                self._absent(name)


class SharedRenderersPreservedTests(unittest.TestCase):
    """⚠ 這三個是共用的，刪了會弄壞報表種類頁與 SSE 刷新。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_inline_report_renderers_survive(self):
        for name in ("readOnlyReportView", "renderReportContentHtml", "loadInlineReport"):
            with self.subTest(name=name):
                self.assertIn(name, self.html)

    def test_sse_narrative_refresh_survives(self):
        self.assertIn("refreshExportContent", self.html)
        self.assertIn("maybeRefreshReportNarratives", self.html)

    def test_report_catalog_page_intact(self):
        """報表種類頁的核心（檢視選單、版本下拉、匯出 HTML）不得被波及。"""
        for name in ("report-view-select", "report-version-select",
                     "exportSelectedReportHtml", "exportReportHtmlFile"):
            with self.subTest(name=name):
                self.assertIn(name, self.html)


class ExportPageReservedForDeckTests(unittest.TestCase):
    """頁面與導覽項保留給 deck 進駐——不是整頁移除。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_nav_item_and_page_remain(self):
        self.assertIn('data-nav="export"', self.html, "導覽項要留著")
        self.assertRegex(self.html, r"function renderExport\(\)", "頁面渲染函式要留著")

    def test_page_states_its_purpose(self):
        """清空期間要說明這頁在等什麼，不能只留一片空白。"""
        m = re.search(r"function renderExport\(\)\s*\{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 renderExport")
        body = m.group(0)
        self.assertRegex(body, r"簡報|deck", "頁面應說明它是簡報／deck 的交付物中心")


if __name__ == "__main__":
    unittest.main()
