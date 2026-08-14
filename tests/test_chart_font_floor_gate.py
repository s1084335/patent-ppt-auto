"""圖內字級門檻要**真的擋**（add-deck-delivery-line）。

## 為什麼要它

`deck_layout.build()` 一直有算圖內字級並與門檻比較（`MIN_CHART_PT = 9.0` 單圖、
`MIN_CHART_PT_MULTI = 12.0` 雙圖），低於門檻時把該圖收進 `weak` 並印出建議動作
——但 **`weak` 只被印出來，沒有計入回傳值**。而 `make_deck.py` 是
`return 1 if bad else 0`，`bad` 只含版面溢出。

於是整條鏈是：`SKILL.md` 寫著門檻 ✅、`check_docs` 驗證文件與常數一致 ✅、
`build` 算得出實際值 ✅、**執行時不擋** ❌。三層綠、最後一層漏，
而且沒有任何東西會報錯。

⚠ 2026-08-14 實測現有產出 11/14 達 14pt、最低 11.54pt，**門檻從未被觸發**，
所以這個洞至今沒有造成實害。修它是預防性的：資料或版面一變，
字級掉到門檻下時要有人知道。
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

    def _build_with_fonts(self, font_px: float) -> int:
        """用 regression 的素材跑一次 `build`，圖內字級全部給 `font_px`。

        ⚠ PNG 只需尺寸讀得到（`build` 用 `Image.open(...).size` 反推原圖寬），
        內容不影響字級計算。
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
        return self.dl.build(self.reg._content(), png_dir, root / "out.pptx")

    def test_adequate_font_passes(self):
        """基準：字級充足時不因字級而失敗。

        ⚠ 沒有這條，下一條就分不清紅的是「字級門檻生效」還是「素材本來就超版」。
        """
        self.assertEqual(self._build_with_fonts(40.0), 0,
                         "字級充足的素材不應有任何問題")

    def test_font_below_floor_makes_build_fail(self):
        """🔴 字級低於門檻 → `build` 必須回傳非零，而不是只印一行警告。"""
        self.assertGreater(self._build_with_fonts(3.0), 0,
                           "圖內字級遠低於門檻，build 卻回報無問題——門檻沒有牙齒")

    def test_floor_is_stricter_for_multi_chart_pages(self):
        """雙圖頁門檻更高（12pt vs 9pt）——擠成兩圖是自找的。"""
        self.assertGreater(self.dl.MIN_CHART_PT_MULTI, self.dl.MIN_CHART_PT)


if __name__ == "__main__":
    unittest.main()
