"""SVG 圖表 → 高解析 PNG，並逐圖找出「不撞版的最大字級」。

為什麼要逐圖找：報表圖表的標籤欄寬、chip 背景寬、圖例間距，都是用原字級（通常 15.1）
算好寫死的。全域放大字級會讓長條圖的類別標籤蓋到長條、chip 文字衝出色塊——**而且只會在
少數幾張圖上發生**，肉眼抽看不一定抓得到。

做法：先在原字級記錄「本來就存在」的元素重疊組合，再由大到小試候選字級，
      只要出現原本沒有的**新**重疊就往下退一級。容忍值 3px，因為 getBBox 含行距留白，
      兩行文字貼邊會被誤判成重疊。

⚠ 只改 font-size，座標與數值一律不動——圖表資料完全不變。
⚠ 已重排過、字級本來就 ≥18 的圖（見 rebuild_chip_chart.py）不再加碼，直接沿用。

用法：python fit_render_charts.py <svg_dir> <png_dir> [候選字級,逗號分隔]
輸出：<png_dir>/*.png 與 <png_dir>/font_choice.json（每張圖採用的圖內字級）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 瀏覽器環境走 browser_env（唯一定義處）——原本這三行在本檔與 shoot_pages
# 各寫一份，改一處不會同步另一處。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_env import ensure_playwright  # noqa: E402

ensure_playwright()

SCALE = 3          # 截圖倍率：投影片放大後仍要清楚
BASE_FONT = 15.1   # 報表圖表的原字級（其餘字級依同一倍率縮放）

# ⚠ 容忍值要分開：
#   text↔text 用 3px——getBBox 含行距留白，兩行貼邊不是真的撞字。
#   text↔rect 用 1px——文字碰到長條／色塊是**看得出來的**瑕疵，不能比照放行。
#   （曾因為一律用 3px，讓 cpc_L4 在字級 18 時類別標籤緊貼長條卻過關。）
TOL_TEXT, TOL_RECT = 3, 1

JS_BOXES = """() => {
  const out = [];
  document.querySelectorAll('svg text, svg rect').forEach((el, i) => {
    const b = el.getBBox();
    out.push({i, tag: el.tagName, x: b.x, y: b.y, w: b.width, h: b.height});
  });
  return out;
}"""


def bump(svg: str, target: float) -> str:
    k = target / BASE_FONT
    return re.sub(r'font-size="([\d.]+)"',
                  lambda m: f'font-size="{round(float(m.group(1)) * k, 1)}"', svg)


def overlaps(boxes) -> set[tuple[int, int]]:
    pairs = set()
    for a in range(len(boxes)):
        A = boxes[a]
        if A["w"] <= 0 or A["h"] <= 0:
            continue
        for b in range(a + 1, len(boxes)):
            B = boxes[b]
            if B["w"] <= 0 or B["h"] <= 0 or (A["tag"] == "rect" and B["tag"] == "rect"):
                continue                      # 底色與格線互疊屬正常
            tol = TOL_TEXT if A["tag"] == B["tag"] == "text" else TOL_RECT
            ox = min(A["x"] + A["w"], B["x"] + B["w"]) - max(A["x"], B["x"])
            oy = min(A["y"] + A["h"], B["y"] + B["h"]) - max(A["y"], B["y"])
            if ox > tol and oy > tol:
                pairs.add((a, b))
    return pairs


def main() -> int:
    svg_dir, png_dir = Path(sys.argv[1]), Path(sys.argv[2])
    cands = [float(x) for x in (sys.argv[3] if len(sys.argv) > 3
                                else "20,19,18,17,16").split(",")]
    png_dir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    chosen: dict[str, float] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()

        def boxes_of(svg, w, h):
            pg = browser.new_page(viewport={"width": w, "height": h})
            pg.set_content(f'<style>html,body{{margin:0;padding:0}}</style>{svg}',
                           wait_until="load")
            pg.wait_for_timeout(120)
            bx = pg.evaluate(JS_BOXES)
            pg.close()
            return bx

        for f in sorted(svg_dir.glob("*.svg")):
            svg = f.read_text(encoding="utf-8")
            m = re.search(r'<svg[^>]*\swidth="([\d.]+)"[^>]*\sheight="([\d.]+)"', svg)
            w, h = (round(float(m.group(1))), round(float(m.group(2)))) if m else (1180, 560)

            have = [float(x) for x in re.findall(r'font-size="([\d.]+)"', svg)]
            preset = max(have) if have and max(have) >= 18 else None
            pick = preset if preset else BASE_FONT
            if not preset:
                base_pairs = overlaps(boxes_of(svg, w, h))
                for t in sorted(cands, reverse=True):
                    if not (overlaps(boxes_of(bump(svg, t), w, h)) - base_pairs):
                        pick = t
                        break
            chosen[f.stem] = pick

            page = browser.new_page(viewport={"width": w, "height": h},
                                    device_scale_factor=SCALE)
            page.set_content(
                f'<style>html,body{{margin:0;padding:0;background:#fff}}'
                f'svg{{display:block}}</style>{svg if preset else bump(svg, pick)}',
                wait_until="load")
            page.wait_for_timeout(250)         # 等字型載入完成再截
            page.screenshot(path=str(png_dir / (f.stem + ".png")),
                            clip={"x": 0, "y": 0, "width": w, "height": h})
            page.close()
            print(f"{f.stem:<34} {w}x{h}  圖內字級 {BASE_FONT} → {pick}")
        browser.close()

    (png_dir / "font_choice.json").write_text(
        json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
