"""§6.4 硬判準：分析程式不得有散落的裸色值。

## 判準與它守不住的地方

**判準**：`chart_runner` 裡與繪圖有關的色值，必須來自色票唯一定義處
（`chart_sizing.PALETTE`／`SCALES`），不得直接寫 `#RRGGBB`。

⚠ **這是代理指標不是恆等式**，必須誠實講：「原始碼裡沒有裸 hex」可以用
**把常數搬家**滿足（移進設定檔／JSON／DB），閘門會綠而行為完全沒變，
而且可追溯性反而下降（從「有註解說明它的用途」變成「設定檔裡一個沒有來歷的值」）
——那正是 v5／v7／v9 形式鎖的死法（§9.9g 記錄過同型）。

真正的恆等式在**產物側**：`recolor_for_deck.unknown_colours()` 掃進 deck 的 SVG，
色票沒有的色一律列出來。搬到哪裡都躲不掉，因為它數的是**畫出來的東西**。
本檔是那道的補充，不是替代——兩道並存，缺一道另一道就會被當成「已經檢查過了」。

## 掃描邏輯與人工盤點同源

`scripts/audit_palette.py` 是唯一定義處（§6.7），本測試 import 它。
⚠ 另寫一份精簡版必然分岔，而分岔時測試那份會比較鬆。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _audit():
    """載入實查腳本。⚠ 它在 import 時就會跑完並印出結果，屬設計如此
    （它同時是人工盤點的 CLI）。本測試只用它的 `scan`。
    """
    spec = importlib.util.spec_from_file_location(
        "audit_palette", ROOT / "scripts" / "audit_palette.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audit_palette"] = mod
    spec.loader.exec_module(mod)
    return mod


class NoLooseHexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = _audit()

    def test_chart_runner_has_no_loose_hex(self):
        found = self.audit.scan([ROOT / "backend/app/reports/chart_runner.py"])
        loose = sorted((f[1], h) for h, v in found.items() for f in v if not f[2])
        self.assertEqual(
            loose, [],
            f"chart_runner 有 {len(loose)} 處散落裸色值：{loose[:8]}"
            "——色票不是唯一定義處，改一處不會連動其他處")

    def test_every_colour_has_a_named_home(self):
        """⚠ 「散落為 0」不等於「每個色都有名字」：全部塞進一個沒有語意的
        大 dict 也能讓散落歸零。這條補驗每個色都掛在具名常數底下。
        """
        found = self.audit.scan([ROOT / "backend/app/reports/chart_runner.py"])
        homeless = sorted(h for h, v in found.items() if not any(f[2] for f in v))
        self.assertEqual(homeless, [], f"這些色沒有任何具名落點：{homeless}")

    def test_scanner_ignores_docstrings(self):
        """⚠ 掃描器不得把說明文字算成用法。

        docstring 裡引用色值（「原本 9 個 `--paper: #F4F6F9;` 寫死在模板裡」）
        非常自然。不排除的話會逼人**改註解**讓數字歸零——為過閘門而改文件，
        本專案「註解破壞斷言」的第 8 次，這次方向是製造假陽性。
        """
        import ast

        src = 'def f():\n    """說明：#ABCDEF 這個色。"""\n    return 1\n'
        skip = self.audit.docstring_lines(ast.parse(src))
        self.assertIn(2, skip, "docstring 行沒被排除")


if __name__ == "__main__":
    unittest.main()
