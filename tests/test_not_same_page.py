"""同頁互斥色對的閘門（tasks §6.8）。

## 為什麼需要它

使用者裁決「都留但不得同頁」有兩組，**分離手段不同**：

| 色對 | 怎麼分開 | 誰保證 |
|---|---|---|
| `#00094A`／`#0B2545`（兩套深藍） | **媒介**：報表側 vs deck 側 | `recolor_for_deck` 換色，結構上不可能同頁 |
| `#C62828`／`#DC2626`（兩個紅） | **色階**：生命週期 vs 量級 | ❌ 沒有東西在保證——本檔補上 |

兩個紅**都在報表側**，分不了媒介。實測 ΔE2000 = 4.59（並置可辨）。
`NOT_SAME_PAGE` 目前只是宣告，宣告不等於分開。

## 判準是「渲染後的頁」不是「圖檔本身」

⚠ 雙圖頁會把兩張圖放同一頁：各自合規，**合起來違規**。
只驗單張圖等於沒驗到這個唯一會出事的情境。

## 違規時不得自動換色

⚠ 兩個紅分屬不同色階，自動換會把語意改掉（把「到期」染成「最高」）。
閘門只報告，由人決定拆頁或改圖——這是判斷不是規則。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "html-report-to-deck" / "scripts"

RED_LIFECYCLE = "#C62828"      # STATUS 的「到期」
RED_INTENSITY = "#DC2626"      # INTENSITY 的「最高」／TIER 的 lead≥2


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _svg(*colours: str) -> str:
    body = "".join(f'<rect fill="{c}" width="1" height="1"/>' for c in colours)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
            f'{body}</svg>')


class NotSamePageGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("recolor_for_deck")

    def _run(self, pages: list[dict], charts: dict[str, str]):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        svg_dir = root / "charts"
        svg_dir.mkdir()
        for name, text in charts.items():
            (svg_dir / f"{name}.svg").write_text(text, encoding="utf-8")
        content = root / "content.json"
        content.write_text(json.dumps({"pages": pages}, ensure_ascii=False),
                           encoding="utf-8")
        return self.mod.check_pages(content, svg_dir)

    def test_two_reds_on_one_page_is_flagged(self):
        """🔴 雙圖頁：兩張圖各自合規，合起來違規。"""
        bad = self._run(
            [{"title": "雙圖頁", "charts": ["status", "bubble"]}],
            {"status": _svg(RED_LIFECYCLE), "bubble": _svg(RED_INTENSITY)})
        self.assertTrue(bad, "同頁兩個紅沒被抓到")
        self.assertTrue(any(RED_LIFECYCLE in b and RED_INTENSITY in b for b in bad), bad)

    def test_same_reds_on_different_pages_is_fine(self):
        """⚠ 反面要驗：分在不同頁是**合規**的，那正是「都留」的意思。

        擋掉這個等於把使用者的裁決做成「收斂成一個」。
        """
        bad = self._run(
            [{"title": "A", "charts": ["status"]}, {"title": "B", "charts": ["bubble"]}],
            {"status": _svg(RED_LIFECYCLE), "bubble": _svg(RED_INTENSITY)})
        self.assertEqual(bad, [], f"不同頁被誤判成違規：{bad}")

    def test_one_chart_carrying_both_is_flagged(self):
        """單張圖同時用到兩個紅——同樣違規，而且更難目視發現。"""
        bad = self._run(
            [{"title": "單圖", "charts": ["mixed"]}],
            {"mixed": _svg(RED_LIFECYCLE, RED_INTENSITY)})
        self.assertTrue(bad, "單張圖裡的兩個紅沒被抓到")

    def test_missing_chart_file_is_reported_not_skipped(self):
        """⚠ 宣告了圖但檔案不在——必須報出來，不能當成「這頁沒有色」放行。

        靜默略過會讓缺圖的頁永遠合規（缺席型偏差）。
        """
        bad = self._run([{"title": "缺圖", "charts": ["nope"]}], {})
        self.assertTrue(any("nope" in b for b in bad), f"缺圖沒被報出：{bad}")

    def test_pages_without_charts_are_not_errors(self):
        """純文字頁沒有圖，不該被當成問題。"""
        self.assertEqual(self._run([{"title": "文字頁", "charts": []}], {}), [])

    def test_gate_does_not_mutate_files(self):
        """🔴 §6.8.4：違規時**不得自動換色**。

        兩個紅分屬不同色階，自動換會把「到期」染成「最高」——語意被改掉，
        而且畫面看起來還變「正確」了。閘門只報告。
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        svg_dir = root / "charts"
        svg_dir.mkdir()
        # ⚠ 素材裡**必須放一個會被換色的色**（報表側深藍）：不放的話，
        #   「閘門偷偷呼叫 recolor」這個突變剛好是 no-op（兩個紅不在對照表上），
        #   測試會綠而其實沒守住。反向驗證 M5 當場證明了這一點。
        original = _svg(RED_LIFECYCLE, RED_INTENSITY, "#00094A")
        (svg_dir / "mixed.svg").write_text(original, encoding="utf-8")
        content = root / "content.json"
        content.write_text(
            json.dumps({"pages": [{"title": "x", "charts": ["mixed"]}]}),
            encoding="utf-8")
        self.mod.check_pages(content, svg_dir)
        self.assertEqual((svg_dir / "mixed.svg").read_text(encoding="utf-8"),
                         original, "閘門改了檔案——它只該報告")


if __name__ == "__main__":
    unittest.main()
