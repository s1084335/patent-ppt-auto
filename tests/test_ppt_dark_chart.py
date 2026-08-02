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
import tempfile
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


class EncodingNoteSingleSourceTests(unittest.TestCase):
    """圖表編碼說明的唯一來源在**畫圖端**（2026-07-31 引擎批 E3）。

    ⚠ 這份說明原本寫在組版端，與引擎各自演進，實測三張對不上：
    `annual_trend` 是折線卻寫「條長」、`application_growth` 縱軸是年增率 %
    卻寫「件數」、`lifecycle` 橫軸是申請人家數卻寫「申請年」。
    只有畫圖的那一端知道自己畫了什麼。
    """

    def test_engine_is_the_source(self):
        src = CHART_RUNNER.read_text(encoding="utf-8")
        self.assertIn("CHART_ENCODING_NOTES", src, "引擎沒有輸出編碼說明")
        self.assertIn('"encoding_notes"', src, "編碼說明沒有進 table_display 傳給下游")

    def test_builder_prefers_engine(self):
        bp = _load_builder()
        ctx = {"report_data": {"table_display": {"encoding_notes": {"foo": "引擎版說明"}}}}
        spec = bp.PageSpec(page=1, kind="chart_hero", title="x", topic="x", report_keys=("foo",))
        self.assertEqual(bp._encoding_note(spec, ctx), "引擎版說明")

    def test_builder_falls_back_for_old_versions(self):
        """舊報表版本沒有 encoding_notes，仍要有說明可用（不得空白）。"""
        bp = _load_builder()
        spec = bp.PageSpec(page=1, kind="chart_hero", title="x", topic="x",
                           report_keys=("application_trend",))
        self.assertTrue(bp._encoding_note(spec, {"report_data": {}}))


class ChartTextSizeOnSlideTests(unittest.TestCase):
    """P-2：圖表文字的判準是**縮放後的 pt**，不是 SVG 裡寫的 px。

    🔴 2026-08-03 實測：排名圖 SVG 980×724px（10.21×7.54 in）塞進 chart_hero
    的 8.9×4.32 in 圖框，被高度卡到 **0.573 倍**——13px 的公司名到投影片上
    只剩 **5.6pt**，而組版原生文字的下限是 12pt。差超過一倍。

    ⚠ 根因不是「字寫太小」，是**圖畫太高**：12 列 × 50px 讓圖有 7.54in。
    07-31 那次「20 列縮到 0.37 倍」我只解了列數，沒解這件事，所以 12 列還是同一個病。

    修法：SVG 以**最終顯示尺寸**設計（畫布比例貼齊圖框），不是畫大圖再縮。
    """

    MIN_SLIDE_PT = 12.0   # 與 theme.font.min_pt 同口徑

    def setUp(self):
        self.theme = json.loads(THEME_PATH.read_text(encoding="utf-8"))
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    def _render_ranking(self, tmp: Path, rows: int = 12):
        from backend.app.reports import chart_runner as cr
        data = [{"applicant_display_name": f"公司名稱{i}", "patent_count": 20 - i,
                 "recent_assignee_count": 0} for i in range(rows)]
        path = tmp / "rank.svg"
        cr.render_segmented_bar_chart(path, "主要申請人排名", data, "applicant_display_name",
                                      total_key="patent_count", segment_key="recent_assignee_count",
                                      limit=rows, segment_label="有最新受讓人")
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _canvas(svg: str) -> tuple[float, float]:
        m = re.search(r'width="(\d+)" height="(\d+)"', svg)
        return int(m.group(1)) / 96, int(m.group(2)) / 96

    def _slide_pt(self, svg: str, px: float, box: str = "chart_hero") -> float:
        g = self.theme["geometry"][box]
        w_in, h_in = self._canvas(svg)
        scale = min(g["image_width_in"] / w_in, g["image_height_in"] / h_in)
        return px * 72 / 96 * scale

    def test_row_labels_readable_after_scaling(self):
        with tempfile.TemporaryDirectory() as tmp:
            svg = self._render_ranking(Path(tmp))
        sizes = [int(s) for s in re.findall(r'text-anchor="end" font-size="(\d+)"', svg)]
        self.assertTrue(sizes, "找不到列標籤")
        worst = min(sizes)
        actual = self._slide_pt(svg, worst)
        self.assertGreaterEqual(
            actual, self.MIN_SLIDE_PT,
            f"列標籤 {worst}px 縮放後只有 {actual:.1f}pt（下限 {self.MIN_SLIDE_PT}pt）")

    def test_canvas_is_not_taller_than_the_frame_allows(self):
        """⚠ 畫布比例要貼齊圖框，否則高度先滿、整張被壓小。"""
        with tempfile.TemporaryDirectory() as tmp:
            svg = self._render_ranking(Path(tmp))
        g = self.theme["geometry"]["chart_hero"]
        w_in, h_in = self._canvas(svg)
        scale = min(g["image_width_in"] / w_in, g["image_height_in"] / h_in)
        self.assertGreaterEqual(scale, 0.85, f"縮放倍率 {scale:.3f} 過小——畫布比圖框高太多")

    def test_value_labels_also_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            svg = self._render_ranking(Path(tmp))
        sizes = [int(s) for s in re.findall(r'font-size="(\d+)"', svg)]
        body = [s for s in sizes if s < 20]          # 排除標題那種大字
        self.assertGreaterEqual(self._slide_pt(svg, min(body)), self.MIN_SLIDE_PT)


