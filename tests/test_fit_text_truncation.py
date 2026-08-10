"""`_fit_text` 的截斷分支必須能真的執行（2026-08-10 實機 TypeError）。

## 實機失敗

    File "build_ppt.py", line 629, in _fit_text
        return text[: max(1, budget - 1)].rstrip("，、。；：") + "…", True
    TypeError: slice indices must be integers or None or have an __index__ method

`budget = per_line * lines`，而 `per_line` **刻意是浮點數**——`_text_capacity`
的註解寫著「向下取整等於白丟將近一個字的寬度：實機每行 25.83 em 被截成 25，
一條 25.3 em 的要點就被推成兩行、整條丟棄」。浮點是對的，但拿去當 slice index
必須先取整。

⚠ **為什麼到今天才爆**：`len(text) <= budget` 這條路徑不碰切片，而在此之前所有
文字都裝得下。直到 KP 頁尾註記變長（加了定位分類的推導規則），第一次走進截斷
分支才現形——組版整個 job 失敗，PPT 產不出來。

這是「happy path 全綠、例外路徑從未執行」的典型：`_fit_text` 被呼叫過無數次，
但截斷那一半是死的。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import pytest

pytest.importorskip("pptx")  # 組版腳本需要 python-pptx；未安裝時跳過（同既有 PPT 測試）

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt"


def _load_builder():
    """以檔案路徑載入可攜的 build_ppt.py（不進主專案 import 路徑，同既有做法）。"""
    spec = importlib.util.spec_from_file_location(
        "build_ppt_fittext", SKILL_DIR / "scripts" / "build_ppt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_ppt = _load_builder()


class FitTextTruncationTests(unittest.TestCase):
    """裝不下時要截斷成功，不是拋型別錯誤。"""

    def setUp(self):
        self.theme = build_ppt.Theme.load(SKILL_DIR / "theme.json")

    def test_long_text_is_truncated_not_crashed(self):
        """🔴 這正是實機爆掉的路徑：文字超出框就走切片。"""
        text = "註記文字" * 200
        fitted, truncated = build_ppt._fit_text(
            self.theme, text, width_in=3.0, height_in=0.4, size_pt=9.0)
        self.assertTrue(truncated, "這麼長的文字必須被判定為截斷")
        self.assertTrue(fitted.endswith("…"))
        self.assertLess(len(fitted), len(text))

    def test_short_text_untouched(self):
        """裝得下就原樣回傳（未截斷路徑不受影響）。"""
        fitted, truncated = build_ppt._fit_text(
            self.theme, "短註記", width_in=6.0, height_in=1.0, size_pt=12.0)
        self.assertEqual(fitted, "短註記")
        self.assertFalse(truncated)

    def test_truncated_result_fits_budget(self):
        """截斷後的長度不得超過容量——截了卻還是裝不下等於沒截。"""
        per_line, lines = build_ppt._text_capacity(
            self.theme, width_in=3.0, height_in=0.4, size_pt=9.0)
        fitted, _ = build_ppt._fit_text(
            self.theme, "長" * 500, width_in=3.0, height_in=0.4, size_pt=9.0)
        self.assertLessEqual(len(fitted), int(per_line * lines))

    def test_tiny_box_still_returns_something(self):
        """極小的框也要回傳可見文字，不得回空字串或負長度切片。"""
        fitted, truncated = build_ppt._fit_text(
            self.theme, "很長的註記文字" * 50, width_in=0.3, height_in=0.15, size_pt=8.0)
        self.assertTrue(truncated)
        self.assertTrue(fitted)


if __name__ == "__main__":
    unittest.main()
