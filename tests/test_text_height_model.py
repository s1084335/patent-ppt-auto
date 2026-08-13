"""文字估高的模型形狀（add-deck-delivery-line tasks 2.2b-1）。

## 為什麼要改模型，不只是調數值

`deck_layout` 長年用「行數 × 字級 × 固定倍率（LS_RENDER=1.40）」估高。
2026-08-13 換 Noto Sans TC 重量時，用 PowerPoint COM 的 `TextRange.BoundHeight`
量到倍率**隨行數上升**：

| 字級 | 行數 | 實測高(pt) | 倍率 |
|---|---|---|---|
| 16 | 1 | 21.39 | 1.337 |
| 16 | 2 | 44.82 | 1.401 |
| 16 | 3 | 68.24 | 1.422 |
| 24 | 1 | 32.08 | 1.337 |
| 24 | 3 | 102.35 | 1.422 |
| 24 | 4 | 137.49 | 1.432 |

拆開就清楚了：**首行 1.337、後續每行 1.464**（16pt 與 24pt 完全一致，
誤差 <0.1%）。固定倍率模型在 2 行剛好吻合、**3 行以上一路低估**。

⚠ `deck_layout.py` 檔頭早就警告「段數一多就會把最後一行切掉，而且裕度表不會叫
——因為它用的正是同一個低估的估算器」。當時歸因為「數值不夠大」，
**真正的原因是模型形狀不對**：一個固定倍率無法同時吻合各種行數。

## 這裡守的是形狀，不是數字

`test_ratio_grows_with_line_count` 直接鎖住「多行的等效倍率必須大於單行」——
任何人把它改回單一倍率都會紅。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# COM 實測值（2026-08-13，Noto Sans TC，PowerPoint TextRange.BoundHeight）。
# (字級pt, 行數, 實測高pt)
MEASURED = [
    (16, 1, 21.39),
    (16, 2, 44.82),
    (16, 3, 68.24),
    (24, 1, 32.08),
    (24, 3, 102.35),
    (24, 4, 137.49),
]


def _load():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("deck_layout", SCRIPTS / "deck_layout.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["deck_layout"] = module
    spec.loader.exec_module(module)
    return module


class LineHeightModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_matches_com_measurements(self):
        """估高要對得上 COM 實測值（容忍 0.8pt，約 4% 的抗鋸齒與捨入）。"""
        for size_pt, lines, measured in MEASURED:
            with self.subTest(size=size_pt, lines=lines):
                got = self.mod.lines_height_pt(lines, size_pt)
                self.assertAlmostEqual(got, measured, delta=0.8,
                                       msg=f"{lines} 行 {size_pt}pt：估 {got:.2f}"
                                           f"／實測 {measured}")

    def test_never_underestimates(self):
        """🔴 估高**寧可高估不可低估**——低估會把最後一行切掉，而裕度表不會叫。"""
        for size_pt, lines, measured in MEASURED:
            with self.subTest(size=size_pt, lines=lines):
                self.assertGreaterEqual(self.mod.lines_height_pt(lines, size_pt),
                                        measured - 0.05,
                                        "低估了——這正是最後一行被切掉的成因")

    def test_ratio_grows_with_line_count(self):
        """🔴 鎖住**模型形狀**：等效倍率必須隨行數上升。

        ⚠ 這條是防「有人覺得兩段式麻煩，改回單一倍率」——那會讓 3 行以上
        重新開始低估，而且症狀（最後一行被切）要到實物目視才看得到。
        """
        ratios = [self.mod.lines_height_pt(n, 16) / (n * 16) for n in (1, 2, 3, 4)]
        for earlier, later in zip(ratios, ratios[1:]):
            with self.subTest(earlier=round(earlier, 4), later=round(later, 4)):
                self.assertGreater(later, earlier,
                                   "等效倍率沒有隨行數上升＝又變回固定倍率模型了")

    def test_first_and_next_are_separate_constants(self):
        """首行與後續行是兩個常數，且後續行較大。"""
        self.assertLess(self.mod.LS_FIRST, self.mod.LS_NEXT)
        self.assertAlmostEqual(self.mod.LS_FIRST, 1.337, delta=0.01)
        self.assertAlmostEqual(self.mod.LS_NEXT, 1.464, delta=0.01)

    def test_zero_lines_is_zero_height(self):
        self.assertEqual(self.mod.lines_height_pt(0, 16), 0)


class TextHeightUsesModelTests(unittest.TestCase):
    """`text_h()` 必須走同一個模型，不得自己乘倍率。"""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_text_h_delegates(self):
        source = (SCRIPTS / "deck_layout.py").read_text(encoding="utf-8")
        body = source.split("def text_h(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("lines_height_pt", body,
                      "text_h 應走 lines_height_pt，不得自己算倍率")
        self.assertNotIn("LS_RENDER", body, "不得殘留舊的固定倍率")

    def test_text_h_matches_measurement(self):
        """`text_h` 的結果（英吋）換回 pt 要對得上實測。"""
        for size_pt, lines, measured in MEASURED:
            # 造一段剛好切成 `lines` 行的文字
            width_in = 6.0
            per_line = self.mod._per_line(width_in, size_pt)
            text = "字" * int(per_line * lines - per_line / 2)
            actual_lines = self.mod.est_lines(text, width_in, size_pt)
            if actual_lines != lines:
                continue                      # 造不出剛好的行數就跳過該組
            got_pt = self.mod.text_h([(text, size_pt, 0)], width_in) * 72
            with self.subTest(size=size_pt, lines=lines):
                self.assertAlmostEqual(got_pt, measured, delta=0.8)


if __name__ == "__main__":
    unittest.main()
