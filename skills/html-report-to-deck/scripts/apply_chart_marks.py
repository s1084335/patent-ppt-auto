"""跨度圖宣告式標記（design §7.8b）：CLI 宣告、引擎照畫——不破「CLI 不改圖」。

content.json 頁 spec 的 `chart_marks`：
    {"<chart>": {"highlight": ["名稱", …], "marker": {"year": 2021, "label": "…"}}}

- highlight＝該列標籤加粗變色＋該列跨度條描邊（哪幾條值得看＝CLI 判斷）
- marker＝該年畫垂直虛線＋標籤（世代分界線畫在哪＝CLI 判斷）
- 🔴 宣告接不上資料（名稱不在圖上、年份不在軸上）→ **非零退出**走修稿輪
  ——靜默略過會讓 CLI 以為標了，目視時又看不出「少了什麼」（缺席型失敗）。

冪等：首次執行把原圖備份到 `charts_orig_marks/`，之後每輪都**從備份重套**
——目視迴圈會反覆執行本步，疊加會越畫越髒。

用法：python apply_chart_marks.py <work_dir>
輸出：套用時印 `MARKS_APPLIED <chart>`（runner 據此決定要不要重跑 fit）。
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

#: 標記色。⚠ 沿引擎圖表色票的紅（#DC2626 是資料色），標記用更深的酒紅
#: 與資料區隔；這裡是「標記樣式的唯一定義處」，CLI 宣告裡沒有任何樣式欄位。
MARK_COLOR = "#B0123C"


class MarkError(ValueError):
    """宣告接不上圖上的資料。"""


def _apply_to_svg(svg: str, marks: dict) -> str:
    """對單張跨度圖 SVG 套 highlight 與 marker；接不上即 raise。"""
    for name in marks.get("highlight") or []:
        label_re = re.compile(
            r'<text data-role="row-label"([^>]*)>' + re.escape(str(name)) + r"</text>")
        m = label_re.search(svg)
        if m is None:
            raise MarkError(f"highlight 名稱「{name}」不在圖上的列標籤中")
        # 列標籤：加粗、變色、蓋 data-mark（供測試與日後程式化檢查辨識）
        attrs = m.group(1)
        attrs = re.sub(r' fill="[^"]*"', "", attrs)
        svg = svg[:m.start()] + (
            f'<text data-role="row-label" data-mark="highlight"{attrs}'
            f' font-weight="700" fill="{MARK_COLOR}">{name}</text>') + svg[m.end():]
        # 同列的跨度條：以 y 帶對位（列標籤 y 落在條的 y..y+height 內）
        label_y = float(re.search(r'y="([\d.]+)"', m.group(0)).group(1))
        def _stroke_bar(bm: re.Match) -> str:
            tag = bm.group(0)
            y = float(re.search(r'y="([\d.]+)"', tag).group(1))
            h = float(re.search(r'height="([\d.]+)"', tag).group(1))
            if y <= label_y <= y + h and 'data-mark=' not in tag:
                return tag.replace('<rect ', '<rect data-mark="highlight" ', 1) \
                          .replace('>', f' stroke="{MARK_COLOR}" stroke-width="2">', 1)
            return tag
        svg = re.sub(r'<rect data-role="span-bar"[^>]*>', _stroke_bar, svg)

    marker = marks.get("marker")
    if marker:
        year = str(marker.get("year") or "")
        label = str(marker.get("label") or "")
        ym = re.search(
            r'<text x="([\d.]+)" y="[\d.]+" font-size="[^"]*" text-anchor="middle"'
            r'[^>]*>' + re.escape(year) + r"</text>", svg)
        if ym is None:
            raise MarkError(f"marker 年份 {year} 不在圖的年軸上")
        x = float(ym.group(1))
        height = float(re.search(r'viewBox="0 0 [\d.]+ ([\d.]+)"', svg).group(1))
        overlay = (
            f'<line data-mark="marker" x1="{x}" y1="90" x2="{x}" y2="{height - 30}"'
            f' stroke="{MARK_COLOR}" stroke-width="1.5" stroke-dasharray="6 4"/>'
            + (f'<text data-mark="marker-label" x="{x + 6}" y="104" font-size="13"'
               f' fill="{MARK_COLOR}">{label}</text>' if label else ""))
        svg = svg.replace("</svg>", overlay + "</svg>")
    return svg


def main() -> int:
    work = Path(sys.argv[1])
    content = json.loads((work / "content.json").read_text(encoding="utf-8"))
    charts_dir = work / "charts"
    pristine_dir = work / "charts_orig_marks"

    declared: dict[str, dict] = {}
    for page in content.get("pages") or []:
        for chart, marks in (page.get("chart_marks") or {}).items():
            declared[str(chart)] = marks
    if not declared:
        return 0                              # 沒宣告＝無事（多數頁不標）

    failures: list[str] = []
    applied: list[str] = []
    for chart, marks in declared.items():
        src = charts_dir / f"{chart}.svg"
        if not src.is_file():
            failures.append(f"chart_marks 指向不存在的圖：{chart}")
            continue
        pristine_dir.mkdir(exist_ok=True)
        pristine = pristine_dir / src.name
        if not pristine.is_file():
            shutil.copyfile(src, pristine)    # 首輪備份；之後每輪從備份重套（冪等）
        try:
            marked = _apply_to_svg(pristine.read_text(encoding="utf-8"), marks)
        except MarkError as exc:
            failures.append(f"{chart}：{exc}")
            continue
        src.write_text(marked, encoding="utf-8")
        applied.append(chart)
        print(f"MARKS_APPLIED {chart}")

    if failures:
        print("\n".join("✗ " + f for f in failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
