"""逐頁截圖的契約（add-deck-delivery-line tasks 2.2 第三塊）。

## 為什麼要專門測「圖片有沒有載到」

2026-08-13 實測踩到：用 `set_content` 載入 SVG 時頁面沒有 base URL，
`<image>` 被跨來源規則擋掉而**破圖**——但同一份 pptx 用 COM 轉圖卻正常。
目視因此看到假警報；更糟的是日後圖真的錯了，分不出是「真的錯」還是
「又是載入問題」。

⚠ 這種失敗**目視才看得到**，任何字串比對或 SVG 結構檢查都抓不到——
SVG 本身完全合法，只是瀏覽器沒去載那個檔。故本檔用**已知顏色**驗像素。

## 解析度來自單一定義處

`deck_layout.VISUAL_SCALE`／`visual_shot_size()`。本檔一併鎖住「截圖尺寸
等於推導值」，避免有人在 runner 那頭自己指定大小（那就是第二個落點）。
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"

RED = (220, 20, 60)


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class VisualShotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")
        cls.shooter = _load("shoot_pages")

    def _shoot_page_with_image(self):
        """造一頁：左半是**純紅 PNG**、右半留白。截圖後檢查紅色真的在。"""
        from PIL import Image

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        Image.new("RGB", (400, 300), RED).save(work / "chart.png")
        # 圖用**相對路徑**——與 svg_canvas.picture() 的產出一致。
        (work / "page01.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="1280" height="720" viewBox="0 0 1280 720">'
            '<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>'
            '<image x="100" y="100" width="400" height="300" xlink:href="chart.png"/>'
            "</svg>", encoding="utf-8")
        out = work / "shots"
        written = self.shooter.shoot([work / "page01.svg"], out)
        return Image.open(written[0]).convert("RGB")

    def test_image_actually_loads(self):
        """🔴 核心：圖片區域必須是那張圖的顏色，不是破圖佔位符。"""
        image = self._shoot_page_with_image()
        scale = self.dl.VISUAL_SCALE
        # SVG 座標 (300, 250) 落在圖片正中央；乘倍率換到截圖座標。
        pixel = image.getpixel((int(300 * scale), int(250 * scale)))
        for channel, (got, want) in enumerate(zip(pixel, RED)):
            with self.subTest(channel=channel):
                self.assertAlmostEqual(got, want, delta=12,
                                       msg=f"圖片沒載到（取樣得 {pixel}，應為 {RED}）")

    def test_shot_size_comes_from_single_source(self):
        """截圖尺寸 ＝ `visual_shot_size()`，呼叫端不得自訂。"""
        image = self._shoot_page_with_image()
        self.assertEqual(image.size, self.dl.visual_shot_size())

    def test_scale_is_derived_not_hardcoded_elsewhere(self):
        """⚠ `shoot_pages` 不得自己寫死尺寸——那會變成第二個落點。"""
        source = (SCRIPTS / "shoot_pages.py").read_text(encoding="utf-8")
        self.assertIn("visual_shot_size", source)
        for hardcoded in ("1280", "2560", "1920", "720", "1440"):
            with self.subTest(hardcoded=hardcoded):
                self.assertNotIn(hardcoded, source,
                                 f"寫死了尺寸 {hardcoded}，應從 deck_layout 導出")

    def test_playwright_env_has_single_source(self):
        """🔴 Playwright 路徑解析只能有一處（tasks 2.2d）。

        ⚠ `fit_render_charts` 與 `shoot_pages` 原本各寫一份相同的三行
        （`PLAYWRIGHT_HOME` → `sys.path` → `PLAYWRIGHT_BROWSERS_PATH`）。
        改一處不會同步另一處，而症狀是「其中一支在產線找不到瀏覽器、另一支正常」，
        很難聯想到是同一件事。
        """
        single = SCRIPTS / "browser_env.py"
        self.assertTrue(single.is_file(), "缺少唯一定義處 browser_env.py")
        for name in ("fit_render_charts.py", "shoot_pages.py"):
            code = "\n".join(
                line for line in (SCRIPTS / name).read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#"))
            with self.subTest(script=name):
                self.assertIn("ensure_playwright", code, "應呼叫共用的環境準備")
                self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", code,
                                 "不得自己設 browsers 路徑")
                self.assertNotIn("PLAYWRIGHT_HOME", code, "不得自己解析安裝根目錄")

    def test_uses_goto_not_set_content(self):
        """🔴 載入方式鎖死：`set_content` 會讓 `<image>` 破圖（2026-08-13 實測）。"""
        source = (SCRIPTS / "shoot_pages.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("#"))
        self.assertIn("goto", code)
        self.assertNotIn("set_content(", code)


if __name__ == "__main__":
    unittest.main()
