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

#: 🔴 SVG 圖元**角色標記**的唯一定義處（2026-08-19，tasks §6.3a）。
#:
#: 為什麼是同一份知識：產生端（`chart_runner`）打標記、消費端
#: （`skills/html-report-to-deck` 的 `rebuild_chip_chart`）依標記取內容，
#: 「改 A 就得改 B」——放兩處必然分岔。與字型同理，故同住這裡。
#:
#: 🔴 為什麼一定要有角色標記：消費端原本靠**顏色值**辨認元素——
#: `if "#9CA3AF" in attrs` 用來認「這段是註記」。顏色是**樣式**，樣式會改：
#: §6.2 裁決「兩套深藍都留但不得同頁」，做法是 SVG 進 deck 時整批換色，
#: 換色一上，那個判斷就找不到目標而 `next(..., "")` **回空字串**——
#: 註記與 FTO 頁尾從圖上消失，且**沒有任何東西會報錯**（缺席型偏差）。
#: 角色是**語意**，語意才能拿來辨認。
#:
#: ⚠ 一個角色在同一張圖只能出現一次：消費端用 `next(...)` 取第一個，
#: 重複就變成碰運氣（測試 `test_roles_are_unique` 盯著）。
ROLE_CHART_TITLE = "chart-title"
ROLE_CHART_NOTE = "chart-note"      # 口徑防呆註（圖上方）
ROLE_CHART_FOOTER = "chart-footer"  # FTO 聲明等頁尾註（圖下方）


@dataclass(frozen=True)
class PaletteEntry:
    """一個色票條目。

    `medium` 是 §6.2「兩套深藍都留但不得同頁」的依據：
    分界不是「哪個模組」而是「哪個媒介」——HTML 報表一種、PPTX 簡報一種，
    同一份 SVG 進 deck 時整批換色，於是任一頁上只會出現一種。

    ⚠ `purpose` 不得留空（§6.5）：沒有用途說明的色，下一個人只能靠猜要不要
    沿用，於是又多一種。實查時 chart 側 48 種顏色有 **24 種完全沒有具名常數**，
    就是這樣長出來的。
    """

    hex: str
    medium: str          # "report"（HTML 報表）／"deck"（PPTX）／"both"（共用）
    purpose: str         # 語意用途，空字串不算填


