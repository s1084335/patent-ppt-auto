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


# ⚠ 2026-08-21：deck 交付線退場，相關落點自本檔移除（封存於 tag archive/2026-08-20/add-deck-delivery-line）。
#   （原 ParseUsesRoleNotColorTests 驗 rebuild_chip_chart／recolor_for_deck；
#     引擎側「產出 role marker」的覆蓋仍在 EngineEmitsRoleMarkersTests）


if __name__ == "__main__":
    unittest.main()
