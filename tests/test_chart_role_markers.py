"""圖元角色用 `data-role` 標記，不得用顏色值辨認（tasks §6.3a）。

## 根因

`rebuild_chip_chart.parse()` 的 docstring 寫著「以**結構**（屬性、位置、順序）
辨識元素，**不比對任何特定字串**」——但 L57／L67 做的正是比對特定字串：

```python
d["note"]   = next((t for a, _, t in head             if "#9CA3AF" in a), "")
d["footer"] = next((t for a, _, t in reversed(tail)   if "#9CA3AF" in a), "")
```

它靠 `#9CA3AF` 這個**色值**辨認「哪一段文字是註記／頁尾」。

## 為什麼現在必須修

§6.2 裁決「兩套深藍都留但不得同頁」，做法是 SVG 進 deck 時整批換色。
換色一上，這兩行就找不到目標，`next(..., "")` **回空字串**——註記與 FTO 頁尾
從重排後的圖上直接消失，而且**沒有任何東西會報錯**。

⚠ 這是缺席型偏差最典型的形狀：壞掉的證據就是「東西不見了」，
而不見的東西不會自己舉手。

## 修法：沿用既有的 `data-role`

`chart-title` 早就用 `data-role` 標記（chart_runner 有 11 處），
角色標記由**產生端**打、消費端只讀——同「一方產生、一方消費」。
顏色是樣式，樣式可以改；角色是語意，語意才能拿來辨認。

⚠ 本檔的關鍵是 `test_parse_survives_recolor`：它**把換色這個突變做進測試**。
只斷言「有 data-role」不夠——那只是換一種字串比對；要證明的是
「顏色變了，解析仍然對」。
"""
from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "html-report-to-deck" / "scripts"

#: 產生端該打的角色標記（🔴 唯一定義處：消費端與本測試都讀這裡）
NOTE_ROLE = "chart-note"
FOOTER_ROLE = "chart-footer"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _quadrant_svg() -> str:
    """用引擎真的產一張 opportunity_quadrant，不手寫假 SVG。

    ⚠ 手寫假 SVG 會讓測試通過而真圖失敗——假的那份是我照著期望寫的，
    等於自己驗自己。
    """
    from backend.app.reports import chart_runner as cr

    rows = [
        {"topic_code": "T001", "label": "拉繩滑雪模擬機構", "patent_count": 10,
         "applicant_count": 9, "leading_count": 2},
        {"topic_code": "T002", "label": "馬達自鎖阻力機構", "patent_count": 6,
         "applicant_count": 2, "leading_count": 1},
        {"topic_code": "T003", "label": "捲輪回捲機構", "patent_count": 3,
         "applicant_count": 3, "leading_count": 0},
    ]
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "opportunity_quadrant.svg"
        cr.render_opportunity_quadrant_svg(out, "機會四象限", {
            "rows": rows,
            "patent_count_median": 6,
            "applicant_count_median": 3,
        })
        return out.read_text(encoding="utf-8")


class EngineEmitsRoleMarkersTests(unittest.TestCase):
    """產生端要打角色標記。"""

    @classmethod
    def setUpClass(cls):
        cls.svg = _quadrant_svg()

    def test_note_carries_role(self):
        self.assertIn(
            f'data-role="{NOTE_ROLE}"', self.svg,
            "口徑防呆註沒有角色標記——消費端只能靠顏色認它")

    def test_footer_carries_role(self):
        self.assertIn(
            f'data-role="{FOOTER_ROLE}"', self.svg,
            "FTO 頁尾沒有角色標記")

    def test_roles_are_unique(self):
        """⚠ 角色標記重複＝消費端 `next(...)` 拿到哪一個是碰運氣。"""
        for role in (NOTE_ROLE, FOOTER_ROLE):
            with self.subTest(role=role):
                self.assertEqual(
                    self.svg.count(f'data-role="{role}"'), 1,
                    f"{role} 出現不只一次，消費端會取到不確定的那個")


class ParseUsesRoleNotColorTests(unittest.TestCase):
    """🔴 本檔的重點：顏色變了，解析仍要對。"""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load("rebuild_chip_chart")
        cls.svg = _quadrant_svg()

    def test_parse_finds_note_and_footer(self):
        d = self.mod.parse(self.svg)
        self.assertTrue(d["note"].strip(), "解析不到口徑防呆註")
        self.assertTrue(d["footer"].strip(), "解析不到 FTO 頁尾")

    def test_parse_survives_recolor(self):
        """把 SVG 裡所有 #9CA3AF 換掉（＝§6.2 的 deck 換色會做的事）。

        ⚠ 這條就是 §6.2 上線後的實況預演。原實作在這裡會回空字串而不報錯。
        """
        before = self.mod.parse(self.svg)
        recolored = self.svg.replace("#9CA3AF", "#53698B")
        self.assertNotEqual(recolored, self.svg, "測試素材裡沒有該色，這條沒驗到東西")
        after = self.mod.parse(recolored)
        self.assertEqual(
            after["note"], before["note"],
            "換色後解析不到註記——顏色被當成語意標記了（換色會靜默吃掉內容）")
        self.assertEqual(
            after["footer"], before["footer"],
            "換色後解析不到頁尾")

    def test_rebuild_output_keeps_role_markers(self):
        """重排後吐出的 SVG 自己也要帶角色標記。

        ⚠ 只在**讀**的那一端認角色、**寫**的那一端不打，等於把問題往下游推一格：
        重排過的圖被誰再讀一次，就又回到「猜」。`build` 已經幫 `chart-title`
        打了標記，note／footer 沒有理由例外。

        ⚠ 這裡**不驗完整往返**（`parse(build(...))`）：`build` 吐的象限方塊
        與 `parse` 期望的 rect 格式本來就不同——這支腳本的設計是對引擎原圖跑
        **一次**、就地覆寫，從來不支援讀自己的輸出。要求它往返是我測過頭，
        會逼出一個沒人需要的相容層。此限制記錄於此，不靜默略過。
        """
        rebuilt = self.mod.build(self.mod.parse(self.svg))
        for role in (NOTE_ROLE, FOOTER_ROLE):
            with self.subTest(role=role):
                self.assertIn(
                    f'data-role="{role}"', rebuilt,
                    f"重排輸出沒帶 {role} 標記——下游只能靠顏色認")

    def test_no_color_literal_used_as_selector(self):
        """結構性判準：`parse` 裡不得出現用色值當條件的比對。

        ⚠ 這條是輔助，不是主證據——主證據是上面的換色突變。
        單獨用它會變成「改個寫法就綠」的字面檢查。
        """
        import inspect

        from tests.source_assertions import executable_source

        src = executable_source(inspect.getsource(self.mod.parse))
        hits = re.findall(r'"#[0-9A-Fa-f]{6}" in ', src)
        self.assertEqual(
            hits, [],
            f"仍用色值當選擇器：{hits}——顏色是樣式，樣式會改；角色才是語意")


if __name__ == "__main__":
    unittest.main()
