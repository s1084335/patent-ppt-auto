"""deck 的漂移護欄——防「上一世代的錯」在新程式碼重演。

本檔不測功能，只測**會靜默分岔的地方**。每一條都對應一個這個專案實際踩過的
失敗模式（見 `references/pitfalls.md` 與 `.agents/context/decisions.md`）：

| 失敗模式 | 這裡怎麼防 |
|---|---|
| 同一份知識多個落點，改一處不同步 | 把兩處算出來的值直接比對 |
| 擬合出來的常數換前提後沒重量 | 把「前提」也寫成斷言，前提變了就紅 |
| 暫定值忘了回頭定案 | 標記為暫定的東西必須留下可搜尋的記號 |

⚠ 這些錯的共同點是**不會報錯**。沒有這些斷言，症狀要到實物目視才看得到，
而目視又剛好是最容易抽樣、最容易漏的一關。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"
for path in (PROJECT_ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class LineHeightHasTwoImplementationsTests(unittest.TestCase):
    """🔴 行高公式目前有**兩份實作**，必須逐值相等。

    `deck_layout.lines_height_pt` 是唯一定義處；`svg_canvas._block_height`
    因為 canvas 收到的是兩個常數（而非整個模組），重算了一次相同公式。
    ⚠ 這就是「同一份知識兩個落點」——當下一致不代表以後一致，
    所以把一致性變成閘門，而不是靠註解提醒。
    """

    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")
        from svg_canvas import SvgCanvas

        cls.canvas = SvgCanvas(cls.dl.SW, cls.dl.SH, font=cls.dl.FONT,
                               ls_first=cls.dl.LS_FIRST, ls_next=cls.dl.LS_NEXT,
                               unit_width=cls.dl.units, wrap_lines=cls.dl.wrap_lines)

    def test_two_implementations_agree(self):
        for size_pt in (11, 16, 20, 24):
            for lines in range(0, 8):
                with self.subTest(size=size_pt, lines=lines):
                    self.assertAlmostEqual(
                        self.dl.lines_height_pt(lines, size_pt),
                        self.canvas._block_height(lines, size_pt), places=9,
                        msg="兩份行高公式分岔了——估高與實際繪製會分家")


class FittedConstantsDeclareTheirPremiseTests(unittest.TestCase):
    """擬合出來的常數，必須把「量它時的前提」寫進原始碼。

    ⚠ `BASELINE_RATIO = 0.65` 是掃描擬合值，前提是**特定字型**（ascent 比例
    隨字型而變）。前提沒寫下來的話，換字型的人不會知道要重量——那是靜默失效。
    """

    def test_baseline_ratio_records_font_dependency(self):
        source = (SCRIPTS / "svg_canvas.py").read_text(encoding="utf-8")
        head = source.split("BASELINE_RATIO", 1)[0]
        self.assertIn("換字型要重量", head,
                      "BASELINE_RATIO 未載明它綁字型——換字型時不會有人知道要重掃")
        self.assertIn("極小值", head, "未載明它是掃描結果而非推導值")

    def test_line_height_records_measurement_method(self):
        source = (SCRIPTS / "deck_layout.py").read_text(encoding="utf-8")
        head = source.split("LS_FIRST", 1)[0]
        self.assertIn("BoundHeight", head, "未載明量測方法（COM），無法複現")


class ProvisionalValuesAreFindableTests(unittest.TestCase):
    """暫定值必須留下可搜尋的記號，否則沒人記得回頭定案。

    ⚠ `VISUAL_SCALE` 現為暫定 2.0，規格說待 tasks 2.3 實測定案。若它只是個
    普通常數，做完 2.3 的人不會知道要回來改——而它錯了也不會報錯，
    只會讓目視解析度不足而抓不到行首標點。
    """

    def test_visual_scale_marked_provisional(self):
        source = (SCRIPTS / "deck_layout.py").read_text(encoding="utf-8")
        head = source.split("VISUAL_SCALE", 1)[0]
        self.assertIn("待 tasks 2.3", head, "暫定值未標明何時定案")
        self.assertIn("暫定", head)


class NoSecondSourceForKnownConstantsTests(unittest.TestCase):
    """已收斂的知識不得再冒出第二個落點。"""

    CASES = [
        # (說明, 唯一定義處, 不得自行定義的檔案們, 判別字串)
        ("字型", "backend/app/reports/chart_sizing.py",
         ["skills/html-report-to-deck/scripts/deck_layout.py",
          "skills/html-report-to-deck/scripts/rebuild_chip_chart.py"],
         "Microsoft JhengHei"),
        ("Playwright 路徑", "skills/html-report-to-deck/scripts/browser_env.py",
         ["skills/html-report-to-deck/scripts/fit_render_charts.py",
          "skills/html-report-to-deck/scripts/shoot_pages.py"],
         "PLAYWRIGHT_HOME"),
        ("截圖尺寸", "skills/html-report-to-deck/scripts/deck_layout.py",
         ["skills/html-report-to-deck/scripts/shoot_pages.py"],
         "2560"),
    ]

    def test_consumers_do_not_redefine(self):
        for label, source_file, consumers, needle in self.CASES:
            self.assertTrue((PROJECT_ROOT / source_file).is_file(),
                            f"{label} 的唯一定義處不存在：{source_file}")
            for consumer in consumers:
                code = "\n".join(
                    line for line
                    in (PROJECT_ROOT / consumer).read_text(encoding="utf-8").splitlines()
                    if not line.lstrip().startswith("#"))
                with self.subTest(knowledge=label, file=Path(consumer).name):
                    self.assertNotIn(needle, code,
                                     f"{Path(consumer).name} 自行定義了{label}")


if __name__ == "__main__":
    unittest.main()
