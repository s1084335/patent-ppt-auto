"""星空紋理生成器：深空主題的背景疊層（確定性，同參數必產出同一張圖）。

⚠ 為什麼是「程式生成」而不是「放一張現成圖」：
使用者提供的風格範本是 NotebookLM 產出的整頁圖片（10 頁全為圖片、零 shape），
程式抽不到參數，只能像素採樣後重建。直接塞一張固定 PNG 也能像，但換尺寸、
調密度、改配色都得重畫；參數化之後改 theme.json 即可，且 seed 固定
＝每次組版產出完全一致（可重現、可驗收）。

⚠ 漸層**不在這裡**：pymupdf 不渲染 SVG 漸層（實測整片變純黑），
故背景漸層一律交給 python-pptx 原生 gradient fill（向量、放大不糊），
本檔只負責星點與星座連線。

參數全部來自 theme.json 的 `starfield` 區段，本檔不寫死任何數值（seed 亦為參數）。
"""
from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any


def _make_stars(rng: random.Random, cfg: dict[str, Any], density: float) -> list[tuple[float, float, float, str, float]]:
    """產生星點 (cx, cy, r, 顏色, 不透明度)。

    density＝密度倍率（封面 1.0、內頁取 inner_page_density）。同時作用在數量與
    不透明度上：內頁星點若與圖表資料點一樣顯眼，會讓人誤以為圖上有資料。
    """
    width = int(cfg["width_px"])
    height = int(cfg["height_px"])
    radii = [float(r) for r in cfg["star_radii"]]
    lo = float(cfg["opacity_min"])
    hi = float(cfg["opacity_max"])
    bright_ratio = float(cfg["bright_ratio"])
    stars: list[tuple[float, float, float, str, float]] = []
    for _ in range(int(cfg["star_count"] * density)):
        cx = rng.uniform(0, width)
        cy = rng.uniform(0, height)
        radius = rng.choice(radii)
        # 少數亮星用 accent 青、其餘用微星白——兩色的比例就是範本的視覺層次。
        color = cfg["bright_color"] if rng.random() < bright_ratio else cfg["star_color"]
        stars.append((cx, cy, radius, color, rng.uniform(lo, hi) * density))
    return stars


def _make_links(rng: random.Random, cfg: dict[str, Any],
                stars: list[tuple[float, float, float, str, float]],
                density: float) -> list[tuple[float, float, float, float, str, float]]:
    """在距離適中的星點之間連線，模擬星座圖 (x1, y1, x2, y2, 顏色, 不透明度)。

    只連距離落在 [min, max] 的配對：太近會糊成一團，太遠會像亂拉線。
    attempts 上限避免參數極端（例如區間取得太窄）時空轉。
    """
    if len(stars) < 2:
        return []
    limit = int(cfg["link_count"] * density)
    dmin = float(cfg["link_dist_min"])
    dmax = float(cfg["link_dist_max"])
    colors = list(cfg["link_colors"])
    lo = float(cfg["link_opacity_min"])
    hi = float(cfg["link_opacity_max"])
    links: list[tuple[float, float, float, float, str, float]] = []
    attempts = 0
    while len(links) < limit and attempts < max(limit, 1) * 200:
        attempts += 1
        a = rng.choice(stars)
        b = rng.choice(stars)
        if not (dmin <= math.hypot(a[0] - b[0], a[1] - b[1]) <= dmax):
            continue
        links.append((a[0], a[1], b[0], b[1], rng.choice(colors), rng.uniform(lo, hi) * density))
    return links


def render_starfield_svg(cfg: dict[str, Any], *, density: float = 1.0) -> str:
    """產出星空 SVG 字串；同 cfg 與 density 必得同一份輸出。"""
    width = int(cfg["width_px"])
    height = int(cfg["height_px"])
    rng = random.Random(int(cfg["seed"]))
    stars = _make_stars(rng, cfg, density)
    links = _make_links(rng, cfg, stars, density)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">']
    # 連線先畫、星點後畫：星點壓在線上，線才不會把星點切開。
    for x1, y1, x2, y2, color, opacity in links:
        parts.append(
            f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            f'stroke="{color}" stroke-width="{cfg["link_width"]}" opacity="{opacity:.2f}"/>'
        )
    for cx, cy, radius, color, opacity in stars:
        parts.append(
            f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{radius}" fill="{color}" opacity="{opacity:.2f}"/>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def starfield_png(cfg: dict[str, Any], cache_dir: Path, *, density: float = 1.0) -> Path | None:
    """產星空 PNG（供貼進投影片）；同參數已存在就重用，不重算。

    ⚠ 回傳 None＝產圖失敗（例如環境沒有 pymupdf）。呼叫端必須**略過紋理層**
    而不是中斷組版：少一層紋理只是比較樸素，整份簡報產不出來才是嚴重的。
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    stem = f"starfield_{int(cfg['seed'])}_{density:.2f}"
    png_path = cache_dir / f"{stem}.png"
    if png_path.exists():
        return png_path
    svg_path = cache_dir / f"{stem}.svg"
    svg_path.write_text(render_starfield_svg(cfg, density=density), encoding="utf-8")
    try:
        import pymupdf  # 延後匯入：沒有它時仍要能產出無紋理的簡報

        doc = pymupdf.open(str(svg_path))
        # ⚠ alpha=True 不可省：pymupdf 預設把 SVG 渲染在**白底**上，
        # 貼到投影片就是一張整版白圖，會把底下的漸層完全蓋掉（實機驗證）。
        doc[0].get_pixmap(dpi=int(cfg["render_dpi"]), alpha=True).save(str(png_path))
    except Exception:
        return None
    return png_path