class ChartTitleStrippedOnSlideTests(unittest.TestCase):
    """F-8：頁標題與圖表內建標題講同一件事，上下兩行重複。

    🔴 實機 p7／p8／p9／p10／p11／p14／p15／p16／p17 九頁皆然：
    上面是 narrative 的 headline，下面是 SVG 自己畫的「IPC 主分類分布 - Level 4」。
    ⚠ 不能直接砍 SVG 標題——網頁報表頁也讀同一份 SVG，那裡需要標題。
    故引擎標上 `data-role="chart-title"`，**只有組版端**在放上投影片時移除。
    """

    def setUp(self):
        self.bp = _load_builder()

    def test_engine_marks_its_chart_titles(self):
        source = CHART_RUNNER.read_text(encoding="utf-8")
        self.assertIn('data-role="chart-title"', source,
                      "引擎沒有標記圖表標題，組版端無從辨識該移除哪一行")

    def test_builder_strips_marked_title(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
               '<text data-role="chart-title" x="16" y="26" font-size="24">IPC 主分類分布 - Level 4</text>'
               '<text x="16" y="60" font-size="11">A63B</text></svg>')
        out = self.bp.strip_chart_title(svg)
        self.assertNotIn("IPC 主分類分布", out)
        self.assertIn("A63B", out, "只該移除標題，其餘內容不得動到")

    def test_unmarked_text_is_kept(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg"><text x="1" y="2">保留我</text></svg>')
        self.assertIn("保留我", self.bp.strip_chart_title(svg))

    def test_idempotent(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg">'
               '<text data-role="chart-title" x="1" y="2">標題</text></svg>')
        once = self.bp.strip_chart_title(svg)
        self.assertEqual(self.bp.strip_chart_title(once), once)


def _luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = []
    for offset in (0, 2, 4):
        c = int(value[offset:offset + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


class RankingScaleOnDarkTests(unittest.TestCase):
    """W-2 硬約束：排名色階映射到深底後，**最淺一階也要 ≥3.0**。

    🔴 使用者選「依數值連續深淺」。⚠ 但 F-2 就是淺色跟深空背景糊在一起造成的
    （`CBD5E1`→`274A66` 對背景實測 1.72，長條等於不存在）。色階必須從
    「最淺一階 ≥3.0」這個下限往上推，不能從主色往下淡——否則只是再做一次 F-2。

    ⚠ 量的是漸層的**最亮端**（`bg_start`）：那是對比最差的位置，
    在最暗端合格不代表整頁合格（批 2 的邊框就是這樣跌破的）。
    """

    def setUp(self):
        self.theme = json.loads(THEME_PATH.read_text(encoding="utf-8"))
        self.bg = self.theme["color"]["bg_start"]

    def _scale(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from backend.app.reports.chart_runner import RANKING_BAR_SCALE
        return RANKING_BAR_SCALE

    def test_every_step_has_a_dark_theme_mapping(self):
        mapping = {k.upper(): v for k, v in self.theme["chart_recolor"]["map"].items()}
        for step in self._scale():
            self.assertIn(step.lstrip("#").upper(), mapping,
                          f"排名色階 {step} 沒有深底對照——深色頁上會維持白底用的藍")

    def test_every_step_is_visible_on_the_brightest_background(self):
        mapping = {k.upper(): v for k, v in self.theme["chart_recolor"]["map"].items()}
        for step in self._scale():
            target = mapping[step.lstrip("#").upper()]
            self.assertGreaterEqual(
                _contrast(target, self.bg), 3.0,
                f"{step} → {target} 對背景 {self.bg} 僅 {_contrast(target, self.bg):.2f}")

    def test_dark_steps_stay_monotonic(self):
        """深底階也要單調——否則「件數多」在兩種底色上指向不同方向。"""
        mapping = {k.upper(): v for k, v in self.theme["chart_recolor"]["map"].items()}
        lums = [_luminance(mapping[s.lstrip("#").upper()]) for s in self._scale()]
        self.assertEqual(lums, sorted(lums, reverse=True),
                         f"深底色階亮度非單調遞減：{[round(x, 3) for x in lums]}")


if __name__ == "__main__":
    unittest.main()
