"""來源行必須一行放得下（2026-08-19，實機 job #426 finding 4）。

## 病徵

目視回報：p04／p05／p07／p08／p09／p13／p15 **七頁**的頁尾來源行被截斷——
畫面上顯示「…／jurisdiction_」「…／ipc_main_」「…／opportunity_quadrant」，
chart key 的尾段掉到看不見的第二行。

## 成因（實測）

來源行的框是 `SW - 1.6 - (ML + 5.2)` ≈ **6.03in**、高 **0.26in**（16pt 剛好一行），
而 `textbox` 未給 size 時預設 `B_SIZE = 16pt`。長 chart key 換行後第二行落在
框外，看不見——**溢出的方式是「消失」而不是「擠出來」**，所以肉眼掃過去像正常。

## 判準

1. 現行報表的**每一個** chart key 都要一行放得下（不抽樣、逐 key 檢查）
2. 多圖頁的 key 串接後仍要放得下——放不下就**看得見地**省略（「等 N 項」），
   ⚠ 不得靜默截斷：看不見的截斷等於謊報來源
3. 幾何與字級由 `deck_layout` 的常數提供，測試與渲染共用同一組數字
   （各寫一份的話，改了框寬測試還是綠的）
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
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


class SourceLineFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")
        from backend.app.reports.report_definitions import DEFAULT_REPORT_NAMES
        cls.keys = list(DEFAULT_REPORT_NAMES)

    def _fits(self, text: str) -> bool:
        """用渲染端同一組常數與同一支估行函式判斷是否一行放得下。"""
        return self.dl.est_lines(text, self.dl.SOURCE_W, self.dl.SOURCE_PT) == 1

    def test_geometry_is_exposed_as_constants(self):
        """⚠ 幾何必須是具名常數：寫死在 `base()` 裡的話，測試只能自己抄一份，
        而抄的那份不會跟著改——閘門就變成裝飾。"""
        for name in ("SOURCE_X", "SOURCE_W", "SOURCE_PT"):
            self.assertTrue(hasattr(self.dl, name), f"缺常數 {name}")
        self.assertGreater(self.dl.SOURCE_W, 0)

    def test_every_report_key_fits_on_one_line(self):
        """逐 key 檢查，不抽樣——#426 裡有七頁中招。"""
        version = "report_trial_20260819_143341"
        bad = []
        for key in self.keys:
            text = self.dl.src_line({"_source_version": version}, {"charts": [key]})
            if not self._fits(text):
                bad.append(key)
        self.assertEqual(bad, [], f"這些 chart key 的來源行放不下一行：{bad}")

    def test_multi_chart_page_is_elided_visibly(self):
        """多圖頁串接後放不下時，要看得見地省略。

        ⚠ 判準是「讀者知道有省略」：靜默截斷會讓人以為那就是完整來源。
        """
        version = "report_trial_20260819_143341"
        text = self.dl.src_line({"_source_version": version},
                                {"charts": self.keys})       # 13 個 key 一起
        self.assertTrue(self._fits(text), f"多圖頁來源行仍放不下：{text}")
        self.assertIn("等", text, f"省略了卻沒有可見標記：{text}")

    def test_elision_keeps_version_and_first_key(self):
        """省略時最有用的兩項要留著：版本（可回溯）與第一個 key（知道在看什麼）。"""
        version = "report_trial_20260819_143341"
        text = self.dl.src_line({"_source_version": version},
                                {"charts": self.keys})
        self.assertIn("20260819_143341", text)
        self.assertIn(self.keys[0], text)

    def test_single_short_key_is_not_elided(self):
        """反面：放得下就不得加「等 N 項」——多餘的省略標記是假資訊。"""
        text = self.dl.src_line({"_source_version": "report_trial_20260819_143341"},
                                {"charts": ["kp_quadrant"]})
        self.assertNotIn("等", text)

    def test_no_charts_still_prints_version(self):
        text = self.dl.src_line({"_source_version": "report_trial_20260819_143341"}, None)
        self.assertEqual(text, "資料來源：20260819_143341")
        self.assertTrue(self._fits(text))

    def test_without_version_returns_none(self):
        self.assertIsNone(self.dl.src_line({}, {"charts": ["kp_quadrant"]}))


if __name__ == "__main__":
    unittest.main()
