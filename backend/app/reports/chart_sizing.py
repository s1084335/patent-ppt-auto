"""圖表尺寸與字級的**唯一** profile。

## 為什麼只剩一份

2026-08-03 曾分 `PPT`／`WEB` 兩份：同一張 SVG 要同時被網頁直接顯示、被 PPT 縮進
圖框，兩邊約束衝突（那次為 PPT 把年度矩陣改 16 欄，連帶把三張報表區塊的圖弄壞
——年份黏串、泡泡互撞、註記壓長條）。當時的解法是「只把會衝突的參數分開，
版面邏輯共用」。

2026-08-12 `unify-chart-source`（RPT-010）定案**圖表單一來源**：引擎只輸出一份
網頁尺寸 SVG，簡報端自行 refit 字級，PPT 補償鏈整條退場。`PPT` profile 自此
沒有任何消費者，連同只有它在用的圖框欄位（`hero_frame_in`／`wide_frame_in`／
`wide_aspect_min`——那是**已移除的 `build_ppt`** 的數字）於
`restructure-html-report-export` 一併刪除。

⚠ 不留「以後可能用到」的死參數：那些圖框與 deck skill 的幾何對不起來，
留著會讓人以為改它能影響簡報，**而且不會有任何東西報錯**。簡報端的尺寸知識
現由 deck 組版層單一持有。

## 使用規則

- 改圖表尺寸／字級 → 改 `WEB`；`chart_runner` 的 11 個尺寸常數由它推導，
  一致性測試（`test_chart_sizing_profile`）盯著，寫死數字就會紅。
- ⚠ **位置參數不在此列**：標籤位數、泡泡半徑、註記位置一律由這些尺寸**推導**
  （見 chart_runner 的 `chart_font_px`／`solve_chart_font`）。尺寸配上寫死的
  位置＝壞兩倍，不是壞一半（2026-08-03 三個 bug 全是這樣來的）。
"""
from __future__ import annotations

from dataclasses import dataclass

#: 🔴 字型的**唯一定義處**（2026-08-13 使用者裁決；deck design 4c／tasks 2.2b）。
#:
#: 為什麼是同一份知識：改圖表字型時，簡報原生文字（標題／內文）也必須跟著改，
#: 否則同一頁上圖內字與標題會是兩種字型。既然「改 A 就得改 B」，就只能有一處。
#:
#: 🔴 為什麼不能各處自己寫：`fit_render_charts` 用 Chromium 的 `getBBox` 量
#: SVG 文字來決定圖內字級——**字型宣告不一致 → 量測錯 → 字級跟著錯，且不報錯**。
#: 2026-08-13 實掃就抓到現成的漂移：`chart_runner.SVG_FONT_STYLE` 宣告正黑體，
#: 同檔四個 SVG 根元素卻宣告 Segoe UI（中文靠 fallback），同一張圖兩種宣告。
#:
#: ⚠ 換字型是**連鎖工程**，不是改一個字串：`deck_layout.LS_RENDER`、
#: `MIN_CHART_PT`／`MIN_CHART_PT_MULTI`、`svg_canvas.BASELINE_RATIO` 都是量自
#: 特定字型的比例，換了要全部重量並重建 regression 基準。
FONT_FAMILY = "Noto Sans TC"

#: HTML／SVG 用的完整 fallback 鏈。⚠ 由 `FONT_FAMILY` 導出，不重打字型名
#: ——重打就是第二個落點。後面兩個是「使用者機器沒裝 Noto」時的退路，
#: ⚠ 但退到它們時量測與產出會一起偏（見 design 4-0b 第 6 項）。
FONT_STACK = f"'{FONT_FAMILY}','Microsoft JhengHei','Segoe UI',sans-serif"


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


#: 🔴 唯一 profile（2026-08-12 restructure-html-report-export 起）。
#:
#: 沿革：2026-08-03 曾分 `PPT`／`WEB` 兩份，因為同一張 SVG 要同時滿足
#: 「PPT 圖框 4.32in、縮兩次後字要 ≥12pt」與「網頁滿寬可捲」兩種約束。
#: `unify-chart-source`（RPT-010，2026-08-12 驗收）定案**單一來源**後，
#: 引擎只輸出網頁尺寸、簡報端自行 refit，`PPT` profile 隨即失去所有消費者
#: ——連同只有它在用的圖框欄位（`hero_frame_in`／`wide_frame_in`／
#: `wide_aspect_min`，那是**已移除的 `build_ppt`** 的數字）一併刪除。
#: ⚠ 不留「以後可能用到」的死參數：它與 deck skill 的幾何對不起來，
#: 留著會讓人以為改它能影響簡報，而且不會有任何東西報錯。
#:
#: 目前的值（2026-08-11 使用者定案「HTML 的圖中文字和表格文字都維持在 15」）：
#: target 15px＝11.25pt，資料與註記同級，全部圖表在頁面上同一字高；
#: `chart_scale` 恆 1.0，不做任何圖框補償。
WEB = ChartSizing(
    canvas_width=1180,
    canvas_max_height=560,
    data_target_pt=11.25,   # 15px @96dpi
    note_target_pt=11.25,   # 使用者「都維持在 15」——註記不再小一級
    row_height=32,
    year_window=16,         # 2026-08-03 使用者定案（原 15 是拍的，16 由資料橫距量出）
    bar_height=18,
    bubble_min_radius=14.0,
)
