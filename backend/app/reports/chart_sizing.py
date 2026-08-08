"""圖表尺寸與字級的兩份 profile（2026-08-03 使用者定案：只分尺寸與字級，版面邏輯共用）。

## 為什麼存在

`chart_runner` 產的 SVG 同時被網頁直接顯示、被 PPT 縮進圖框——兩邊約束不同：
PPT 圖框 4.32in、縮兩次後字要 ≥12pt；網頁滿寬可捲動。2026-08-03 實證：
為 PPT 把年度矩陣改 16 欄，連帶把三張**報表區塊**的圖弄壞（年份黏串、泡泡互撞、
註記壓長條）——使用者定案「你要嘛把兩邊獨立分開，要嘛注意修 ppt 不要搞到這邊也壞掉」。

三個選項中選了中間：**只把會衝突的參數分開**（畫布尺寸、字級目標、列高、圖框），
版面邏輯（排序、左右配置、註記內容、編碼說明）共用——完全分成兩套渲染被否決，
版面邏輯分開會漂移成兩套，正是「同一份知識只能有一個定義處」要防的事。

## 使用規則

- 🔴 **修 PPT 的尺寸／字級 → 只改 `PPT` profile**（或 theme.json 的圖框，
  一致性測試會盯兩邊相等）。
- `WEB` profile 目前與 `PPT` 同值（單一輸出、兩端共用同一張 SVG）；
  日後網頁要走自己的尺寸時，改 `WEB` 並在渲染端分流——欄位已備好，不必再翻架構。
- ⚠ **位置參數不在此列**：標籤位數、泡泡半徑、註記位置一律由這些尺寸**推導**
  （見 chart_runner 的 `chart_font_px`／`solve_chart_font`）。兩份尺寸配上寫死的
  位置＝壞兩倍，不是壞一半（2026-08-03 三個 bug 全是這樣來的）。
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ChartSizing:
    """一組會在網頁／PPT 之間衝突的尺寸與字級參數。"""

    canvas_width: int            # 畫布寬（px）——縮放比的主要決定者
    canvas_max_height: int       # 畫布高上限（px）
    data_target_pt: float        # 資料文字的最終顯示大小（縮進圖框後）
    note_target_pt: float        # 註記／圖例的最終顯示大小
    row_height: int              # 長條圖基準列高（px，隨字級縮放）
    year_window: int             # 年度矩陣顯示年數
    bar_height: int              # 排名長條高（px）
    bubble_min_radius: float     # 年度矩陣大泡泡的最小半徑（px）
    hero_frame_in: tuple[float, float]   # PPT 側欄版型圖框（in）＝theme.chart_hero.image
    wide_frame_in: tuple[float, float]   # PPT 滿寬版型圖框（in）＝theme.chart_wide.image
    wide_aspect_min: float       # 長寬比達此值改用滿寬版型（與 build_ppt 同值）


#: PPT profile——目前的權威值（圖是進簡報的 artifact，以 PPT 約束為準）。
PPT = ChartSizing(
    canvas_width=949,
    canvas_max_height=460,
    data_target_pt=14.0,    # 2026-08-04 使用者定案「超過的降下來，不夠的要調上去」
    note_target_pt=12.0,    # 註記比資料小一級；12pt 是全案硬底線，不得再低
    row_height=28,
    year_window=16,         # 2026-08-03 使用者定案（原 15 是拍的，16 由資料橫距量出）
    bar_height=18,
    bubble_min_radius=14.0,
    hero_frame_in=(8.9, 5.0),
    wide_frame_in=(12.13, 3.2),
    wide_aspect_min=3.5,
)

#: WEB profile（P3，2026-08-07 起有自己的值）——網頁滿寬可捲、不經 PPT 圖框縮放。
#: 🔴 只改**尺寸與字級**：排序、配色、註記內容、版面邏輯一律與 PPT 共用
#: （separate-web-and-ppt-chart-profiles 的 Non-goals 明列不建第二套 engine）。
#: - 畫布更寬：網頁沒有 4.32in 圖框限制，同樣列數更不擠
#: - 目標字級略低：網頁只縮一次（PPT 縮兩次），12pt 在螢幕上已清楚
#: - 列高略增：滑鼠瞄準與可讀性優先，不必為頁高妥協
WEB = replace(
    PPT,
    canvas_width=1180,
    canvas_max_height=560,
    data_target_pt=12.0,
    note_target_pt=10.5,
    row_height=32,
)
