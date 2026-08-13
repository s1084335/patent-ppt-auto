"""重排「機會四象限」這類 chip 版面的圖表，讓圖內字級可以放大。

⚠ 這支腳本會改變圖表**版面**，屬於重畫。預設流程禁止使用，
   必須使用者明確授權「這幾張可以重排」才可執行，且只能對被授權的圖執行。

為什麼需要：chip 是「固定寬度的色塊 ＋ 獨立定位的文字」，字級一放大文字就衝出色塊、
蓋到隔壁 chip，fit_render_charts.py 因此只能把它們卡在原字級。

關鍵幾何：投影片上的圖內字級 ≈ 圖內字級 × (可用高度 × 72) ÷ SVG 高度。
**只跟 SVG 的高度有關，跟寬度無關**（寬度會被等比縮放吃掉）。所以重排的目標是壓低高度：
chip 改一行一個、象限標題與建議語併成一行。

不變的東西：chip 文字、顏色、tooltip、象限歸屬、象限標題與建議語、軸標、頁尾——全部照抄。

用法：python rebuild_chip_chart.py <svg_dir> <名稱1> [名稱2 ...]   （就地覆寫，請先備份原圖）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

F = 20                    # 新字級
W = 1260                  # 圖寬（只影響長寬比，不影響投影片上的字級）
ML_, MR_ = 64, 40
GAP = 14
CHIP_H, CHIP_GAP = 32, 6
LINE = 26


def tw(text: str, size: int = F) -> float:
    """估算文字寬度：CJK 約 1.0 em、半形約 0.55 em，乘 1.02 安全係數。"""
    return sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in text) * size * 1.02


def esc(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def parse(svg: str) -> dict:
    """以**結構**（屬性、位置、順序）辨識元素，不比對任何特定字串——換一批資料仍適用。"""
    d: dict = {}
    d["title"] = re.search(r'data-role="chart-title"[^>]*>([^<]*)<', svg).group(1)
    # (attrs, y, text)：保留 y 座標，用位置決定角色
    items = [(a, float(re.search(r'\sy="([\d.]+)"', a).group(1)), t)
             for a, t in re.findall(r'<text([^>]*)>([^<]*)</text>', svg)
             if re.search(r'\sy="([\d.]+)"', a) and "rotate" not in a]
    head = sorted([i for i in items if i[1] < 120], key=lambda i: i[1])
    d["note"] = next((t for a, _, t in head if "#9CA3AF" in a), "")
    d["legend_head"] = next((t for a, _, t in head
                             if "font-weight" in a and "chart-title" not in a), "")
    d["legend"] = [(m.group(1), m.group(2)) for m in re.finditer(
        r'<rect x="[\d.]+" y="[\d.]+" width="12" height="12" fill="(#[0-9A-Fa-f]{6})" rx="2"/>\s*'
        r'<text[^>]*>([^<]*)</text>', svg)]
    tail = sorted([i for i in items if i[1] > 120], key=lambda i: i[1])
    d["axis_x"] = next((t for a, _, t in reversed(tail) if 'text-anchor="middle"' in a), "")
    rot = re.search(r'<text([^>]*transform="rotate[^>]*)>([^<]*)</text>', svg)
    d["axis_y"] = rot.group(2) if rot else ""
    d["footer"] = next((t for a, _, t in reversed(tail) if "#9CA3AF" in a), "")

    boxes = []
    for m in re.finditer(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="[\d.]+" height="[\d.]+" rx="10" '
            r'fill="(#[0-9A-Fa-f]{6})" fill-opacity="([\d.]+)" stroke="[^"]*"/>\s*'
            r'<text[^>]*fill="(#[0-9A-Fa-f]{6})"[^>]*>([^<]*)</text>\s*'
            r'<text[^>]*>([^<]*)</text>', svg):
        x, y, fill, op, lc, label, sugg = m.groups()
        boxes.append({"x": float(x), "y": float(y), "fill": fill, "op": op, "color": lc,
                      "label": label, "sugg": sugg, "chips": [], "empty": None})
    d["boxes"] = sorted(boxes, key=lambda b: (b["y"], b["x"]))

    for m in re.finditer(
            r'<rect class="chip"([^>]*)x="([\d.]+)" y="([\d.]+)"[^>]*fill="(#[0-9A-Fa-f]{6})">\s*'
            r'<title>([^<]*)</title>\s*</rect>\s*<text[^>]*>([^<]*)</text>', svg):
        attrs, cx, cy, fill, tip, label = m.groups()
        cell = re.search(r'data-cell="([^"]*)"', attrs)
        topic = re.search(r'data-topic="([^"]*)"', attrs)
        box = min(d["boxes"], key=lambda b: abs(b["x"] - float(cx)) + abs(b["y"] - float(cy)) * .6)
        box["chips"].append({"label": label, "fill": fill, "tip": tip,
                             "cell": cell.group(1) if cell else "",
                             "topic": topic.group(1) if topic else ""})

    for a, _y, t in items:
        if 'font-style="italic"' in a:
            mm = re.search(r'x="([\d.]+)" y="([\d.]+)"', a)
            if mm:
                bx, by = float(mm.group(1)), float(mm.group(2))
                box = min(d["boxes"],
                          key=lambda b: abs(b["x"] - bx + 12) + abs(b["y"] - by) * .6)
                box["empty"] = t
    return d


def build(d: dict) -> str:
    box_w = (W - ML_ - MR_ - GAP) / 2
    n_max = max(max(len(b["chips"]), 1 if b["empty"] else 0) for b in d["boxes"])
    box_h = 40 + n_max * CHIP_H + (n_max - 1) * CHIP_GAP + 12

    y_title, y_note, y_leg = 28, 28 + LINE, 28 + 2 * LINE + 4
    leg_rows, cur = [[]], ML_ + tw(d["legend_head"]) + 22
    for color, text in d["legend"]:
        need = 16 + 8 + tw(text) + 26
        if cur + need > W - MR_:
            leg_rows.append([])
            cur = ML_
        leg_rows[-1].append((cur, color, text))
        cur += need
    y_top = y_leg + (len(leg_rows) - 1) * LINE + 18

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{{H}}" '
         f'viewBox="0 0 {W} {{H}}" font-family="Segoe UI, sans-serif">',
         '<rect width="100%" height="100%" fill="white"/>',
         f'<text data-role="chart-title" x="{ML_}" y="{y_title}" font-size="{F}" '
         f'font-weight="700" fill="#00094A">{esc(d["title"])}</text>',
         f'<text x="{ML_}" y="{y_note}" font-size="{F}" fill="#9CA3AF">{esc(d["note"])}</text>',
         f'<text x="{ML_}" y="{y_leg}" font-size="{F}" font-weight="600" '
         f'fill="#00094A">{esc(d["legend_head"])}</text>']
    for ri, row in enumerate(leg_rows):
        ry = y_leg + ri * LINE
        for x, color, text in row:
            o.append(f'<rect x="{x:.0f}" y="{ry - 13:.0f}" width="16" height="16" '
                     f'fill="{color}" rx="3"/>')
            o.append(f'<text x="{x + 24:.0f}" y="{ry:.0f}" font-size="{F}" '
                     f'fill="#00094A">{esc(text)}</text>')

    for i, b in enumerate(d["boxes"]):
        bx = ML_ + (i % 2) * (box_w + GAP)
        by = y_top + (i // 2) * (box_h + GAP)
        o.append(f'<rect x="{bx:.0f}" y="{by:.0f}" width="{box_w:.0f}" height="{box_h:.0f}" '
                 f'rx="10" fill="{b["fill"]}" fill-opacity="{b["op"]}" stroke="#E5E7EB"/>')
        o.append(f'<text x="{bx + 14:.0f}" y="{by + 28:.0f}" font-size="{F}" font-weight="600" '
                 f'fill="{b["color"]}">{esc(b["label"])}'
                 f'<tspan font-weight="400">　{esc(b["sugg"])}</tspan></text>')
        cy = by + 40
        for c in b["chips"]:
            o.append(f'<rect class="chip" data-cell="{c["cell"]}" data-topic="{c["topic"]}" '
                     f'x="{bx + 14:.0f}" y="{cy:.0f}" width="{tw(c["label"]) + 20:.0f}" '
                     f'height="{CHIP_H}" rx="6" fill="{c["fill"]}">'
                     f'<title>{esc(c["tip"])}</title></rect>')
            o.append(f'<text x="{bx + 24:.0f}" y="{cy + CHIP_H / 2 + 7:.0f}" font-size="{F}" '
                     f'fill="#FFFFFF" data-on-fill="{c["fill"]}">{esc(c["label"])}</text>')
            cy += CHIP_H + CHIP_GAP
        if b["empty"]:
            o.append(f'<text x="{bx + 14:.0f}" y="{by + 62:.0f}" font-size="{F}" '
                     f'fill="#9CA3AF" font-style="italic">{esc(b["empty"])}</text>')

    bottom = y_top + 2 * box_h + GAP
    y_ax, mid = bottom + 30, (y_top + bottom) / 2
    y_ft = y_ax + 28
    H = int(y_ft + 14)
    o.append(f'<text x="{W / 2:.0f}" y="{y_ax}" text-anchor="middle" font-size="{F}" '
             f'fill="#00094A">{esc(d["axis_x"])}</text>')
    o.append(f'<text x="26" y="{mid:.0f}" text-anchor="middle" font-size="{F}" fill="#00094A" '
             f'transform="rotate(-90,26,{mid:.0f})">{esc(d["axis_y"])}</text>')
    o.append(f'<text x="{ML_}" y="{y_ft}" font-size="{F}" fill="#9CA3AF">{esc(d["footer"])}</text>')
    o.append("</svg>")
    return "\n".join(o).replace("{H}", str(H))


def main() -> int:
    d_dir = Path(sys.argv[1])
    for name in sys.argv[2:]:
        src = d_dir / f"{name}.svg"
        out = build(parse(src.read_text(encoding="utf-8")))
        src.write_text(out, encoding="utf-8")
        h = re.search(r'height="(\d+)"', out)
        print(f"{name}: {W}x{h.group(1) if h else '?'}  字級 {F}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
