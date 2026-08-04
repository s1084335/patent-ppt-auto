"""J-11（2026-08-04）：封面「分析框架」條不得讀起來像被截斷。

## 症狀（第五輪實機 p1）

「分析框架：專利申請趨勢與專利授權公告趨勢（Trend）→ 等共 16 項分析」——
只放得下**一個**主題名就接收尾，讀者以為句子被切掉。

## 根因

用「實際頁面主題名」串動線，主題名 15–20 字，58 字預算只塞得下 1–2 個，
後面全部折進「等共 N 項」。F-15 修的是收尾被切，沒修「只剩半條動線」。

## 修法

改用**固定的論證鏈分組名**（時間→地域→技術→競爭→機會）＋總項數：
分組名短而穩定，永遠放得下、永遠是完整句，不再依賴哪個主題名先排到。
"""
import sys
import unittest
from pathlib import Path

_SKILL = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt" / "scripts"
sys.path.insert(0, str(_SKILL))
import build_ppt as bp  # noqa: E402


def _spec(topic, kind="chart_hero", appendix=False):
    return bp._spec_with(bp.PAGE_LAYOUT[0], topic=topic, kind=kind, is_appendix=appendix)


class FrameworkLineTests(unittest.TestCase):
    def _layout(self, n):
        return [_spec(f"很長很長的報表主題名稱第{i:02d}號（Level）") for i in range(n)]

    def test_no_half_sentence_tail(self):
        """不得出現「一個主題 → 等共 N 項」這種讀起來像截斷的形狀。"""
        text = bp._framework_text(self._layout(16))
        self.assertNotIn("→ 等共", text, f"仍是半句：{text!r}")

    def test_always_within_budget_and_complete(self):
        for n in (1, 5, 16, 30):
            with self.subTest(n=n):
                text = bp._framework_text(self._layout(n))
                self.assertLessEqual(len(text), bp.FRAMEWORK_BUDGET_CHARS,
                                     f"{n} 項時爆行：{len(text)} 字")
                self.assertIn(f"共 {n} 項分析", text, f"項數不見了：{text!r}")

    def test_empty_layout_still_reads(self):
        self.assertTrue(bp._framework_text([]))


if __name__ == "__main__":
    unittest.main()
