"""引擎自行斷行的契約（add-deck-delivery-line tasks 2.2 的核心）。

## 為什麼要引擎自己斷

B 案把排版決定權從 PowerPoint 收回。現行 `deck_layout` 只**估算**行數
（`est_lines`）來算高度，真正的斷點是 PowerPoint 開檔時決定的——估算與實際
有落差正是「裕度表全綠但實物溢出」的根源。B 案改為引擎切好每一行、絕對定位、
關 wrap，PowerPoint 零重排自由。

## 🔴 一致性是這裡最重要的事

切分函式與 `est_lines` 若各算各的，就是「同一份知識兩個落點」——估高用一套、
實際排版用另一套，不一致**不會報錯**，只會讓版面偶爾溢出或留白。
本檔用 `test_line_count_matches_est_lines` 直接把兩者鎖在一起。

## 避頭尾

中文排版不得讓標點落在行首。現行做法是給 PowerPoint 加 `eaLnBrk`／`hangingPunct`
屬性請它處理（`deck_layout.py:183`），B 案改由引擎自己保證——這也是 SKILL.md
目視清單裡最難看出來的一項（明訂「要放大到 2× 以上看」）從此不必靠目視。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"


def _load():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("deck_layout", SCRIPTS / "deck_layout.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["deck_layout"] = module
    spec.loader.exec_module(module)
    return module


SAMPLES = [
    "申請量自 2020 年起穩定成長，2023 年達到高峰共 14 件。",
    "主要玩家集中在拉繩滑雪模擬機構，五家合計 11 件、近期集中度 88.9%。",
    "先讀完那 4 件請求項再落筆，避開既有構型。",
    "CN 121754861、CN 121754862、CN 223248694 三案同族，合併後計 1 件。",
    "短句。",
    "IPC 主分類集中於 A63B（訓練與體育器械），佔全體 78%；其餘散在 A61H 與 G09B。",
]


class WrapBasicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_no_line_exceeds_capacity(self):
        """每行寬度不得超過該寬度的容量（`_per_line`）。"""
        for width in (3.0, 6.0, 10.0):
            capacity = self.mod._per_line(width)
            for text in SAMPLES:
                lines = self.mod.wrap_lines(text, width)
                for line in lines:
                    with self.subTest(width=width, line=line):
                        self.assertLessEqual(self.mod.units(line), capacity + 1e-9,
                                             f"這行超出容量 {capacity:.2f}")

    def test_nothing_lost_or_added(self):
        """切分不得丟字或加字——拼回去要等於原文。"""
        for width in (3.0, 6.0, 10.0):
            for text in SAMPLES:
                with self.subTest(width=width, text=text[:12]):
                    self.assertEqual("".join(self.mod.wrap_lines(text, width)), text)

    def test_short_text_stays_one_line(self):
        self.assertEqual(self.mod.wrap_lines("短句。", 10.0), ["短句。"])

    def test_empty_text_yields_one_empty_line(self):
        """空字串回一行空的，不是空清單——下游按行數配位置，少一行會整段上移。"""
        self.assertEqual(self.mod.wrap_lines("", 6.0), [""])


class ConsistencyWithEstimatorTests(unittest.TestCase):
    """🔴 切分結果的行數必須等於 `est_lines`——否則估高與實際排版是兩套。"""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_line_count_matches_est_lines(self):
        for width in (3.0, 4.5, 6.0, 8.0, 10.0):
            for text in SAMPLES:
                with self.subTest(width=width, text=text[:12]):
                    self.assertEqual(
                        len(self.mod.wrap_lines(text, width)),
                        self.mod.est_lines(text, width),
                        "切分行數與 est_lines 不一致——版面裕度會算錯")


class HangingPunctuationTests(unittest.TestCase):
    """避頭尾：行首不得是中文標點。"""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_no_line_starts_with_punctuation(self):
        """⚠ 掃過多種寬度：標點會不會掉到行首取決於斷點落在哪，
        單一寬度測不出來——這正是它難以目視發現的原因。
        """
        for width_tenth in range(25, 105, 5):      # 2.5in ~ 10.0in
            width = width_tenth / 10
            for text in SAMPLES:
                for line in self.mod.wrap_lines(text, width):
                    if not line:
                        continue
                    with self.subTest(width=width, line=line):
                        self.assertNotIn(line[0], self.mod.NO_LINE_START,
                                         f"行首是禁則標點「{line[0]}」")

    def test_no_line_ends_with_opening_bracket(self):
        """行尾不得是開括號類（下一行開頭會孤零零一個引號）。"""
        text = "他說（這是一段補充說明，用來測試行尾禁則）然後結束。"
        for width_tenth in range(25, 105, 5):
            width = width_tenth / 10
            for line in self.mod.wrap_lines(text, width):
                if not line:
                    continue
                with self.subTest(width=width, line=line):
                    self.assertNotIn(line[-1], self.mod.NO_LINE_END,
                                     f"行尾是禁則字元「{line[-1]}」")

    def test_punctuation_pulled_back_not_hung(self):
        """🔴 標點以**回推**處理（把前一字移到下一行），不用懸掛。

        懸掛（讓標點突出右邊界）是排版慣例，但本 skill 是絕對定位，
        突出去會撞到右側元素——`deck_layout` 的標籤欄頁右欄就緊貼邊界。
        回推的代價是該行少一個字，不會撞版。
        """
        for width_tenth in range(25, 105, 5):
            width = width_tenth / 10
            capacity = self.mod._per_line(width)
            for text in SAMPLES:
                for line in self.mod.wrap_lines(text, width):
                    with self.subTest(width=width, line=line):
                        self.assertLessEqual(self.mod.units(line), capacity + 1e-9,
                                             "回推不該讓行超寬（那是懸掛的行為）")


if __name__ == "__main__":
    unittest.main()
