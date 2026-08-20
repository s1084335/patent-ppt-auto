"""圖內字級門檻要**擋對的、放行對的**（add-deck-delivery-line）。

## 沿革

**2026-08-14**：`deck_layout.build()` 一直有算圖內字級並與門檻比較
（`MIN_CHART_PT = 9.0` 單圖、`MIN_CHART_PT_MULTI = 12.0` 雙圖），低於門檻時
把該圖收進 `weak` 並印出建議動作——但 **`weak` 只被印出來，沒有計入回傳值**，
而 `make_deck.py` 是 `return 1 if bad else 0`。整條鏈是：文件寫著門檻 ✅、
`check_docs` 驗證文件與常數一致 ✅、`build` 算得出實際值 ✅、**執行時不擋** ❌。
於是把 `weak` 計入回傳值。

🔴 **2026-08-16 #400 首跑修正**：一律計入是錯的。字級不足分兩類——

| 類 | 例 | 處置 |
|---|---|---|
| **可修** | 雙圖頁（可拆頁）、判讀帶佔 >2 行擠掉圖 | 計入失敗，走修稿輪 |
| **結構所限** | 判讀帶已精簡、非 chip 型 | **只揭露不擋** |

⚠ 為什麼結構所限不能擋：那類 CLI 怎麼改內容都不會變好，逼它過閘門只能刪
判讀帶（**缺席型偏差**），而圖還是不會變大——正是 `deepen-deck-evidence-layer`
design §1.2 三問第 3 題要防的 v5 同型錯誤。SKILL.md 對這類的處置本來就是
「必須在完成回報中揭露此頁字級」，不是擋下。

實證：#400 的 P9／P11 判讀帶各只有 2 則、共 ~100 字，壓縮後字級只從
8.1→8.6pt——因為根因是 `fit` 放大不了那幾張圖（圖內仍是 BASE_FONT 15.1），
不是判讀帶佔位。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ChartFontFloorGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")
        cls.reg = _load("regression")

    def _build(self, font_px: float, *, long_band: bool = False) -> int:
        """用 regression 素材跑一次 `build`，圖內字級全給 `font_px`。

        `long_band=True` 時把判讀帶灌長（>2 行），模擬「可修」那一類。
        """
        from PIL import Image

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        png_dir = root / "png"
        png_dir.mkdir()
        for name, (w, h) in self.reg.SHAPES.items():
            Image.new("RGB", (w * 3, h * 3), (255, 255, 255)).save(png_dir / f"{name}.png")
        (png_dir / "font_choice.json").write_text(
            json.dumps({name: font_px for name in self.reg.SHAPES}), encoding="utf-8")
        content = self.reg._content()
        if long_band:
            for page in content["pages"]:
                if page.get("charts"):
                    page["lines"] = ["這是一則刻意灌長的判讀敘述，用來把判讀帶推過兩行"
                                     "的門檻，讓字級不足被歸類為可修那一類。" * 2] * 3
        return self.dl.build(content, png_dir, root / "out.pptx")

    def test_adequate_font_passes(self):
        """基準：字級充足時不因字級而失敗。

        ⚠ 沒有這條，下面幾條就分不清紅的是門檻生效還是素材本來就超版。
        """
        self.assertEqual(self._build(40.0), 0, "字級充足的素材不應有任何問題")

    def test_structural_shortfall_does_not_block(self):
        """🔴 判讀帶已精簡仍不足＝原圖結構所限 → **放行**（只揭露）。

        擋下去只會逼 CLI 刪判讀帶內容，而圖不會變大——缺席型偏差。

        ⚠ 期望值不是 0：regression 素材含一個**雙圖頁**（flat_a＋flat_b），
        那兩張按定義屬「可修」（拆成兩頁就解決），本來就該擋。單圖頁的
        tall／square 判讀帶短、屬結構所限，必須放行——所以回傳恰為 2。
        """
        blocked = self._build(3.0)
        self.assertEqual(
            blocked, 2,
            f"回傳 {blocked}：應只擋雙圖頁那 2 張（可拆頁解決）；"
            "單圖頁的結構所限被擋＝逼 CLI 刪內容，而圖不會變大")

    def test_fixable_shortfall_blocks(self):
        """判讀帶佔 >2 行擠掉圖＝可修 → **必須擋**，走修稿輪。"""
        self.assertGreater(
            self._build(3.0, long_band=True), 0,
            "判讀帶過長造成的字級不足應該擋下並要求濃縮")

    def test_floor_is_stricter_for_multi_chart_pages(self):
        """雙圖頁門檻更高（12pt vs 9pt）——擠成兩圖是自找的，且拆頁可解。"""
        self.assertGreater(self.dl.MIN_CHART_PT_MULTI, self.dl.MIN_CHART_PT)


if __name__ == "__main__":
    unittest.main()
