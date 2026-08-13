"""逐頁截圖：SVG → PNG，供產線目視迴圈使用（B 案）。

## 🔴 為什麼一定要 `goto` 而不是 `set_content`

2026-08-13 實測：用 `set_content` 載入 SVG 時頁面沒有 base URL，SVG 內的
`<image>` 會被跨來源規則擋掉而**破圖**——但同一份 pptx 用 COM 轉圖卻正常。
目視因此看到假警報；更糟的是日後圖真的錯了，分不出是「真的錯」還是「又是
載入問題」。故一律把 SVG 存檔後 `goto file://…`，圖用相對路徑放同目錄。

## 解析度

倍率取自 `deck_layout.VISUAL_SCALE`（唯一定義處，見該常數的註記）。
本檔不自己決定大小，也不接受呼叫端隨意指定——那會變成第二個落點。

用法：python shoot_pages.py <svg_dir> <out_dir>
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 瀏覽器用工作區既有的 Playwright，不另行安裝（沿 fit_render_charts.py 同一套）。
_PW = Path(os.environ.get("PLAYWRIGHT_HOME", r"D:\vscode\playwright"))
sys.path.insert(0, str(_PW / "lib"))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_PW / "browsers"))

sys.path.insert(0, str(Path(__file__).resolve().parent))


def shoot(svg_paths: list[Path | str], out_dir: Path | str) -> list[Path]:
    """逐頁截圖，回傳 PNG 路徑清單（順序同輸入）。"""
    from playwright.sync_api import sync_playwright

    from deck_layout import VISUAL_SCALE, visual_shot_size

    paths = [Path(p).resolve() for p in svg_paths]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    width, height = visual_shot_size()
    # viewport 用版面原尺寸，放大交給 device_scale_factor——這樣 SVG 內的
    # 座標不必跟著倍率變，放大是純粹的取樣密度。
    view_w, view_h = int(width / VISUAL_SCALE), int(height / VISUAL_SCALE)

    written: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": view_w, "height": view_h},
                                device_scale_factor=VISUAL_SCALE)
        for svg_path in paths:
            page.goto(svg_path.as_uri())      # 🔴 goto，不是 set_content（見檔頭）
            page.wait_for_timeout(150)        # 等字型與圖片就位
            target = out_dir / f"{svg_path.stem}.png"
            page.screenshot(path=str(target))
            written.append(target)
        browser.close()
    return written


def main() -> int:
    if len(sys.argv) < 3:
        print("用法：python shoot_pages.py <svg_dir> <out_dir>")
        return 2
    svg_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    pages = sorted(svg_dir.glob("*.svg"))
    if not pages:
        print(f"✗ {svg_dir} 沒有 SVG")
        return 1
    written = shoot(pages, out_dir)
    from deck_layout import visual_shot_size

    w, h = visual_shot_size()
    print(f"✓ 逐頁截圖 {len(written)} 張（{w}×{h}）→ {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
