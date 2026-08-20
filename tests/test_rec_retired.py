"""建議頁（rec）退場，結論頁成為唯一的第 2 頁（tasks §9.1／§9.3）。

## 為什麼退場

`conclusions` 宣告時本來就**取代** rec（§7.7「同一問題不答兩次」），
而 §9.2c 之後「沒有分群主題就不產簡報」已是硬檢查——也就是 conclusions
一定會有內容，rec 那條分支永遠走不到。

留著一條走不到的分支不是中性的：
- 它有**自己的數量鎖**（`len(recommendations) not in (3, 4, 5)`），
  而使用者 2026-08-19 要求「不一定只能提四項」
- 它的版面**寫死兩排**（`need_total = 2 * gh`），5 張會溢出而裕度檢查不會擋
  ——「允許 3–5」是假的
- SKILL.md 與範本還在教它，CLI 會照抄

## 順帶清掉 `slide_roadmap`

⚠ 第 2 段（§7d）只把 `_compose` 裡的呼叫拿掉，**函式本體留著**。
死碼本身無害，但它是 `PALETTE[r["color"]]` 的第二個消費者——
判斷「色彩庫還有沒有人用」時會把它算進去，於是判斷失準。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "html-report-to-deck"
SCRIPTS = SKILL / "scripts"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class RecRetiredTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")
        cls.code = (SCRIPTS / "deck_layout.py").read_text(encoding="utf-8")

    def test_slide_rec_is_gone(self):
        self.assertFalse(hasattr(self.dl, "slide_rec"),
                         "slide_rec 還在——結論頁已是唯一的第 2 頁")

    def test_slide_roadmap_is_gone(self):
        """⚠ §7d 只拿掉呼叫、函式留著。死碼會讓「還有誰在用色彩庫」判斷失準。"""
        self.assertFalse(hasattr(self.dl, "slide_roadmap"),
                         "slide_roadmap 還在（§7d 已移除該頁）")

    def test_compose_always_uses_conclusions(self):
        from tests.source_assertions import executable_source

        src = executable_source(self.code)
        self.assertNotIn(
            "slide_rec(", src,
            "_compose 仍有走 rec 的分支——§9.2c 之後那條路永遠走不到")


class CountLockRemovedTests(unittest.TestCase):
    """🔴 使用者：「結論或研發方向不一定只能提四項。」"""

    def test_no_three_to_five_lock(self):
        from tests.source_assertions import executable_source

        code = executable_source(
            (SCRIPTS / "check_content.py").read_text(encoding="utf-8"))
        self.assertNotIn(
            "(3, 4, 5)", code,
            "數量鎖還在——數量鎖與 v5／v7／v9 形式鎖同族，已證明守不住")

    def test_template_no_longer_demonstrates_four_cards(self):
        """⚠ 範本示範幾張，CLI 就照抄幾張（§7 已證實）。"""
        data = json.loads(
            (SKILL / "references" / "content-template.json").read_text(encoding="utf-8"))
        self.assertNotIn("recommendations", data,
                         "範本仍教 recommendations——rec 已退場")

    def test_skill_doc_no_longer_teaches_rec(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for stale in ("建議卡用 4 張", "3–5 張", '"recommendations"'):
            with self.subTest(token=stale):
                self.assertNotIn(stale, text, f"SKILL.md 仍教 {stale}")


class ColorLibraryStillHasConsumerTests(unittest.TestCase):
    """🔴 rec 退場後，CLI 可挑的色彩庫不得變成孤兒。

    使用者 2026-08-19：「色彩庫和版型庫本來就是要給 CLI 去選擇的。」
    ⚠ rec 是 `PALETTE[r["color"]]` 的唯一真消費者（另一個是死碼 slide_roadmap）。
    兩個都拿掉的話，色彩庫就沒有人用了——那等於把使用者明確要的東西做成擺設。

    現行 `pages[].tag` 已經有一套色（`TAG_COLOR`），但它**不在色彩庫裡**，
    是第三份各說各話的清單。退場後把 CLI 的挑色能力收斂到 tag 這一條。
    """

    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")

    def test_tag_colors_come_from_the_library(self):
        """`TAG_COLOR` 的值必須取自色彩庫，不得自己寫一份。"""
        lib = self.dl.COLOR_LIBRARY
        known = {tuple(e["rgb"]) for e in lib.values()}
        stray = {tag: tuple(c) for tag, c in self.dl.TAG_COLOR.items()
                 if tuple(c) not in known}
        self.assertEqual(stray, {}, f"標籤色不在色彩庫內：{stray}")

    def test_library_is_not_orphaned(self):
        """⚠ 色彩庫必須有真的消費者，不能只是登記表。"""
        code = (SCRIPTS / "deck_layout.py").read_text(encoding="utf-8")
        self.assertRegex(
            code, r"TAG_COLOR\s*=\s*\{[^}]*COLOR_LIBRARY",
            "色彩庫沒有任何消費者——使用者要的是「給 CLI 挑」，不是一張擺設清單")


class TagIsTheCliColourChoiceTests(unittest.TestCase):
    """CLI 挑色的入口收斂到 `tag`；挑庫外的要被擋。"""

    def test_unknown_tag_is_rejected(self):
        import check_content

        bad = check_content._check_p2_evidence_rules({
            "pages": [{"title": "頁", "takeaway": "t", "charts": [],
                       "lines": ["內容"], "tag": "沒這個標籤"}],
        })
        self.assertTrue(any("標籤" in b for b in bad),
                        f"庫外的標籤沒被擋：{bad}")

    def test_known_tag_passes(self):
        import check_content
        import deck_layout

        tag = next(iter(deck_layout.TAG_COLOR))
        bad = check_content._check_p2_evidence_rules({
            "pages": [{"title": "頁", "takeaway": "t", "charts": [],
                       "lines": ["內容"], "tag": tag}],
        })
        self.assertEqual([b for b in bad if "標籤" in b], [],
                         f"合法標籤 {tag} 被誤擋")

    def test_none_tag_is_allowed(self):
        """⚠ 不掛標籤是合法的——強制每頁都要標籤是形式鎖。"""
        import check_content

        bad = check_content._check_p2_evidence_rules({
            "pages": [{"title": "頁", "takeaway": "t", "charts": [],
                       "lines": ["內容"], "tag": None}],
        })
        self.assertEqual([b for b in bad if "標籤" in b], [])


if __name__ == "__main__":
    unittest.main()
