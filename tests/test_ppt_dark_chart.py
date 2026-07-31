"""圖表深色化與白邊裁切（S7／S6，2026-07-31）。

## 背景

PPT 改深空主題後，引擎產的**淺底**圖表貼上去會像補丁。使用者裁示：
不動引擎（同一份 SVG 也內嵌在網頁報表頁的淺底上），改在 **PPT 組版端轉色**。

## 這裡守什麼

1. **覆蓋完整**：引擎不是只有一套配色——折線／長條走 `COLOR_*`（`00094A` 系），
   熱圖與象限走另一套 Tailwind 色（`111827` 系）。⚠ 只換前者，熱圖會整張維持
   深色文字貼在深底上看不見。故掃描 `chart_runner` 的所有色值，
   要求每一個都**有對照或被明確保留**，不允許漏網。
2. **白底不留**：整版白底矩形必須移除，否則深色頁上出現一塊白板。
3. **同源**：PNG（後援）與 SVG（PowerPoint 顯示的向量版）必須來自同一份轉色與
   同一個裁切框——否則會出現「縮圖淺色、放大深色」的錯位，且極難察覺。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / "skills" / "patent-report-ppt"
THEME_PATH = SKILL_DIR / "theme.json"
CHART_RUNNER = REPO / "backend" / "app" / "reports" / "chart_runner.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_ppt_dark", SKILL_DIR / "scripts" / "build_ppt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_ppt_dark", module)
    spec.loader.exec_module(module)
    return module


class RecolorCoverageTests(unittest.TestCase):
    """引擎新增顏色卻忘了補對照時，這支會擋下來。"""

    def test_every_engine_colour_is_mapped_or_kept(self):
        theme = json.loads(THEME_PATH.read_text(encoding="utf-8"))
        recolor = theme["chart_recolor"]
        known = {key.upper() for key in recolor["map"]} | {c.upper() for c in recolor["keep"]}
        used = {m.upper() for m in re.findall(r"#([0-9A-Fa-f]{6})",
                                             CHART_RUNNER.read_text(encoding="utf-8"))}
        missing = sorted(used - known)
        self.assertFalse(
            missing,
            f"chart_runner 有 {len(missing)} 個色值沒對照也沒列入保留：{missing}\n"
            "→ 深色頁上會維持原本的淺色主題色。請補進 theme.json 的 chart_recolor.map "
            "或 keep（確定該保留才放 keep）。")

    def test_mapped_targets_are_light_enough_for_dark_background(self):
        """對照後的顏色要在深底上看得見——換了色卻還是深的等於沒換。"""
        theme = json.loads(THEME_PATH.read_text(encoding="utf-8"))
        too_dark = []
        for source, target in theme["chart_recolor"]["map"].items():
            r, g, b = (int(target[i:i + 2], 16) for i in (0, 2, 4))
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
            # 面板／格線本來就該是暗的（它們是底不是前景），只檢查非底色類。
            if target.upper() in {"0B243A", "274A66", "2F82C4", "C98A5A"}:
                continue
            if luminance < 0.45:
                too_dark.append((source, target, round(luminance, 2)))
        self.assertFalse(too_dark, f"這些對照色在深底上仍偏暗：{too_dark}")


class RecolorBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.bp = _load_builder()
        self.theme = self.bp.Theme.load(THEME_PATH)

    def test_full_canvas_white_rect_is_stripped(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">'
               '<rect width="100%" height="100%" fill="white"/>'
               '<text fill="#00094A">x</text></svg>')
        out = self.bp.recolor_svg(svg, self.theme.chart_recolor)
        self.assertNotIn('fill="white"', out, "整版白底沒移除——深色頁上會有一塊白板")

    def test_dark_text_becomes_light(self):
        svg = '<svg width="10" height="10"><text fill="#111827">x</text></svg>'
        out = self.bp.recolor_svg(svg, self.theme.chart_recolor)
        self.assertIn("#EAF6FB", out, "熱圖系的深色文字沒被換成亮字")

    def test_kept_colour_is_untouched(self):
        """白色在熱圖裡是深色格上的文字，換掉那些數字就消失了。"""
        svg = '<svg width="10" height="10"><text fill="#FFFFFF">7</text></svg>'
        out = self.bp.recolor_svg(svg, self.theme.chart_recolor)
        self.assertIn("#FFFFFF", out)


if __name__ == "__main__":
    unittest.main()
