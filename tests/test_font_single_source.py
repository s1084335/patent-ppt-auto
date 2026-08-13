"""字型的單一定義處（add-deck-delivery-line tasks 2.2b）。

## 為什麼這條特別重要

`fit_render_charts` 用 Chromium 的 `getBBox` 量 SVG 文字，據以決定圖內字級。
**字型宣告不一致 → 量測錯 → 字級跟著錯**，而且不會有任何東西報錯。

2026-08-13 實掃就抓到現成的漂移：`chart_runner.SVG_FONT_STYLE` 宣告正黑體，
但同檔四個 SVG 根元素宣告 `Segoe UI`（中文靠 fallback）——同一張圖兩種宣告。

## 唯一定義處＝`chart_sizing`（2026-08-13 使用者裁決選項 A）

判準是「改圖表字型時，簡報原生文字也要跟著改」——那就是同一份知識。
deck skill 走專案環境後 `import` 得到它（`backend/app/reports/__init__.py`
是空的，import 無副作用）。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 掃描範圍：會產出字型宣告的檔案。
TARGETS = [
    PROJECT_ROOT / "backend" / "app" / "reports" / "chart_runner.py",
    SCRIPTS / "deck_layout.py",
    SCRIPTS / "rebuild_chip_chart.py",
    SCRIPTS / "svg_canvas.py",
]


def _code_only(path: Path) -> str:
    """剔除註解——本庫慣例是留註解交代沿革，那些會提到舊字型名。"""
    text = path.read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


class SingleSourceTests(unittest.TestCase):
    def test_constants_exist(self):
        from backend.app.reports.chart_sizing import FONT_FAMILY, FONT_STACK

        self.assertEqual(FONT_FAMILY, "Noto Sans TC")
        self.assertTrue(FONT_STACK.startswith(f"'{FONT_FAMILY}'"),
                        "Noto Sans TC 必須是第一順位")

    def test_stack_is_derived_not_duplicated(self):
        """FONT_STACK 必須由 FONT_FAMILY 導出，不是另外打一次字。"""
        source = _code_only(PROJECT_ROOT / "backend" / "app" / "reports" / "chart_sizing.py")
        self.assertRegex(source, r"FONT_STACK\s*=.*FONT_FAMILY",
                         "FONT_STACK 應引用 FONT_FAMILY，不得重打字型名")
        self.assertEqual(source.count('"Noto Sans TC"') + source.count("'Noto Sans TC'"), 1,
                         "字型名在唯一定義處也只能出現一次")


class NoHardcodedFontTests(unittest.TestCase):
    """產字型宣告的地方一律引用常數，不得寫死。"""

    def test_no_literal_font_names_in_output_code(self):
        """⚠ 判準是「**產出**用的字面量」——fallback 鏈裡有正黑體是合理的
        （Noto 沒裝時退回），但那條鏈本身要來自 `FONT_STACK`，不是各處自己打。
        """
        for path in TARGETS:
            code = _code_only(path)
            with self.subTest(file=path.name):
                for literal in ("Microsoft JhengHei", "Segoe UI"):
                    self.assertNotIn(literal, code,
                                     f"{path.name} 寫死了 {literal}，應引用 chart_sizing")

    def test_every_svg_producing_function_declares_font(self):
        """🔴 每個產 SVG 的函式都要宣告字型——漏一個，那張圖就用瀏覽器預設字量測，
        而 `fit_render_charts` 據以算出的字級也就錯了，且不會報錯。

        ⚠ 合法的宣告有**兩種**：根元素帶 `font-family`，或函式內用
        `SVG_FONT_STYLE`（`<style>text{…}</style>`）。本測試不強制用哪一種，
        只要求「至少有一種」——第一版只認根元素屬性，結果把七個靠 style 的
        函式全判成違規（假警報）。
        """
        code = _code_only(PROJECT_ROOT / "backend" / "app" / "reports" / "chart_runner.py")
        # 以 def 切段，逐個函式檢查
        chunks = re.split(r"\ndef ", code)
        offenders = []
        for chunk in chunks:
            if "<svg" not in chunk:
                continue
            name = chunk.split("(", 1)[0].strip().splitlines()[0][:40]
            if "FONT_STACK" in chunk or "SVG_FONT_STYLE" in chunk:
                continue
            offenders.append(name)
        self.assertEqual(offenders, [],
                         f"這些函式產 SVG 卻沒宣告字型：{offenders}")


class SkillReadsFromSingleSourceTests(unittest.TestCase):
    """deck skill 的字型來自 `chart_sizing`，不自己定義。"""

    def test_deck_layout_font_comes_from_chart_sizing(self):
        code = _code_only(SCRIPTS / "deck_layout.py")
        self.assertRegex(code, r"from backend\.app\.reports\.chart_sizing import",
                         "deck_layout 應從唯一定義處取字型")
        self.assertNotRegex(code, r'^FONT\s*=\s*["\']', "不得自己寫死 FONT")

    def test_runtime_value_matches(self):
        """實際載入後兩邊的值要相同——只看原始碼會漏掉載入期的覆寫。"""
        import importlib.util

        from backend.app.reports.chart_sizing import FONT_FAMILY

        spec = importlib.util.spec_from_file_location("dl_font", SCRIPTS / "deck_layout.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["dl_font"] = module
        spec.loader.exec_module(module)
        self.assertEqual(module.FONT, FONT_FAMILY)


if __name__ == "__main__":
    unittest.main()
