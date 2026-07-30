"""移除 PPT 的 CSS 模擬版面（2026-07-30 使用者定案）。

## 問題：兩套版面並存

前端有一整套 `.ppt-slide` CSS 與對應 JS，**用 HTML/CSS 模擬 PPT 版面**
（`pptCoverSlideHtml`／`pptChartNarrativeSlideHtml`／`pptTableSlideHtml`…約 490 行）。

⚠ 但批次 2 已導入**真實 `.pptx` 渲染**（vendored `@aiden0z/pptx-renderer`）。
兩套並存＝同一份版面維護兩份實作，必然分岔——與 `theme.json` 註解
「座標分岔曾是本專案反覆出現的問題」同型，只是這次分岔在 CSS 而非座標。

⚠ **證據：模擬版已經落後**——`.ppt-watermark` 還在，但浮水印已於 2026-07-29
定案不印、批次 1 也已從 `build_ppt.py` 移除。模擬的是舊行為。

## 定案（使用者：「編輯模式的 CSS 模擬要移除，編輯模式入口可以留著，
規劃上是以現在的 ppt 架構去做編輯模式」）

| 項目 | 處置 |
|---|---|
| `.ppt-slide` 系列 CSS 與模擬渲染 JS | ❌ 移除 |
| 編輯模式入口 `#export-edit-toggle` | ✅ 保留 |
| `exportPreview.edits` 資料結構 | ✅ 保留 |
| 真實 `.pptx` 渲染（`PptxViewer`） | ✅ 保留 |
| 單頁 HTML 匯出（`exportCoverHtml` 等） | ✅ 保留——⚠ 那是另一條路，不是 PPT 模擬 |

⚠ 編輯模式**改建在真實 PPT 架構上**的方案（E1 表單式／E2 疊層式／E3 重產式）
待第二步規劃，見 `.agents/context/ppt-visual-rework-spec.md` 四之二節。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parents[1] / "backend" / "app" / "static" / "index.html"


class CssSimulationRemovedTests(unittest.TestCase):
    """🔴 模擬版面的 CSS 與 JS 都要移除。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_simulation_css_classes_gone(self):
        """`.ppt-slide` 系列 CSS 選擇器不得存在。

        ⚠ 只驗 CSS 選擇器（`.ppt-slide` 開頭的規則），不是驗字串完全消失——
        `ppt-preview-stack` 等容器類別可能仍被真實渲染沿用。
        """
        for selector in (".ppt-slide .ppt-box", ".ppt-slide .ppt-cover",
                         ".ppt-slide .ppt-watermark", ".ppt-slide .ppt-panel",
                         ".ppt-slide .ppt-slot-edit", ".ppt-slide .draggable-ppt-box"):
            with self.subTest(selector=selector):
                self.assertNotIn(selector, self.html,
                                 f"模擬版面 CSS `{selector}` 仍在")

    def test_simulation_render_functions_gone(self):
        """畫模擬投影片的 JS 函式不得存在。"""
        for func in ("pptCoverSlideHtml", "pptChartNarrativeSlideHtml",
                     "pptTableSlideHtml", "pptTableNarrativeSlideHtml",
                     "pptDirectionSlideHtml", "pptNarrativeOnlySlideHtml",
                     "pptClusterSplitSlideHtml", "renderPptPagePreviewHtml",
                     "pptSlideBodyHtml", "pptSlideStyle", "pptBoxStyle"):
            with self.subTest(func=func):
                self.assertNotIn(f"function {func}", self.html,
                                 f"模擬渲染函式 {func} 仍在")

    def test_drag_handlers_gone(self):
        """⚠ 拖曳定位已於 2026-07-29 取消，其 handler 一併移除。"""
        for func in ("attachPptDragHandlers", "savePptPositionOverride"):
            with self.subTest(func=func):
                self.assertNotIn(f"function {func}", self.html)

    def test_watermark_gone(self):
        """🔴 浮水印已定案不印，模擬版卻還留著——一併清掉。"""
        self.assertNotIn("ppt-watermark", self.html,
                         "浮水印 class 仍在（已定案不印）")


class PreservedEntriesTests(unittest.TestCase):
    """🔴 這些必須保留——移除範圍不得擴散。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_edit_mode_entry_kept(self):
        """編輯模式入口保留（使用者明示）。"""
        self.assertIn("export-edit-toggle", self.html, "編輯模式開關被移除")
        self.assertIn("function toggleExportEditMode", self.html,
                      "toggleExportEditMode 被移除")

    def test_edits_structure_kept(self):
        """`exportPreview.edits` 結構保留——第二步的編輯模式要用它。"""
        self.assertIn("exportPreview.edits", self.html)
        self.assertIn("function exportEditsDefault", self.html)

    def test_real_pptx_render_kept(self):
        """🔴 真實 .pptx 渲染是唯一預覽路徑，絕不可誤刪。"""
        for kw in ("PptxViewer", "ensurePptxRenderer", "renderRealPptPreview",
                   "loadExportPptFiles", "renderMissingPptState"):
            with self.subTest(kw=kw):
                self.assertIn(kw, self.html, f"真實渲染路徑 {kw} 被誤刪")

    def test_single_page_html_export_kept(self):
        """⚠ 單頁 HTML 匯出是另一條路，不是 PPT 模擬——不得連坐移除。

        ⚠ 2026-07-31 起清單不含 confirmExportOutput：使用者定案改分頁預覽後
        它「刻意」退場（見 test_export_html_preview_tab.py），不是誤刪。
        """
        for func in ("exportCoverHtml", "reviewExportOutput", "buildExportHtml"):
            with self.subTest(func=func):
                self.assertIn(func, self.html, f"單頁 HTML 匯出的 {func} 被誤刪")

    def test_approval_overrides_kept(self):
        """🔴 `exportPptApprovalOverrides` 必須保留——**真實 PPT 路徑在用**。

        ⚠ 它宣告在要移除的區塊內（原 4055 行），但 `requestExportPpt`（原 5272）
        送 `approval_overrides` 時會呼叫它。整段砍會讓「產生 PPT」壞掉。
        移除時要把它搬出來，不是連坐刪除。
        """
        self.assertIn("function exportPptApprovalOverrides", self.html,
                      "產 PPT 需要的 approval_overrides 組裝函式被誤刪")
        self.assertIn("exportPptApprovalOverrides()", self.html,
                      "requestExportPpt 對它的呼叫不見了")


class SlotEditRemovedTests(unittest.TestCase):
    """⚠ 文案槽的**就地編輯**隨模擬版面移除。

    `savePptSlotEdit`／`resetPptSlotEdit` 只被模擬投影片的 `onchange` 呼叫
    （實測區塊外零引用），是「在假投影片上打字」的實作。
    第二步的 E1 表單式編輯會另外做，不沿用這兩支。
    ⚠ 但它們寫入的 `exportPreview.edits.slots` **資料結構保留**——
    E1 會用同一個結構。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_inplace_slot_edit_gone(self):
        for func in ("savePptSlotEdit", "resetPptSlotEdit",
                     "pptSlotContentHtml", "pptSlotText"):
            with self.subTest(func=func):
                self.assertNotIn(f"function {func}", self.html,
                                 f"模擬版面的就地編輯 {func} 仍在")

    def test_slots_data_structure_kept(self):
        """⚠ 資料結構不刪——第二步的編輯模式要用同一個。"""
        self.assertIn("slots:", self.html, "edits.slots 結構被移除")


if __name__ == "__main__":
    unittest.main()
