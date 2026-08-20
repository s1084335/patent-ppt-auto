"""SVG 畫布：`deck_layout` 的第二個輸出端（B 案）。

## 為什麼是「第二個輸出端」而不是改寫 deck_layout

頁型的幾何計算（哪個區塊多高、圖放哪、行長多少）是這個 skill 累積最多實測教訓
的部分，一行都不該動。B 案要換的只有**最後畫出去那一步**：原本呼叫 python-pptx
的 `add_shape`／`add_textbox`／`add_picture`，改成產 SVG 元素。

因此 `deck_layout.rect()`／`textbox()`／`picture()` 三個原語改為依畫布型別分派，
頁型函式（`slide_cover`／`slide_chart`／…）完全不知道自己在畫哪一種。

## 座標系

對外一律用**英吋**（與 `deck_layout` 全檔一致）；本檔在輸出時乘 `DPI` 轉成 px。
96 dpi 的依據見 `deck_layout.py` RULE_W 那條註記。

## 一個 `<text>` ＝ 一行 ＝ 一種樣式

詞彙不含 `<tspan>`（見 `SVG_VOCABULARY`）。同一行內若有多種樣式（例如
「A　主題名」＋標籤），就輸出多個 `<text>`，x 依前段實際寬度累加
——寬度用 `units()` 算，與斷行同一套係數。
"""
from __future__ import annotations

from html import escape
from pathlib import Path

DPI = 96.0

#: 🔴 頁面 SVG 的**元素詞彙邊界**（唯一定義處；2026-08-14 使用者裁決自窄轉換器
#: 獨立出來）。`test_svg_vocabulary_guard` 掃全部頁型的實際輸出鎖住它。
#:
#: 為什麼要有邊界：這份詞彙是「頁面 SVG 隨時可轉原生 PPTX」的前提——
#: 窄轉換器（svg_to_pptx，已封存於 git 歷史，復活指標見 tasks 2.1b）只吃這些。
#: 邊界一鬆，期權就默默失效：圖形文法的箭頭用字符（→／vs）而非多邊形、
#: 分隔線用細 rect 而非 <line>，都是這條邊界在起作用。
#: ⚠ 擴詞彙＝同時擴轉換器的承接能力（先復活它再說），不得只在這裡放行。
SVG_VOCABULARY = frozenset({"svg", "rect", "text", "image"})

# 行盒頂 → 基線的比例（乘上「字級 × 該行的行高倍率」）。
#
# ⚠ **量出來的，不是猜的**。SVG 的 y 是基線、pptx 的 top 是框頂，兩者差一個
#   ascent＋行內留白。量法：A（現行 pptx 路徑）與 B（本檔）各產一份 → COM 轉同
#   一批圖 → 逐頁比像素差 → 掃描取總差最小者。
#
#   第一次（2026-08-13，Microsoft JhengHei ＋ 固定倍率 1.40）：
#     0.50→291880  0.55→275694  0.60→256289  **0.65→189651**  0.68→228784
#     0.72→241013  0.75→260433  0.80→258047  0.85→283007  0.90→299534
#   換 Noto Sans TC ＋兩段式行高後**重掃**（tasks 2.2b-2）：
#     0.55→388353  0.60→369329  **0.65→297503**  0.70→305788  0.75→349967
#   ⚠ 兩次都是 0.65 且兩側都上升——真極小值，不是掃描邊界。
#
# 它與字型學吻合，不是任意擬合：0.65 × 行高倍率(約 1.40) = 0.91，即基線距框頂
# 約 0.91 em，落在中文字型 ascent 的常見範圍（0.88–0.92 em）。
# 🔴 **換字型要重量**——ascent 比例隨字型而變。
BASELINE_RATIO = 0.65


def _hex(color) -> str:
    """RGBColor → `#RRGGBB`。已經是字串就原樣用。"""
    if color is None:
        return "none"
    text = str(color)
    return text if text.startswith("#") else f"#{text}"


