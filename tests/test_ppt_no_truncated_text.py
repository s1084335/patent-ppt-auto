"""批 2：判讀要點不得出現截斷（H-2，2026-08-03）。

🔴 使用者看到 p4 的「意涵｜受理局高度集中於中國，海…」後：
「這種卡掉的敘述不要再有」。

## 兩個不同根因（都要修）

**① p4（`percentage_bars`）框高算錯**
`_points_area` 用 `points_panel.height_in`＝5.0 算容量，但實際渲染時
**有判讀限制框**的頁用的是 `height_with_caveat_in`＝3.3（`build_ppt.py:1556`）。
雖然容量計算有扣掉警語佔的行數，但那補不上 1.7 in 的框差
→ 解讀 CLI 照 5.0 的容量寫 → 塞進 3.3 的框 → 被 `_trim_blocks` 截成「…」。

**② p2（`chart_hero`）單條行數配額不足**
那條只有 28 字，遠小於全域上限 55（所以契約驗證放行），
但超過「每條最多 `NARRATIVE_POINT_LINES`＝2 行」的配額而被截字。

## 定案

截字**一律不做**。放不下時整條不放並記進 warnings——
句子斷在半路讀者看不懂，比少一條更糟；而少一條至少「看到的都是完整的」，
且 warnings 會講出來，不是靜默。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt"
THEME_PATH = SKILL_DIR / "theme.json"


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_ppt_batch2", SKILL_DIR / "scripts" / "build_ppt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_ppt_batch2", module)
    spec.loader.exec_module(module)
    return module


bp = _load_builder()


def _theme():
    return bp.Theme.load(THEME_PATH)


class CaveatShrinksCapacityTests(unittest.TestCase):
    """① 有判讀限制框的頁，容量要按**縮小後**的框算。"""

    def test_points_area_honours_caveat_height(self):
        theme = _theme()
        without = bp._points_area(theme, "percentage_bars")
        with_caveat = bp._points_area(theme, "percentage_bars", caveat=True)
        self.assertIsNotNone(with_caveat)
        self.assertLess(with_caveat[1], without[1],
                        "有判讀限制框時要點區其實只有 3.3 in，容量卻按 5.0 算")

    def test_capacity_smaller_for_caveated_report(self):
        """實際容量表：有 caveat 的報表可寫條數必須少於同版型無 caveat 者。"""
        theme = _theme()
        capacity = bp.narrative_capacity(theme)
        caveated = [k for k in capacity if k in bp.CAVEATS]
        self.assertTrue(caveated, "測試前提不成立：沒有任何帶 caveat 的報表")
        for key in caveated:
            spec = next((s for s in bp.PAGE_LAYOUT if key in s.report_keys), None)
            if spec is None or spec.kind not in ("percentage_bars", "chart_with_points", "stat_callout"):
                continue
            area = bp._points_area(theme, spec.kind, caveat=True)
            plain = bp._points_area(theme, spec.kind)
            with self.subTest(report=key):
                self.assertLess(area[1], plain[1])


class NoEllipsisTests(unittest.TestCase):
    """② `_trim_blocks` 一律不得截字。"""

    def _blocks(self, count=6, chars=60):
        return [(f"現況", "字" * chars, "ink", False) for _ in range(count)]

    def test_never_appends_ellipsis(self):
        theme = _theme()
        out = bp._trim_blocks(theme, self._blocks(), width_in=3.4, height_in=2.0, size_pt=15)
        for _label, text, _color, _emph in out:
            with self.subTest(text=text[:20]):
                self.assertNotIn("…", text, "還在截字——使用者：「這種卡掉的敘述不要再有」")

    def test_kept_blocks_keep_original_text(self):
        """留下來的每一條都要是**原文**，不得被修剪過。"""
        theme = _theme()
        blocks = self._blocks()
        out = bp._trim_blocks(theme, blocks, width_in=3.4, height_in=2.0, size_pt=15)
        originals = {text for _l, text, _c, _e in blocks}
        for _label, text, _color, _emph in out:
            self.assertIn(text, originals)

    def test_dropped_blocks_are_reported(self):
        """丟掉的條目要留下紀錄——靜默截斷正是這輪被抓到的問題。"""
        theme = _theme()
        bp.reset_dropped_points()
        bp._trim_blocks(theme, self._blocks(count=8), width_in=3.4, height_in=1.2, size_pt=15)
        self.assertTrue(bp.dropped_points(), "放不下卻沒有任何紀錄＝靜默丟棄")

    def test_dropped_records_carry_page_number(self):
        """紀錄要說得出**哪一頁**——只說丟了哪條，看的人還得自己翻 22 頁去找。

        ⚠ 頁碼由分派處（`RENDERERS[spec.kind]` 前）設定，`_trim_blocks` 不加參數：
        它有 7 個呼叫端，全都只關心「要畫哪些條」。
        """
        theme = _theme()
        bp.reset_dropped_points()
        bp.set_current_page(12)
        bp._trim_blocks(theme, self._blocks(count=8), width_in=3.4, height_in=1.2, size_pt=15)
        pages = {page for page, _block in bp.dropped_points()}
        self.assertEqual(pages, {12}, "丟棄紀錄沒有帶頁碼")
        bp.set_current_page(None)

    def test_caveat_still_protected(self):
        """判讀限制那條照舊優先保留（警語講一半比不講更糟）。"""
        theme = _theme()
        blocks = self._blocks(count=5) + [(bp.CAVEAT_LABEL, "本分析僅供參考不構成侵權判斷", "muted", False)]
        out = bp._trim_blocks(theme, blocks, width_in=3.4, height_in=1.2, size_pt=15)
        self.assertTrue(any(label == bp.CAVEAT_LABEL for label, *_ in out),
                        "判讀限制被擠掉了")


class PerVariantCapacityTests(unittest.TestCase):
    """I-1：容量要**逐 variant** 算，不是逐 report_key（2026-08-03 第四輪實機）。

    🔴 實機 #166：`points_dropped` 從 2 條暴增到 **10 條**，分布為
    p5×1、p8×4、p10×3、p13×1、p17×1 —— p8（IPC L5）與 p10（CPC L5）
    **全是窄側欄頁**，L4 兩頁一條都沒丟。

    根因：同一個 report_key 的不同 variant 落在**不同版型**：

    | 版型 | 每行字數 | 行×欄 | 容量 |
    |---|---|---|---|
    | `chart_wide`（L4 扁圖，底部寬橫幅） | 27 | 8×2 | 8 條 × 54 字 |
    | `chart_hero`（L5，右側窄欄） | 13 | 15×1 | 7 條 × 26 字 |

    而 `narrative_capacity` 的迴圈是「只要**任一張**圖是扁的，整個 report_key
    就用 chart_wide 算」→ `ipc_main_distribution` 拿到 8×54，CLI 照這個寫
    （實測每條 35–45 字），L4 放得下、**L5 每條要 3 行**、總行數爆掉。

    ⚠ 不走「同一 key 取最小容量」：那會讓 L4 那種寬頁寫得比能放的少而變空，
    等於推翻 C-9（使用者：「要的是濃縮不是丟棄」）。
    """

    @staticmethod
    def _capacity_with_charts():
        """帶 charts 算容量——variant 是從**圖檔名後綴**推出來的，沒有圖就沒有 variant。

        ⚠ 實務上 runner 一定帶 run_dir（`load_narrative_capacity(run_dir)`），
        所以「帶 charts」才是真實情境；不帶 charts 只是前端縮圖預覽用的降級路徑。
        """
        import json

        cache = SKILL_DIR.parents[1] / "var" / "report_cache"
        runs = sorted(cache.glob("report_trial_*")) if cache.is_dir() else []
        if not runs:
            raise unittest.SkipTest("需要 var/report_cache 內的實際報表版本")
        run_dir = runs[-1]
        manifest_path = run_dir / "artifact_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        theme = _theme()
        charts = bp.ChartIndex(run_dir, run_dir / ".cache", manifest, theme)
        return bp.narrative_capacity(theme, charts)

    def test_variant_keys_present(self):
        cap = self._capacity_with_charts()
        self.assertIn("ipc_main_distribution:L4", cap,
                      "沒有逐 variant 的容量鍵——CLI 只能拿到一個混合值")
        self.assertIn("ipc_main_distribution:L5", cap)

    def test_narrow_variant_gets_smaller_capacity(self):
        """L5（窄側欄）的每條字數上限必須明顯小於 L4（寬橫幅）。"""
        cap = self._capacity_with_charts()
        wide = cap["ipc_main_distribution:L4"]
        narrow = cap["ipc_main_distribution:L5"]
        self.assertLess(narrow["max_chars"], wide["max_chars"],
                        "窄側欄的每條字數上限沒有比寬橫幅小——L5 頁會塞不下")

    def test_report_key_fallback_kept(self):
        """report_key 層要保留，供沒有 variant 的報表與舊資料使用。"""
        cap = self._capacity_with_charts()
        self.assertIn("ipc_main_distribution", cap)


if __name__ == "__main__":
    unittest.main()