#: 🔴 色票的**唯一定義處**（2026-08-19，tasks §6.3）。
#:
#: 為什麼與字型同住這裡：`deck_layout.py:27` 與 `rebuild_chip_chart.py` 已經
#: import 本模組取 `FONT_FAMILY`（字型 2026-08-13 使用者裁決選項 A）。
#: 色票沿用同一條路——少一個序列化層，也少一份可能分岔的副本。
#:
#: ⚠ 目前只收**已有具名常數**的色。實查（§6.0）另有 24 種散落在函式裡、
#: 完全沒有名字的色，那批要在 §6.4／§6.5 逐一命名並填用途後才進來。
#: **不預先塞進來湊數**：沒有用途說明的條目等於把問題搬個地方放。
PALETTE: dict[str, PaletteEntry] = {
    # ── 文字（🔴 §6.2 的兩套深藍，媒介分流）───────────────────────────
    "TEXT_IN_CHART": PaletteEntry(
        "#00094A", "report",
        "圖內標題與主文字（SVG／HTML 報表）。原 chart_runner.COLOR_TEXT"),
    "TEXT_ON_PAGE": PaletteEntry(
        "#0B2545", "deck",
        "投影片頁面文字（PPTX）。原 deck_layout.TEXT"),
    "TEXT_SOFT": PaletteEntry(
        "#869FB2", "report", "次要文字：刻度、副標"),
    "TEXT_ON_LIGHT": PaletteEntry(
        "#1A1A1A", "report",
        "淺色填色**上**的圖元內文字。⚠ 與 TEXT_IN_CHART 不共用——"
        "那是頁面文字，這是畫在圖元上的，底色來源不同"),
    # ── 資料主色 ──────────────────────────────────────────────
    "DATA_PRIMARY": PaletteEntry(
        "#006DF5", "report", "申請線／長條主色（COLOR_APPLICATION／COLOR_BAR）"),
    "DATA_ALERT": PaletteEntry(
        "#C62828", "report", "公告線：與藍線對比（COLOR_PUBLICATION）"),
    "DATA_SEGMENT": PaletteEntry(
        "#D97706", "report",
        "分段長條的區段色（有最新受讓人）。⚠ 與藍段色相差約 180°，色盲安全配對"),
    "DATA_BAR_ALT": PaletteEntry(
        "#C99A5B", "report", "次要長條：暖中性，與資料暖色系一致"),
    "DATA_TRANSFERRED": PaletteEntry(
        "#7C3AED", "report",
        "已轉讓（申請人排名圖）：2026-08-17 使用者實物驗收「斜線看不清」改第三色"),
    # ── 底與線 ────────────────────────────────────────────────
    "SURFACE_MAP": PaletteEntry("#F8FAFC", "report", "地圖底色"),
    "LINE_GRID": PaletteEntry("#DCE3F2", "report", "格線"),
    # ── 圖例與軸的中性色（2026-08-19 §6.4／§6.5 收編）───────────────
    # ⚠ 用途逐條讀自實際用法（`scripts/audit_palette.py` 掃出的呼叫點），
    #   不是照名字猜。空泛用途在此等於沒填。
    "LEGEND_HEAD": PaletteEntry(
        "#374151", "report",
        "圖例前綴與泡泡描邊：矩陣圖的圖例標題（粗體）與泡泡 stroke"),
    "LEGEND_ITEM": PaletteEntry(
        "#4B5563", "report", "圖例項目文字（比圖例標題淺一階）"),
    "AXIS_TICK_LINE": PaletteEntry(
        "#94A3B8", "report", "軸刻度線與地圖外框；比格線深、比文字淺"),
    "SURFACE_CARD": PaletteEntry(
        "#FFFFFF", "both",
        "白：圖表卡底、深色填色上的反白文字。⚠ 標為 both——報表與 deck 都用，"
        "且兩邊要的就是同一個白，不是各自的白"),
    "SURFACE_TABLE_HEAD": PaletteEntry(
        "#F1F5F9", "report", "HTML 表格表頭底色；矩陣圖空格底色"),
    "LINE_TABLE": PaletteEntry(
        "#E5E7EB", "report", "HTML 表格列分隔線；象限格描邊"),
    "TEXT_TABLE": PaletteEntry(
        "#111827", "report",
        "HTML 表格正文字色。⚠ 與 `TEXT_IN_CHART` 分開：那是 SVG 圖內文字，"
        "這是 HTML 表格——兩者底色與媒介都不同"),
    "BUBBLE_FILL": PaletteEntry(
        "#2563EB", "report", "地圖泡泡填色（單一國家）；與 stroke 成對使用"),
    "BUBBLE_STROKE": PaletteEntry(
        "#1E40AF", "report", "地圖泡泡描邊（單一國家）：同色系深一階"),
    "BUBBLE_STROKE_REGIONAL": PaletteEntry(
        "#92400E", "report",
        "地區型受理局（EP／WO 等）泡泡的描邊；填色沿用 INTENSITY 的「高」橘。"
        "⚠ 與單一國家分色是為了讓讀者一眼分辨「一國」與「一區」"),
    "LINE_ROW_FAINT": PaletteEntry(
        "#EEF2F7", "report", "矩陣圖的列分隔線：比格線更淡，只用來分列不搶視線"),
    "TEXT_NOTE": PaletteEntry(
        "#9CA3AF", "report",
        "圖內註記與頁尾註的文字色（口徑防呆註、FTO 聲明、「本案無此類」）。"
        "⚠ 與 STATUS 的「放棄」、TIER 的 `lead=0`、QUADRANT 的 q3 **撞色但無關**"
        "——那三個是資料編碼，這個是文字層級。改註記灰不該連帶改掉法律狀態"),
}

