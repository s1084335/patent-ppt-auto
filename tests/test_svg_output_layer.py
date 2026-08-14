"""`deck_layout.build_svg` 輸出層的契約（add-deck-delivery-line tasks 2.2）。

## 這一層在做什麼

B 案把 `deck_layout` 的輸出端一分為二：現行的 python-pptx 路徑，與新的 SVG 路徑。
**頁型的幾何計算完全共用**（`_compose` 是唯一落點），只有最後畫出去那一步不同。

## 🔴 最重要的一條：兩端詞彙必須對得上

`svg_canvas` 產什麼、`svg_to_pptx` 收什麼，是同一份知識的兩個落點。
兩邊漂移不會有任何東西報錯——只會在某一頁突然 `UnsupportedElement`，
或更糟：某個元素被靜默畫成別的樣子。
`test_every_page_survives_the_converter` 直接把兩端串起來鎖住。
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class SvgOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")
        cls.reg = _load("regression")

    def _run(self):
        """用 regression 的合成 content ＋ 小尺寸假 PNG 跑一次輸出。

        ⚠ 合成而非真實報表：不把某批專利釘進測試（沿 regression.py 的原則）。
        這裡不跑 fit_render_charts（慢且需 Chromium），PNG 只要尺寸讀得到即可
        ——本檔驗的是輸出層，不是圖表擬合。
        """
        from PIL import Image

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        png_dir = root / "png"
        png_dir.mkdir()
        for name, (w, h) in self.reg.SHAPES.items():
            Image.new("RGB", (w * 3, h * 3), (255, 255, 255)).save(png_dir / f"{name}.png")
        out = root / "svg"
        pages = self.dl.build_svg(self.reg._content(), png_dir, out)
        return out, pages

    def test_one_svg_per_page(self):
        out, pages = self._run()
        self.assertEqual(len(pages), 8, "頁數應與現行 pptx 路徑一致")
        for path in pages:
            with self.subTest(page=path.name):
                self.assertTrue(path.is_file())

    def test_pages_are_wellformed_svg(self):
        _, pages = self._run()
        for path in pages:
            with self.subTest(page=path.name):
                root = ET.fromstring(path.read_text(encoding="utf-8"))
                self.assertTrue(root.tag.endswith("svg"))

    # ⚠ test_every_page_survives_the_converter 已隨窄轉換器封存移除
    #   （2026-08-14 使用者裁決）。詞彙鎖由 test_svg_vocabulary_guard 直接掃
    #   產出承接（svg_canvas.SVG_VOCABULARY 為唯一定義處）；復活指標見 tasks 2.1b。

    def test_images_use_relative_paths(self):
        """圖檔寫相對路徑——Chromium 用 `goto` 載入 SVG 時才抓得到同目錄的圖。

        ⚠ 2026-08-13 實測：`file://` 絕對 URI ＋ `set_content` 會破圖
        （跨來源被擋），而 COM 轉圖卻正常——目視因此看到假警報。
        """
        out, pages = self._run()
        found = False
        for path in pages:
            for el in ET.fromstring(path.read_text(encoding="utf-8")).iter():
                if not el.tag.endswith("image"):
                    continue
                href = (el.get("{http://www.w3.org/1999/xlink}href")
                        or el.get("href") or "")
                found = True
                with self.subTest(page=path.name, href=href):
                    self.assertFalse(href.startswith("file:"), "不得用 file:// 絕對 URI")
                    self.assertFalse(Path(href).is_absolute(), "不得用絕對路徑")
        self.assertTrue(found, "測試素材應該要有圖表頁")

    def test_png_copied_next_to_svg(self):
        """PNG 必須跟 SVG 同目錄，相對路徑才解得到。"""
        out, pages = self._run()
        for path in pages:
            for el in ET.fromstring(path.read_text(encoding="utf-8")).iter():
                if el.tag.endswith("image"):
                    href = (el.get("{http://www.w3.org/1999/xlink}href")
                            or el.get("href") or "")
                    with self.subTest(href=href):
                        self.assertTrue((out / href).is_file(),
                                        f"{href} 不在 SVG 同目錄")

    def test_text_is_one_element_per_line(self):
        """逐行定位：不得出現 `<tspan>`（詞彙外），每行自成 `<text>`。"""
        _, pages = self._run()
        for path in pages:
            content = path.read_text(encoding="utf-8")
            with self.subTest(page=path.name):
                self.assertNotIn("<tspan", content, "詞彙不含 tspan")
                self.assertIn("<text ", content, "頁面應該要有文字")


if __name__ == "__main__":
    unittest.main()
