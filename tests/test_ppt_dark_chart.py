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


class PairedTextRecolorTests(unittest.TestCase):
    """轉色後，畫在圖元上的文字要依**新底色**重算（2026-07-31 引擎批 E1）。

    ## 為什麼需要

    引擎本來就會自動算對比色，但那是對**原始淺色主題**的底色算的。
    PPT 端把底色換成深空配色後字色沒跟著變——實測象限 chip 白字掉到 1.44、
    泡泡數字 1.24，畫面上實質看不見。

    ⚠ 單靠字串替換無從得知「這段白字疊在哪個底上」，故由引擎輸出
    `data-on-fill` 標記配對關係，PPT 端據此重算。
    """

    def setUp(self):
        self.bp = _load_builder()
        self.theme = self.bp.Theme.load(THEME_PATH)

    def _contrast(self, a: str, b: str) -> float:
        def lum(h):
            h = h.lstrip("#")
            ch = []
            for i in (0, 2, 4):
                c = int(h[i:i + 2], 16) / 255
                ch.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
            return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
        la, lb = lum(a), lum(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    def test_text_on_light_fill_becomes_dark(self):
        """淺色底（轉色後的暖色）上的白字要改成深字，否則看不見。"""
        svg = ('<svg><rect fill="#F59E0B"/>'
               '<text fill="#FFFFFF" data-on-fill="#F59E0B">7</text></svg>')
        out = self.bp.recolor_svg(svg, self.theme.chart_recolor)
        fill = re.search(r'<rect fill="([^"]+)"', out).group(1)
        text = re.search(r'<text fill="([^"]+)"', out).group(1)
        self.assertGreaterEqual(
            self._contrast(text, fill), 4.5,
            f"文字 {text} 疊在 {fill} 上對比不足——轉色後沒有重算字色")

    def test_unmarked_text_follows_normal_map(self):
        """沒有 data-on-fill 的文字（軸標、標題）畫在頁面底上，照原規則轉亮字。"""
        svg = '<svg><text fill="#00094A">軸標</text></svg>'
        out = self.bp.recolor_svg(svg, self.theme.chart_recolor)
        self.assertIn("#EAF6FB", out)

    def test_engine_marks_pairing(self):
        """引擎必須輸出 data-on-fill，否則 PPT 端沒有資訊可依據。"""
        src = (REPO / "backend" / "app" / "reports" / "chart_runner.py").read_text(encoding="utf-8")
        self.assertIn("data-on-fill", src,
                      "引擎沒有標記文字與底色的配對——下游無從得知白字疊在哪")


if __name__ == "__main__":
    unittest.main()
