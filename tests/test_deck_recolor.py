"""SVG 進 deck 時整批換色（tasks §6.2b–§6.2f）。

## 為什麼

使用者裁決（§6.2）：兩套深藍**都留，但不得同頁**。分界從「哪個模組」改成
「哪個媒介」——HTML 報表用 `#00094A`、PPTX 簡報用 `#0B2545`。
同一份 SVG 進 deck 時整批換色，於是任一頁上只會出現一種深藍。

## 三道閘門，每道都有它擋不住的事（寫在對應測試上）

- **§6.2c 驗產物不驗原始碼**：數對照表左欄在產物裡的出現次數。
  ⚠ 斷言「原始碼有呼叫換色函式」是代理指標——本專案踩過：
  函式在、字串在、資料到不了，照樣綠。
- **§6.2d 缺席要現形**：列出「兩張色票都沒有」的色。只擋已知左欄的話，
  新冒出來的第三種藍不會被發現。
- **§6.2f 恆等式的另一半**：換色前後**圖檔數與文字節點數不變**。
  ⚠ 沒有這條，「把圖整個拿掉」也能滿足 §6.2c——那正是 v5／v7／v9
  形式鎖的死法（為過鎖而刪內容，且刪掉的不留痕跡）。
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "html-report-to-deck" / "scripts"

REPORT_NAVY = "#00094A"
DECK_NAVY = "#0B2545"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _svg(*extra: str) -> str:
    """一張最小但**結構真實**的 SVG：帶標題、格線、資料色與頁尾。"""
    body = "".join(extra)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200">'
        f'<text data-role="chart-title" x="10" y="20" fill="{REPORT_NAVY}">標題</text>'
        '<line x1="0" y1="180" x2="400" y2="180" stroke="#DCE3F2"/>'
        '<rect x="10" y="40" width="60" height="120" fill="#006DF5"/>'
        f'<text x="10" y="195" fill="#9CA3AF">頁尾</text>{body}</svg>')


def _text_nodes(svg: str) -> int:
    root = ET.fromstring(svg)
    return sum(1 for el in root.iter() if el.tag.split("}")[-1] == "text")


class RecolorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("recolor_for_deck")

    def _run(self, files: dict[str, str]):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        for name, text in files.items():
            (d / name).write_text(text, encoding="utf-8")
        result = self.mod.recolor_dir(d)
        return d, result

    def test_report_navy_becomes_deck_navy(self):
        d, _ = self._run({"a.svg": _svg()})
        out = (d / "a.svg").read_text(encoding="utf-8")
        self.assertNotIn(REPORT_NAVY, out, "報表深藍沒被換掉")
        self.assertIn(DECK_NAVY, out, "沒換成 deck 深藍")

    def test_other_colours_untouched(self):
        """⚠ 只換對照表上的色。多換等於把別的設計一起改掉，而且不會有人發現。"""
        d, _ = self._run({"a.svg": _svg()})
        out = (d / "a.svg").read_text(encoding="utf-8")
        for keep in ("#DCE3F2", "#006DF5", "#9CA3AF"):
            with self.subTest(color=keep):
                self.assertIn(keep, out, f"{keep} 不在對照表上，不該被動")

    def test_case_insensitive_source(self):
        """⚠ SVG 裡可能是小寫。漏掉小寫＝換了一半，而閘門只數大寫會顯示已完成。"""
        d, _ = self._run({"a.svg": _svg().replace(REPORT_NAVY, REPORT_NAVY.lower())})
        out = (d / "a.svg").read_text(encoding="utf-8")
        self.assertNotIn(REPORT_NAVY.lower(), out)
        self.assertIn(DECK_NAVY, out)

    def test_idempotent(self):
        """跑兩次結果相同——runner 重試時會重跑，不得愈跑愈歪。"""
        d, _ = self._run({"a.svg": _svg()})
        first = (d / "a.svg").read_text(encoding="utf-8")
        self.mod.recolor_dir(d)
        self.assertEqual((d / "a.svg").read_text(encoding="utf-8"), first)

    def test_preserves_file_and_text_node_counts(self):
        """🔴 §6.2f：恆等式的另一半。

        沒有這條，「把圖整個拿掉」也能滿足「產物裡沒有報表色」——
        那正是 v5／v7／v9 形式鎖的死法。
        """
        before = {"a.svg": _svg(), "b.svg": _svg()}
        d, result = self._run(before)
        self.assertEqual(len(list(d.glob("*.svg"))), 2, "圖檔數變了")
        for name, text in before.items():
            with self.subTest(file=name):
                self.assertEqual(
                    _text_nodes((d / name).read_text(encoding="utf-8")),
                    _text_nodes(text),
                    f"{name} 的文字節點數變了——換色不該增刪內容")
        self.assertEqual(result["files"], 2)
        self.assertEqual(result["text_nodes_before"], result["text_nodes_after"])


class GateTests(unittest.TestCase):
    """§6.2c／§6.2d：驗產物。"""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load("recolor_for_deck")

    def _dir(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        for name, text in files.items():
            (d / name).write_text(text, encoding="utf-8")
        return d

    def test_gate_red_when_report_colour_remains(self):
        d = self._dir({"a.svg": _svg()})          # 故意不換色
        bad = self.mod.check_dir(d)
        self.assertTrue(bad, "產物還有報表色，閘門卻沒紅")
        self.assertTrue(any(REPORT_NAVY in b for b in bad), bad)

    def test_gate_green_after_recolor(self):
        d = self._dir({"a.svg": _svg()})
        self.mod.recolor_dir(d)
        self.assertEqual(self.mod.check_dir(d), [], "換完了卻還是紅")

    def test_unknown_colour_is_surfaced(self):
        """🔴 §6.2d：兩張色票都沒有的色要被列出來。

        ⚠ 只擋已知左欄的話，新冒出來的第三種藍不會被發現——缺席型偏差。
        """
        d = self._dir({"a.svg": _svg('<rect fill="#123456"/>')})
        self.mod.recolor_dir(d)
        unknown = self.mod.unknown_colours(d)
        self.assertIn("#123456", unknown, f"未知色沒被列出：{unknown}")

    def test_unknown_list_excludes_known_palette(self):
        """⚠ 把色票裡的色也報成未知＝訊號被雜訊淹掉，等於沒有這個功能。"""
        d = self._dir({"a.svg": _svg()})
        self.mod.recolor_dir(d)
        unknown = self.mod.unknown_colours(d)
        for known in (DECK_NAVY, "#DCE3F2", "#006DF5"):
            with self.subTest(color=known):
                self.assertNotIn(known, unknown)


if __name__ == "__main__":
    unittest.main()
