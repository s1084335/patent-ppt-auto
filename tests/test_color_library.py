"""色彩庫是**給 CLI 挑的**，比照版型庫（tasks §6.5b，2026-08-19 使用者提醒）。

## 我前幾批偏掉的方向

使用者：「色彩庫和版型庫本來就是要給 CLI 去選擇的，這個要記得。」

前兩批我把色票當成**引擎內部常數**在收（`chart_sizing.PALETTE`／`SCALES`），
那部分是對的，但**漏了 CLI 面向的那一半**：CLI 現在挑的是
`deck_layout.PALETTE`（cyan／blue／amber／rose／green），而它——

- `check_content` **完全不驗**：CLI 寫 `"color": "purple"` 一路走到 `make_deck`
  才 KeyError，錯誤訊息與內容問題無關，修稿輪也修不掉
- 沒有語意用途：SKILL.md 只列了五個名字，沒說什麼情況用哪個
- 沒有三處同步閘門：版型庫（§7a.2）有「能畫的／會擋的／CLI 照抄的」三者集合
  必須相等，色彩庫**一道都沒有**

⚠ 這正是「同一份清單三處各說各話」的同型問題，§7a 才剛修完一次。

## 兩層要分清楚

| 層 | 誰用 | 落點 |
|---|---|---|
| 引擎內部色 / 色階 | `chart_runner` 畫圖 | `chart_sizing.PALETTE`／`SCALES` |
| **CLI 可挑的色** | CLI 寫進 `content.json` | `deck_layout.PALETTE` |

CLI 一律**挑名字**，不寫 hex——同版型 `layout: "chart"` 而不是寫幾何。
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "html-report-to-deck"
SCRIPTS = SKILL / "scripts"
PY = sys.executable


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class ColorLibraryRegistryTests(unittest.TestCase):
    """色彩庫本身：要有語意，不能只是名字對色值。"""

    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")

    def test_library_declares_purpose_for_each_colour(self):
        """⚠ 只有 `{"cyan": RGBColor(...)}` 等於沒告訴 CLI 什麼時候用哪個。

        版型庫（`LAYOUTS`）每個版型都帶用途說明，色彩庫沒有理由例外。
        """
        lib = getattr(self.dl, "COLOR_LIBRARY", None)
        self.assertIsNotNone(lib, "缺 COLOR_LIBRARY——CLI 挑色沒有可讀的清單")
        for name, entry in lib.items():
            with self.subTest(colour=name):
                self.assertTrue(
                    str(entry.get("purpose", "")).strip(),
                    f"{name} 沒有語意用途，CLI 只能靠名字猜")

    def test_library_covers_the_rendering_palette(self):
        """⚠ 能畫的與能挑的必須是同一組，否則會有「畫得出但不准挑」的死色。"""
        self.assertEqual(
            set(self.dl.COLOR_LIBRARY), set(self.dl.PALETTE),
            "色彩庫與渲染色表不一致——兩份清單各自演進的起點")


class GateRejectsUnknownColourTests(unittest.TestCase):
    """CLI 挑了庫外的色要**在閘門就擋**，不是拖到 make_deck 才 KeyError。"""

    def _check(self, colour: str):
        from tests.test_deck_caliber_page import _minimal_content

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        content = _minimal_content()
        content["pages"] = [{"title": "頁", "takeaway": "t", "charts": [],
                             "lines": ["內容"], "tag": None}]
        for rec in content.get("recommendations") or []:
            rec["color"] = colour
        path = work / "content.json"
        path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return subprocess.run(
            [PY, str(SCRIPTS / "check_content.py"), str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")

    def test_unknown_colour_is_rejected(self):
        proc = self._check("purple")
        self.assertEqual(proc.returncode, 1, "庫外的色沒被擋")
        self.assertIn("purple", proc.stdout)

    def test_known_colour_passes(self):
        """⚠ 反面也要驗：只擋不放行的閘門會逼 CLI 亂改到過為止。"""
        proc = self._check("amber")
        self.assertNotIn("色", proc.stdout.split("\n")[0] if proc.stdout else "",
                         proc.stdout)


class ThreeWaySyncTests(unittest.TestCase):
    """§7a.2 同一套作法：能畫的／會擋的／CLI 照抄的，三者集合必須相等。"""

    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")
        cls.names = set(cls.dl.COLOR_LIBRARY)

    def test_skill_doc_lists_every_colour(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        missing = [n for n in self.names if f"`{n}`" not in text]
        self.assertEqual(missing, [], f"SKILL.md 沒列到：{missing}")

    def test_gate_reads_the_live_registry_not_a_copy(self):
        """⚠ 驗**行為**：往色彩庫塞一個新色，閘門必須跟著接受。

        初版寫成 `assertIn("COLOR_LIBRARY", 原始碼)`——那是字串存在型斷言，
        而反向驗證當場證明它守不住：把閘門改成**自己抄一份同名清單**，
        原始碼裡照樣有那幾個字，測試照樣綠（本專案同型陷阱第 7 次）。

        改為在執行期把新色注入 `deck_layout.COLOR_LIBRARY`，若閘門讀的是
        自己抄的副本，它不會知道這個新色 → 仍會判成庫外色 → 測試紅。
        """
        import check_content
        import deck_layout

        original = dict(deck_layout.COLOR_LIBRARY)
        deck_layout.COLOR_LIBRARY["teal_probe"] = {
            "rgb": original["cyan"]["rgb"], "purpose": "測試用臨時色"}
        try:
            bad = check_content._check_p2_evidence_rules({
                "recommendations": [{
                    "title": "A", "tag": "t", "color": "teal_probe",
                    "evidence": "依據：TW123456", "lines": ["依據：TW123456"],
                }],
                "pages": [],
            })
        finally:
            deck_layout.COLOR_LIBRARY.clear()
            deck_layout.COLOR_LIBRARY.update(original)
        colour_errors = [b for b in bad if "色彩庫" in b]
        self.assertEqual(
            colour_errors, [],
            "閘門不認得剛加進色彩庫的色——它讀的是自己抄的副本，兩份會分岔")


class StaleRoadmapInSkillDocTests(unittest.TestCase):
    """🔴 §7d 的收尾漏網：SKILL.md 還在教已移除的 roadmap。

    第 2 段移除路線圖頁時只清了 `content-template.json`，SKILL.md 沒跟上。
    ⚠ CLI **同時讀這兩份**，範本沒有而 SKILL 有，等於教它寫一個畫不出來的區塊。
    """

    def test_skill_no_longer_teaches_roadmap(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for stale in ('"roadmap"', '"roadmap_title"', "短期　0–3 個月"):
            with self.subTest(token=stale):
                self.assertNotIn(
                    stale, text,
                    f"SKILL.md 仍教已移除的 {stale}（§7d 路線圖頁已併入結論頁）")

    def test_skill_page_count_formula_updated(self):
        """⚠ 「圖表數＋3（封面、結論、路線圖）」的 3 也要跟著改。"""
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotRegex(
            text, r"圖表數\s*[＋+]\s*3.*路線圖",
            "頁數公式仍把路線圖算進去")


if __name__ == "__main__":
    unittest.main()
