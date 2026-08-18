"""版型庫要有唯一定義處，且三處必須同步（tasks §7a）。

## 根因：三份清單各說各話

| 誰 | 知道什麼 |
|---|---|
| `deck_layout`（能畫的） | 依 `charts` 有無分派 chart／text，text 再看 `layout:"label"` |
| `check_content`（會擋的） | 只認得 `layout:"label"` |
| `content-template.json`（CLI 照抄的） | 只示範 `layout:"label"` |

⚠ 實例：`conclusions` **有畫法（`slide_conclusions`）、有閘門（`_check_conclusions`）、
但範本裡沒有**——CLI 不宣告就 `if not cc: return []` 靜默放行，那頁根本不產出。
這與同期修的兩個 bug 同型：**能力在、守門在，中間那段沒接上**。

## 這道閘門要的是「三處同步」不是「有沒有寫」

`LAYOUTS` 是唯一定義處；三處都必須涵蓋它的全部鍵。新增版型時，
漏在任何一處都會紅——而不是等到實機發現 CLI 從來不用那個版型（缺席型）。

⚠ 三問：Q1 過（集合比對，純機械）、Q2 **過**（要讓它綠只有一種方式＝三處都寫，
沒有更省力的路）、Q3 不適用。這是恆等式型閘門，零自由度。
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "html-report-to-deck"
sys.path.insert(0, str(SKILL / "scripts"))


class RegistryExistsTests(unittest.TestCase):
    def test_layouts_registry_is_declared(self):
        """🔴 唯一定義處（比照 `ACTION_VERBS`）。"""
        import deck_layout

        self.assertTrue(
            hasattr(deck_layout, "LAYOUTS"),
            "沒有版型清單的唯一定義處——三處各自維護就會像 conclusions 那樣漏掉一處")

    def test_registry_documents_each_layout(self):
        """每個版型要寫用途，否則 CLI 不知道何時該用（等於永遠不用）。"""
        import deck_layout

        for name, desc in deck_layout.LAYOUTS.items():
            with self.subTest(layout=name):
                self.assertTrue(
                    str(desc).strip(),
                    f"版型 {name} 沒有說明——CLI 不知道何時用，結果就是不用（缺席型）")

    def test_registry_matches_what_compose_can_draw(self):
        """⚠ 清單不得宣告畫不出來的版型（那會讓 CLI 產出無法組版的內容）。"""
        import deck_layout

        src = (SKILL / "scripts" / "deck_layout.py").read_text(encoding="utf-8")
        for name in deck_layout.LAYOUTS:
            with self.subTest(layout=name):
                self.assertRegex(
                    src, rf'(?:layout"?\)?\s*==\s*"{name}"|def slide_{name})',
                    f"LAYOUTS 宣告了 {name} 但 deck_layout 沒有對應畫法")


class ThreeWaySyncTests(unittest.TestCase):
    """🔴 核心：能畫的／會擋的／CLI 照抄的，三者必須涵蓋 LAYOUTS 全部。"""

    def _layouts(self) -> dict:
        import deck_layout

        return deck_layout.LAYOUTS

    def test_check_content_recognises_every_layout(self):
        src = (SKILL / "scripts" / "check_content.py").read_text(encoding="utf-8")
        missing = [n for n in self._layouts()
                   if not re.search(rf'"{n}"', src)]
        self.assertEqual(
            missing, [],
            f"閘門不認得這些版型：{missing}——CLI 用了也不會被檢查")

    def test_template_demonstrates_every_layout(self):
        text = (SKILL / "references" / "content-template.json").read_text(encoding="utf-8")
        missing = [n for n in self._layouts() if f'"{n}"' not in text]
        self.assertEqual(
            missing, [],
            f"範本沒有示範這些版型：{missing}——CLI 照抄範本，沒示範等於不知道能用")

    def test_narrative_guide_mentions_every_layout(self):
        text = (SKILL / "references" / "narrative.md").read_text(encoding="utf-8")
        missing = [n for n in self._layouts() if n not in text]
        self.assertEqual(
            missing, [],
            f"寫作指引沒提到這些版型：{missing}——CLI 不知道何時該用")


class ConclusionsInTemplateTests(unittest.TestCase):
    """§7a.4：`conclusions` 有畫法有閘門，範本卻沒有。"""

    def test_template_declares_conclusions(self):
        data = json.loads(
            (SKILL / "references" / "content-template.json").read_text(encoding="utf-8"))
        self.assertIn(
            "conclusions", data,
            "範本沒有 conclusions——CLI 不宣告，_check_conclusions 第一行就 return []，"
            "那頁根本不會產出，而且不會有任何人發現")

    def test_conclusions_example_has_four_columns(self):
        data = json.loads(
            (SKILL / "references" / "content-template.json").read_text(encoding="utf-8"))
        rows = (data.get("conclusions") or {}).get("rows") or []
        self.assertTrue(rows, "conclusions 範例沒有列")
        for field in ("topic", "finding", "reading", "action"):
            self.assertIn(
                field, rows[0],
                f"conclusions 範例缺「{field}」——閘門要求四欄齊備")


if __name__ == "__main__":
    unittest.main()