#: 🔴 報表色 → deck 色（§6.2「都留但不得同頁」的對照表）。
#:
#: 同一份 SVG 進 deck 時依此整批換色。⚠ 左欄必須是 `medium="report"` 的色、
#: 右欄必須是 `medium="deck"` 的色——方向反了會把 deck 的色換成報表的色，
#: 而且產物看起來仍然「只有一種深藍」，閘門會綠。
REPORT_TO_DECK: dict[str, str] = {
    PALETTE["TEXT_IN_CHART"].hex: PALETTE["TEXT_ON_PAGE"].hex,
}


@dataclass(frozen=True)
class ColorScale:
    """一組**有序**、語意相關的色（🔴 2026-08-19 使用者裁決，tasks §6.5）。

    為什麼色票的單位不能只是「一個色」：實查發現四套**獨立**色階恰好共用色值
    ——`#9CA3AF` 同時是法律狀態「放棄」、龍頭涉入 `lead=0`、象限 q3 與註記文字。
    它們是**不同的知識**（改「授權」的綠不該連帶改掉象限 q1），只是撞到同一個
    hex。若拆成一色一條目並禁止重複，會被迫把四套獨立設計綁死——
    **方向與「同一份知識一個落點」想防的完全相反**。

    ⚠ `steps` 存 `(語意, 色值)` 而不只是色值序列：只有 hex 的話，下一個人不知道
    第三格是什麼意思，那就等於沒有語意，與 §6.5 的軟揭露要求不符。
    ⚠ 同一套色階內不得重複色值（那兩階讀者分不出來，是真的 bug）；
    **跨色階重複是允許的**，那正是本結構存在的理由。
    """

    steps: tuple[tuple[str, str], ...]
    medium: str
    purpose: str


