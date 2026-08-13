"""窄 SVG→DrawingML 轉換器：把引擎組好的每頁 SVG 轉成原生 PPTX。

## 為什麼窄

B 案把排版決定權從 PowerPoint 收回引擎——SVG 裡的文字**已經逐行斷好、絕對定位**，
這裡只負責忠實搬運。因此只支援本 skill 的元素詞彙（`<rect>`／`<text>`／`<image>`），
不支援 SVG 標準全集。詞彙外的元素一律 raise，**不靜默略過**：略過會讓版面少一塊
而沒有任何人發現，那正是本專案反覆踩過的靜默失敗。

## 詞彙＝現行組版實際畫得出來的東西（自 `deck_layout.py` 反解）

| SVG | pptx | 對應 |
|---|---|---|
| `<rect>` | `add_shape(RECTANGLE / ROUNDED_RECTANGLE)` | `deck_layout.rect()` |
| `<text>` | `add_textbox()`（一個 `<text>` ＝ 一行 ＝ 一個框） | `deck_layout.textbox()` |
| `<image>` | `add_picture()` | `chart_stack()` |

⚠ **「線」不是獨立詞彙**，是矩形的退化形（`RULE_W = 0.014in` 的細 rect）。
另立一種表示法就會有兩個落點。

## 座標系＝96 dpi

`deck_layout.py:73` 的註記寫死了這個前提（「0.008in 在 96dpi 下只有 0.77px」）。
13.333×7.5in 的投影片 ＝ 1280×720 的 SVG。

用法：`from svg_to_pptx import build; build([svg1, svg2, …]).save(out)`
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlparse

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE
from pptx.util import Emu, Inches, Pt

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# 座標系：SVG 以 px 表示，96 px = 1 in（見模組 docstring）。
DPI = 96.0
SLIDE_W_IN, SLIDE_H_IN = 13.333, 7.5

# 詞彙表。⚠ 新增元素要同步 `tests/test_svg_to_pptx_converter.py` 的 fail-loud 測試，
# 否則等於偷偷放寬詞彙。
SUPPORTED = {"rect", "text", "image"}
# 純結構性、不畫東西的標籤：略過不算違規。
STRUCTURAL = {"svg", "g", "defs", "title", "desc", "style", "metadata"}


class UnsupportedElement(ValueError):
    """SVG 用了詞彙外的元素。窄轉換器的核心約束——不猜、不略過、直接失敗。"""


def _local(tag: str) -> str:
    """去掉 namespace 前綴。"""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _px(value: str | None, default: float = 0.0) -> float:
    """SVG 長度字串 → px。只認純數字與 px；其餘單位不在詞彙內。"""
    if value is None:
        return default
    text = str(value).strip()
    if text.endswith("px"):
        text = text[:-2]
    try:
        return float(text)
    except ValueError as exc:
        raise UnsupportedElement(f"看不懂的長度值：{value!r}（詞彙只收 px）") from exc


def _emu(px: float) -> int:
    """px @96dpi → EMU。

    ⚠ 走 `Emu(round(...))` 而不是 `Inches(px/DPI)`：後者對細線（1.344px）會在
    浮點轉換時丟精度，而 `deck_layout.py:73` 明記細線消失是隨機發生、目視抓不到。
    """
    return Emu(round(px / DPI * 914400))


def _color(value: str | None) -> RGBColor | None:
    """`#RRGGBB` → RGBColor；`none`／缺值 → None（＝不填色／不描邊）。"""
    if not value or value.strip().lower() in ("none", "transparent"):
        return None
    text = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", text):
        raise UnsupportedElement(f"看不懂的顏色：{value!r}（詞彙只收 #RRGGBB）")
    return RGBColor.from_string(text.upper())


def _add_rect(slide, el: ET.Element) -> None:
    """`<rect>` → autoshape。有 `rx` 就是圓角矩形（對應 `ROUNDED_RECTANGLE`）。"""
    x, y = _px(el.get("x")), _px(el.get("y"))
    w, h = _px(el.get("width")), _px(el.get("height"))
    rx = _px(el.get("rx")) if el.get("rx") else 0.0
    shape_kind = MSO_SHAPE.ROUNDED_RECTANGLE if rx > 0 else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_kind, _emu(x), _emu(y), _emu(w), _emu(h))

    fill = _color(el.get("fill"))
    if fill is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill

    stroke = _color(el.get("stroke"))
    if stroke is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = stroke
        shape.line.width = Pt(_px(el.get("stroke-width"), 1.0) / DPI * 72)

    # 陰影一律關掉：`deck_layout.rect()` 同款（`s.shadow.inherit = False`）。
    shape.shadow.inherit = False
    if rx > 0 and w > 0:
        # SVG 的 rx 是絕對長度，pptx 的 adjustment 是相對短邊的比例。
        shape.adjustments[0] = min(0.5, rx / min(w, h) if min(w, h) else 0)


def _add_text(slide, el: ET.Element) -> None:
    """`<text>` → 一個文字框 ＝ **一行**。

    🔴 SVG 的 `y` 是**基線**，pptx 的 top 是框頂。以字級推回框頂，並關掉
    內距與自動調整，讓框的位置只由座標決定。
    ⚠ 一個 `<text>` 一個框，不合併：合併就等於把斷點交還給 PowerPoint，
    B 案整個白做。
    """
    size_px = _px(el.get("font-size"), 21.33)
    baseline_y = _px(el.get("y"))
    # 基線→框頂：約 0.8 個字高是 ascent，其餘留白由關掉的內距吸收。
    top_px = baseline_y - size_px * 0.8
    x_px = _px(el.get("x"))
    text = "".join(el.itertext())

    # 框寬給足，反正 wrap 關掉、不會換行；高度以行高估。
    box = slide.shapes.add_textbox(_emu(x_px), _emu(top_px),
                                   _emu(max(size_px * len(text) + size_px, size_px)),
                                   _emu(size_px * 1.6))
    tf = box.text_frame
    tf.word_wrap = False                    # 🔴 B 案的前提：PowerPoint 零重排自由
    tf.auto_size = MSO_AUTO_SIZE.NONE       # 字級鎖死，不讓它縮放
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0

    para = tf.paragraphs[0]
    run = para.add_run()
    run.text = text
    run.font.size = Pt(size_px / DPI * 72)
    family = el.get("font-family")
    if family:
        run.font.name = family.split(",")[0].strip().strip("'\"")
    weight = (el.get("font-weight") or "").strip()
    run.font.bold = weight in ("bold", "600", "700", "800", "900")
    fill = _color(el.get("fill"))
    if fill is not None:
        run.font.color.rgb = fill


def _add_image(slide, el: ET.Element, base_dir: Path | None) -> None:
    """`<image>` → `add_picture`。只收本機檔案，不抓網路。

    🔴 相對路徑相對於 **SVG 檔所在目錄**（`base_dir`），不是當前工作目錄。
    ⚠ 2026-08-13 映射煙霧測試的教訓：SVG 用 `file://` 絕對 URI 引圖時，
    Chromium 以 `set_content` 載入會因缺 base URL 而破圖（COM 轉圖卻正常）。
    可行的目視路徑是「SVG 存檔 ＋ 圖用相對路徑 ＋ Chromium `goto`」，
    那條路徑要成立，這裡就必須以 SVG 檔的位置解析。
    以 cwd 解析則會在 runner 從別的目錄呼叫時**靜默找不到圖**。
    """
    href = el.get(f"{{{XLINK_NS}}}href") or el.get("href")
    if not href:
        raise UnsupportedElement("<image> 缺 href")
    if href.startswith("data:"):
        raise UnsupportedElement("<image> 不收 data URI（圖檔走磁碟路徑，避免 SVG 肥大）")
    parsed = urlparse(href)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path).lstrip("/"))
    else:
        path = Path(unquote(href))
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
    if not path.is_file():
        raise UnsupportedElement(f"<image> 指向的檔案不存在：{path}")
    slide.shapes.add_picture(str(path), _emu(_px(el.get("x"))), _emu(_px(el.get("y"))),
                             _emu(_px(el.get("width"))), _emu(_px(el.get("height"))))


def _walk(slide, el: ET.Element, base_dir: Path | None) -> None:
    """深度優先走訪；詞彙外即 raise。"""
    tag = _local(el.tag)
    if tag == "rect":
        _add_rect(slide, el)
        return
    if tag == "text":
        _add_text(slide, el)
        return
    if tag == "image":
        _add_image(slide, el, base_dir)
        return
    if tag not in STRUCTURAL:
        raise UnsupportedElement(
            f"詞彙外的元素：<{tag}>。窄轉換器只收 {sorted(SUPPORTED)}——"
            f"要新增請同步更新詞彙表與 fail-loud 測試，不要在這裡放行。")
    for child in el:
        _walk(slide, child, base_dir)


def build(svg_pages: list[str], base_dirs: list[Path] | None = None) -> Presentation:
    """一頁 SVG ＝ 一張投影片。回傳 Presentation（呼叫端自己 `.save()`）。

    `base_dirs`：各頁 SVG 所在目錄，供解析 `<image>` 的相對路徑；
    不給則相對路徑以 cwd 解（僅適用測試與單檔手動呼叫）。
    """
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(SLIDE_W_IN), Inches(SLIDE_H_IN)
    blank = prs.slide_layouts[6]            # 空白版面：所有元素都由我們畫
    for index, svg in enumerate(svg_pages):
        slide = prs.slides.add_slide(blank)
        base = base_dirs[index] if base_dirs and index < len(base_dirs) else None
        _walk(slide, ET.fromstring(svg), base)
    return prs


def build_file(svg_paths: list[Path | str], out_path: Path | str) -> int:
    """讀檔版：給 runner 用。回傳頁數。相對圖檔以各 SVG 自己的目錄解析。"""
    paths = [Path(p) for p in svg_paths]
    pages = [p.read_text(encoding="utf-8") for p in paths]
    prs = build(pages, [p.resolve().parent for p in paths])
    prs.save(str(out_path))
    return len(pages)
