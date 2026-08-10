"""規劃端的上限必須對得上組版端的實際容量（2026-08-10，同型錯誤第三次後補）。

## 為什麼需要這支

`planning_contracts` 訂「CLI 最多能寫幾條、每條幾字」，`build_ppt` 算「版面實際
放得下幾條、每條幾字」——**同一份知識的兩個落點**，跨 repo 邊界無法 import，
於是各自演進。三次實測失敗全是這個成因：

| 次 | 症狀 |
|---|---|
| job 278 | prompt 要求無圖頁「至少 6 個單元」，`MAX_POINTS_PER_SLIDE=5` 打回 |
| job 279 | prompt 給的範例格式本身 57 字，`MAX_POINT_CHARS=50` 打回 |
| （更早） | 上限比版面容量**還嚴**，等於白白浪費版面 |

⚠ 而且失敗方向是最糟的那種：**規則要求的形式，守門不允許**，使用者什麼都拿不到。

## 判準

上限不得**超過**最窄版型的容量（超過＝寫了會被靜默丟棄），也不該**遠低於**它
（遠低＝白白浪費版面，內容量寫不到範例水準）。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import pytest

pytest.importorskip("pptx")

from backend.app.reports.planning_contracts import (  # noqa: E402
    MAX_POINT_CHARS,
    MAX_POINTS_PER_SLIDE,
)

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt"


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_ppt_limits", SKILL_DIR / "scripts" / "build_ppt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LimitsMatchCapacityTests(unittest.TestCase):
    """兩端的數字要對得上。"""

    def setUp(self):
        self.m = _builder()
        self.theme = self.m.Theme.load(SKILL_DIR / "theme.json")

    def _capacity(self, width_in, height_in):
        per_line, lines = self.m._text_capacity(
            self.theme, width_in=width_in, height_in=height_in,
            size_pt=self.theme.size("body_pt"))
        return self.m.points_budget(per_line, lines, columns=1)

    def _narrowest_chars(self) -> int:
        """最窄版型的每條字數容量——上限不得超過它。"""
        panel = self.theme.geometry["points_panel"]
        side = self._capacity(panel["width_in"], panel["height_in"])["max_chars"]
        g = self.theme.geometry["direction"]
        full = (g["basis_left_in"] + g["basis_width_in"]) - g["body_left_in"]
        two_col = self._capacity((full - 0.4) / 2, g["body_height_in"])["max_chars"]
        return min(side, two_col)

    def test_char_limit_does_not_exceed_narrowest_layout(self):
        """字數上限超過版面容量 → 寫了會被靜默丟棄，比擋下來更糟。"""
        narrowest = self._narrowest_chars()
        self.assertLessEqual(
            MAX_POINT_CHARS, narrowest,
            f"MAX_POINT_CHARS={MAX_POINT_CHARS} 超過最窄版型容量 {narrowest}",
        )

    def test_char_limit_is_not_needlessly_strict(self):
        """上限遠低於容量 → 白白浪費版面，內容量寫不到範例水準。

        ⚠ 實測 job 279 即此：上限 50 比最窄版型的 82 還嚴，
        prompt 自己給的範例格式（57 字）當場被打回。
        """
        narrowest = self._narrowest_chars()
        self.assertGreaterEqual(
            MAX_POINT_CHARS, narrowest * 0.8,
            f"MAX_POINT_CHARS={MAX_POINT_CHARS} 遠低於容量 {narrowest}，版面沒被用滿",
        )

    def test_point_count_limit_fits_two_column_layout(self):
        """條數上限要放得進無圖頁的雙欄配置（單欄 3 條 → 雙欄 6 條）。"""
        g = self.theme.geometry["direction"]
        full = (g["basis_left_in"] + g["basis_width_in"]) - g["body_left_in"]
        per_col = self._capacity((full - 0.4) / 2, g["body_height_in"])["max_points"]
        self.assertLessEqual(
            MAX_POINTS_PER_SLIDE, per_col * 2,
            f"MAX_POINTS_PER_SLIDE={MAX_POINTS_PER_SLIDE} 超過雙欄容量 {per_col * 2}",
        )


if __name__ == "__main__":
    unittest.main()