#: 🔴 色階登記表（與 `PALETTE` 並列，單色與色階兩種單位並存）。
SCALES: dict[str, ColorScale] = {
    "STATUS": ColorScale(
        (("申請", "#93C5FD"), ("公開", "#60A5FA"), ("審查中", "#006DF5"),
         ("授權", "#10B981"), ("放棄", "#9CA3AF"), ("到期", "#C62828")),
        "report",
        "法律狀態堆疊（受理局圖）。順序即堆疊順序：由「剛遞件」到「權利消滅」，"
        "一條看完生命週期。⚠ 鍵是表格的六欄字面，不是四大桶——圖與表因此逐欄對得上"),
    "INTENSITY": ColorScale(
        (("低", "#93C5FD"), ("中", "#14B8A6"), ("高", "#F59E0B"), ("最高", "#DC2626")),
        "report",
        "年度泡泡矩陣的量級分帶（`YEAR_BUBBLE_COLOR_BANDS`）。"
        "⚠ 與 STATUS 撞色（#93C5FD 同時是「申請」）純屬巧合，兩套各自獨立"),
    "TIER": ColorScale(
        (("lead≥2", "#DC2626"), ("lead=1", "#F59E0B"), ("lead=0", "#9CA3AF")),
        "report",
        "龍頭涉入三級（chip 底色，沿用散點版 tier_colors）"),
    "QUADRANT": ColorScale(
        (("q1", "#10B981"), ("q2", "#3B82F6"), ("q3", "#9CA3AF"), ("q4", "#F59E0B")),
        "report",
        "機會四象限的格子底色。⚠ 象限**名稱**與後續動作的唯一定義處在 "
        "`chart_runner._qlabel`，本表只管色"),
    "RANKING": ColorScale(
        (("第1階", "#0A3A80"), ("第2階", "#0B4FB8"), ("第3階", "#1268D6"),
         ("第4階", "#2E86E0"), ("第5階", "#4A97E3")),
        "report",
        "申請人排名長條的深淺階（由多到少）。🔴 硬約束：**最淺一階也要 ≥3.0** "
        "（WCAG 圖形元素門檻）——色階是從這個下限往上推，不是從主色往下淡。"
        "白底實測對比 10.88／7.43／5.28／3.75／3.08，全數過關。"
        "⚠ 分階依**數值**不依名次：名次相鄰但件數差很多時該有明顯色差"),
    "REPORT_THEME": ColorScale(
        (("paper", "#F4F6F9"), ("card", "#FFFFFF"), ("ink", "#1A1A2E"),
         ("ink-soft", "#5A6472"), ("line", "#E2E6EC"), ("line-soft", "#EEF1F5"),
         ("brand", "#0F3460"), ("brand-soft", "#1A6BC4"), ("wash", "#EDF2F9"),
         ("meta", "#8A93A3"), ("row-hover", "#F8FAFD"), ("btn-hover", "#DFE8F4")),
        "report",
        "HTML 報表頁面的 CSS 變數主題（`:root{--paper…}`）。淺色單一主題，"
        "不宣告 dark——瀏覽器自動反轉會把圖表白底與頁面撞在一起。"
        "🔴 其中 ink／line／brand／brand-soft **與產品前端 index.html 的 "
        "--text／--border／--accent／--accent-2 是同一份知識**（同一個產品不該有"
        "兩套視覺語言）。⚠ 跨語言不能 import，故走一致性測試"
        "（`test_palette_single_source` 斷言兩處相等）——它不防止複製，"
        "但讓分岔立刻紅"),
    "KP_CLASS": ColorScale(
        (("全領域布局", "#D97706"), ("單一技術深布局", "#0D9488"),
         ("利基／探索", "#60A5FA"), ("前案（多失效）", "#6B7280")),
        "report",
        "Key Players 競爭定位的分類色。⚠ 這是**類別編碼不是數值色階**——"
        "同一個分類在不同泡泡必須同色，否則圖例對不上。"
        "分類本身由資料推導，不吃 AI 給的字串"),
}

#: 🔴 **不得出現在同一頁**的色對（2026-08-19 使用者裁決「都留但不同頁」）。
#:
#: 兩組的分離手段不同，這點很重要：
#: - 深藍（`#00094A`／`#0B2545`）靠**媒介**分離——`recolor_for_deck` 換色，
#:   報表側與 deck 側各一種，結構上不可能同頁。
#: - 兩個紅（`#C62828`／`#DC2626`）**都在報表側**，分不了媒介；它們靠**色階**
#:   分離（生命週期 vs 量級），所以只能驗**頁面組成**——同一頁不得同時出現。
#:   實測 ΔE2000 = 4.59（並置可辨），與深藍同一種病，小一號。
#:
#: ⚠ 登記在這裡不等於已經分開，只等於「宣告了要分開」；真正的保證在閘門
#: （深藍＝換色後產物檢查；兩個紅＝逐頁色彩共現檢查）。
NOT_SAME_PAGE: tuple[tuple[str, str], ...] = (
    (PALETTE["TEXT_IN_CHART"].hex, PALETTE["TEXT_ON_PAGE"].hex),
    ("#C62828", "#DC2626"),
)


def known_colours() -> set[str]:
    """色票涵蓋的所有色值（單色 ＋ 色階 ＋ deck 側對照目標）。

    ⚠ 消費端（`recolor_for_deck.unknown_colours`）用它判斷「哪些色沒進色票」。
    漏掉色階的話，四套色階的每一個色都會被報成未知——訊號被雜訊淹掉，
    等於沒有那個功能。
    """
    out = {e.hex for e in PALETTE.values()} | set(REPORT_TO_DECK.values())
    for scale in SCALES.values():
        out |= {h for _lbl, h in scale.steps}
    return out


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
