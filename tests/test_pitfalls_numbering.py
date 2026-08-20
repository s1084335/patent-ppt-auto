"""pitfalls 編號要唯一且被引用得到。

## 為什麼要它

2026-08-14 加一條 pitfalls 時只看了檔尾（最大 40）就用了 41，但 41 已存在於
檔案中段（「中文標點掉到行首」），而且 `SKILL.md` 正引用著它。撞號之後
「見 pitfalls #41」會指到兩條裡的哪一條，取決於讀的人先看到哪一條——
**而且不會有任何東西報錯**。

⚠ 這是恆等式類的閘門（見 `deepen-deck-evidence-layer` design §1.2 三問）：
滿足它的唯一途徑就是把編號改對，沒有「滿足它但把事情做壞」的捷徑。
"""
from __future__ import annotations

import re
import unittest
from collections import Counter
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "html-report-to-deck"
PITFALLS = SKILL_DIR / "references" / "pitfalls.md"


def _numbers() -> list[int]:
    text = PITFALLS.read_text(encoding="utf-8")
    return [int(m.group(1)) for m in re.finditer(r"^\*\*(\d+)\.", text, re.M)]


class PitfallsNumberingTests(unittest.TestCase):
    def test_numbers_are_unique(self):
        dupes = [n for n, c in Counter(_numbers()).items() if c > 1]
        self.assertEqual(dupes, [], f"pitfalls 編號重複：{dupes}")

    def test_referenced_numbers_exist(self):
        """凡是文件裡寫「pitfalls … #N」的 N，都要真的存在。"""
        existing = set(_numbers())
        pattern = re.compile(r"pitfalls[^\n]{0,40}?#(\d+)", re.I)
        missing = []
        for doc in list(SKILL_DIR.rglob("*.md")):
            for m in pattern.finditer(doc.read_text(encoding="utf-8")):
                num = int(m.group(1))
                if num not in existing:
                    missing.append((doc.name, num))
        self.assertEqual(missing, [], f"引用了不存在的 pitfalls 編號：{missing}")


if __name__ == "__main__":
    unittest.main()
