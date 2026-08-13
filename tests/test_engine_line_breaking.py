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
        """⚠ 2026-08-13 起 `est_lines` **委派**給 `wrap_lines`，本測試因此成為
        結構保證而非數值驗證（自己跟自己比必然通過，見 pitfalls #39）。
        留著是為了：有人把 `est_lines` 改回獨立算法時立刻紅。
        真正的數值驗證在 `test_est_lines_delegates`（原始碼）與
        `test_text_height_model.py`（對 COM 實測值）。
        """
        for width in (3.0, 4.5, 6.0, 8.0, 10.0):
            for text in SAMPLES:
                with self.subTest(width=width, text=text[:12]):
                    self.assertEqual(
                        len(self.mod.wrap_lines(text, width)),
                        self.mod.est_lines(text, width),
                        "切分行數與 est_lines 不一致——版面裕度會算錯")

    def test_est_lines_delegates(self):
        """🔴 `est_lines` 必須委派，不得自己算。

        獨立的數學式在加入詞組保護後就會與實際切分分岔——保護讓某些行提早斷，
        實際行數多一行，於是估高一套、排版另一套，版面偶爾溢出而裕度表不叫。
        """
        source = (SCRIPTS / "deck_layout.py").read_text(encoding="utf-8")
        body = source.split("def est_lines(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("wrap_lines", body, "est_lines 應委派給 wrap_lines")
        self.assertNotIn("math.ceil", body, "不得殘留獨立的數學估算")


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


class UnbreakableTokenTests(unittest.TestCase):
    """不可分割詞組：專利號之類「前綴＋空格＋號碼」不得從空格處斷開。

    ## 怎麼發現的

    2026-08-13 逐頁目視 `regression_baseline/slide05`（標籤欄頁）：第二行結尾是
    孤立的「CN」，號碼「223248696）。」被推到第三行。
    ⚠ 那張是 pptx 路徑（PowerPoint 斷的），**不是 B 案引入的**；但 B 案把斷行
    收回引擎後，`wrap_lines` 只看字寬，會照樣拆。

    🔴 這是「只有目視看得到」的一類：程式化檢查全綠、SVG 合法、字寬也沒超，
    但讀者會看到孤立的「CN」。它同時證明了逐頁目視不可抽樣。

    ## 判準

    空格兩側都非 CJK、且**至少一側含數字** → 不可分割。
    - `CN 223248696`、`A63B 069/18`、`US 12345678` → 保護
    - `11 件`、`2020 年` → 不保護（CJK 側本來就可斷）
    - `the quick` → 不保護（兩側都沒數字，是一般英文）
    """

    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    PROTECTED = ["CN 223248696", "A63B 069/18", "US 12345678"]

    def test_patent_number_not_split(self):
        text = ("本頁列出三案：CN 111167066、CN 211798524、CN 223248694，"
                "另有 CN 223248696 待補。")
        for width_tenth in range(25, 105, 5):
            width = width_tenth / 10
            lines = self.mod.wrap_lines(text, width)
            for index, line in enumerate(lines[:-1]):
                with self.subTest(width=width, line=line):
                    # 行尾若是「CN」這種前綴，下一行開頭就是被拆開的號碼
                    self.assertFalse(
                        line.rstrip().endswith("CN"),
                        f"專利號被拆開：行尾『CN』，下一行『{lines[index + 1][:12]}』")

    def test_protected_tokens_stay_whole(self):
        """受保護的詞組必須整串出現在同一行（除非它本身就超過行寬）。"""
        for token in self.PROTECTED:
            for width_tenth in range(30, 105, 5):
                width = width_tenth / 10
                if self.mod.units(token) > self.mod._per_line(width):
                    continue                # 詞組本身放不下，允許斷（fail open）
                text = f"前置文字說明一下，{token}，後面還有一些補充內容。"
                joined = "\n".join(self.mod.wrap_lines(text, width))
                with self.subTest(token=token, width=width):
                    self.assertIn(token, joined.replace("\n", ""),
                                  "文字內容改變了")
                    self.assertIn(token, joined,
                                  f"『{token}』被斷行拆開")

    def test_oversized_token_still_breaks(self):
        """⚠ 詞組本身超過行寬時仍須斷——否則會無限迴圈或整行溢出。"""
        text = "AB 1234567890123456789012345678901234567890"
        lines = self.mod.wrap_lines(text, 2.5)
        self.assertGreater(len(lines), 1, "超長詞組沒有被斷開")
        self.assertEqual("".join(lines), text, "斷開時丟字了")

    def test_cjk_side_still_breaks(self):
        """`11 件`／`2020 年` 這種 CJK 側不受保護——否則會過度限制斷點。"""
        text = "共 11 件、2020 年至 2026 年之間，分布於五個受理局與八個技術主題。"
        # 只要能正常切成多行即可（不因保護規則而卡死）
        lines = self.mod.wrap_lines(text, 3.0)
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), text)


if __name__ == "__main__":
    unittest.main()