class SvgCanvas:
    """一張投影片。收英吋座標，吐 SVG 字串。

    `font`／`ls_render`／`unit_width` 由 `deck_layout` 注入——那裡是字型與行高的
    唯一定義處，這裡不另存一份（存了就會漂移）。
    """

    def __init__(self, w_in: float, h_in: float, *, font: str,
                 ls_first: float, ls_next: float, unit_width, wrap_lines,
                 baseline_ratio: float | None = None) -> None:
        self.w_in, self.h_in = w_in, h_in
        self.font = font
        self.ls_first, self.ls_next = ls_first, ls_next
        self.baseline_ratio = (BASELINE_RATIO if baseline_ratio is None
                               else baseline_ratio)
        self._units = unit_width          # deck_layout.units
        self._wrap = wrap_lines           # deck_layout.wrap_lines
        self.parts: list[str] = []

    # ── 三個原語 ──────────────────────────────────────────────
    def rect(self, x, y, w, h, *, fill=None, line=None, radius=None) -> None:
        """radius 是相對短邊的比例（同 pptx adjustment），輸出時換成 SVG 的絕對 rx。"""
        attrs = [f'x="{x * DPI:.3f}"', f'y="{y * DPI:.3f}"',
                 f'width="{w * DPI:.3f}"', f'height="{h * DPI:.3f}"',
                 f'fill="{_hex(fill)}"']
        if radius:
            attrs.append(f'rx="{radius * min(w, h) * DPI:.3f}"')
        if line is not None:
            attrs += [f'stroke="{_hex(line)}"', 'stroke-width="1"']
        self.parts.append(f"  <rect {' '.join(attrs)}/>")

    def picture(self, path, x, y, w, h) -> None:
        """圖檔以**相對路徑**寫入——Chromium 用 `goto` 載入 SVG 檔時才抓得到。

        ⚠ 2026-08-13 實測：用 `file://` 絕對 URI ＋ `set_content` 會破圖
        （跨來源被擋），但 COM 轉圖卻正常——目視因此看到假警報。
        """
        href = escape(str(path), quote=True)
        self.parts.append(
            f'  <image x="{x * DPI:.3f}" y="{y * DPI:.3f}" '
            f'width="{w * DPI:.3f}" height="{h * DPI:.3f}" xlink:href="{href}"/>')

    def text_block(self, x, y, w, h, blocks, *, anchor_middle: bool = False,
                   space_after: float = 6.0, default_size_pt: float = 16.0,
                   default_color=None) -> None:
        """把 `deck_layout.textbox()` 的 blocks 展成逐行 `<text>`。

        blocks = [(txt, opt)]；txt 可為字串或 [(片段, 片段opt)]。
        opt：size(Pt)／bold／color／align／space_after／space_before。
        """
        rendered = self._layout_blocks(blocks, w, space_after, default_size_pt,
                                       default_color)
        total_h = sum(item["height"] for item in rendered)
        cursor = y + (h - total_h) / 2 if anchor_middle else y

        for item in rendered:
            cursor += item["space_before"]
            for index, line in enumerate(item["lines"]):
                size_pt = line["size_pt"]
                # 🔴 行高是**兩段式**：首行 ls_first、後續 ls_next
                # （見 deck_layout.lines_height_pt 的實測依據）。
                # SVG 的 y 是基線，落在行盒內 BASELINE_RATIO 處。
                ratio = self.ls_first if index == 0 else self.ls_next
                baseline = cursor + (size_pt * ratio * self.baseline_ratio) / 72
                self._emit_line(line, x, w, baseline)
                cursor += size_pt * ratio / 72
            cursor += item["space_after"]

    # ── 內部 ──────────────────────────────────────────────────
    def _block_height(self, lines: int, size_pt: float) -> float:
        """n 行的高度（pt）。⚠ 與 `deck_layout.lines_height_pt` 是同一個模型
        ——那裡是唯一定義處，這裡因注入的是兩個常數而重算一次相同公式；
        兩者若不一致，估高與實際繪製就會分家。
        """
        if lines <= 0:
            return 0.0
        return size_pt * (self.ls_first + (lines - 1) * self.ls_next)

    def _layout_blocks(self, blocks, w_in, space_after, default_size_pt, default_color):
        """段落 → 行；每行帶其樣式片段。回傳含高度資訊的結構。"""
        out = []
        for txt, opt in blocks:
            size_pt = _pt(opt.get("size"), default_size_pt)
            segments = txt if isinstance(txt, list) else [(txt, {})]
            # 攤平成 (字元, 樣式) 序列，切行時樣式跟著走。
            chars: list[tuple[str, dict]] = []
            for seg in segments:
                seg_txt, seg_opt = seg if isinstance(seg, tuple) else (seg, {})
                merged = {**opt, **seg_opt}
                for char in str(seg_txt):
                    chars.append((char, merged))
            plain = "".join(c for c, _ in chars)
            wrapped = self._wrap(plain, w_in, int(round(size_pt)))

            lines, cursor = [], 0
            for text_line in wrapped:
                span = chars[cursor:cursor + len(text_line)]
                cursor += len(text_line)
                lines.append({
                    "size_pt": size_pt,
                    "align": str(opt.get("align", "")),
                    "runs": _merge_runs(span, default_color),
                })
            out.append({
                "lines": lines,
                "height": self._block_height(len(lines), size_pt) / 72
                          + _pt(opt.get("space_after"), space_after) / 72
                          + _pt(opt.get("space_before"), 0) / 72,
                "space_after": _pt(opt.get("space_after"), space_after) / 72,
                "space_before": _pt(opt.get("space_before"), 0) / 72,
            })
        return out

    def _emit_line(self, line, x_in, w_in, baseline_in) -> None:
        """一行可能有多段樣式 → 多個 `<text>`，x 依實際字寬累加。"""
        size_pt = line["size_pt"]
        # 1 個字寬單位 ＝ size * 1.06 pt（與 `_per_line`／`units` 同一套係數）
        unit_in = size_pt * 1.06 / 72
        total_units = sum(self._units(run["text"]) for run in line["runs"])
        if "CENTER" in line["align"]:
            cursor = x_in + (w_in - total_units * unit_in) / 2
        elif "RIGHT" in line["align"]:
            cursor = x_in + w_in - total_units * unit_in
        else:
            cursor = x_in

        for run in line["runs"]:
            if not run["text"]:
                continue
            attrs = [f'x="{cursor * DPI:.3f}"', f'y="{baseline_in * DPI:.3f}"',
                     f'font-size="{size_pt / 72 * DPI:.2f}"',
                     f'font-family="{escape(self.font, quote=True)}"',
                     f'fill="{_hex(run["color"])}"']
            if run["bold"]:
                attrs.append('font-weight="bold"')
            self.parts.append(
                f"  <text {' '.join(attrs)}>{escape(run['text'])}</text>")
            cursor += self._units(run["text"]) * unit_in

    def to_svg(self) -> str:
        w_px, h_px = self.w_in * DPI, self.h_in * DPI
        body = "\n".join(self.parts)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'xmlns:xlink="http://www.w3.org/1999/xlink" '
                f'width="{w_px:.0f}" height="{h_px:.0f}" '
                f'viewBox="0 0 {w_px:.0f} {h_px:.0f}">\n{body}\n</svg>\n')

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.write_text(self.to_svg(), encoding="utf-8")
        return path


def _pt(value, default) -> float:
    """接受 pptx 的 Pt 物件或純數字，一律回 pt 數值。"""
    if value is None:
        return float(default.pt) if hasattr(default, "pt") else float(default)
    return float(value.pt) if hasattr(value, "pt") else float(value)


def _merge_runs(chars: list[tuple[str, dict]], default_color) -> list[dict]:
    """相鄰同樣式的字元併成一個 run，減少 `<text>` 數量。"""
    runs: list[dict] = []
    for char, opt in chars:
        style = (bool(opt.get("bold", False)), _hex(opt.get("color", default_color)))
        if runs and runs[-1]["_style"] == style:
            runs[-1]["text"] += char
        else:
            runs.append({"text": char, "bold": style[0], "color": style[1],
                         "_style": style})
    return runs
