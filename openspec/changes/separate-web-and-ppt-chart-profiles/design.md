## Context

現行 chart runner 產生報表資料與圖檔，網頁與 PPT 都依 artifact identity 取用；goal-driven 規劃另要求使用者選圖全部傳入 CLI，CLI 無權自行增減圖片。若只把同一 raster 縮放到兩種媒介，字級與長寬比互相牽制；若分叉 renderer 或 transform，則會破壞圖表口徑唯一來源。

## Goals / Non-Goals

**Goals:**

- 一份 chart specification／dataset 產生兩種媒介 profile，保持資料與視覺語意同源。
- 使用者選圖以穩定 identity 解析，不以檔名猜測。
- 將已選圖的 PPT asset 全部交給 CLI，維持唯讀與不可換圖邊界。

**Non-Goals:**

- 不新增圖表種類、分析指標、插圖或固定頁序。
- 不讓 CLI 執行圖表 renderer 或直接查 artifact store。

## Decisions

### 1. Profile 是 renderer 參數，不是第二套圖表定義

`REPORT_DEFINITIONS`／chart specification 保持資料、排序、色彩 semantic token 與 layout algorithm 的唯一來源；`RenderProfile` 只提供 canvas、DPI、font scale、stroke scale 與 margin policy。未採複製 `render_*_web`／`render_*_ppt`，避免兩份邏輯逐步漂移。

### 2. Chart identity 與 profile identity 分離

穩定 chart identity 由 report version、report key、variant key 與 chart key 組成；profile 是同 identity 下的 artifact 維度。manifest 保存 dataset/version、renderer version、profile 與 checksum，不依 `_ppt` 檔名字串作真相。

### 3. 選圖 payload 傳 identity，CLI payload 傳已解析實體

瀏覽器提交所選 chart identities；backend 在同一 report version 的 artifact store 解析並驗證對應 PPT profiles，再建立 evidence manifest 與 CLI 可讀的封裝路徑／bytes。跨 backend 與 Companion 的唯一交換媒介是版本化 artifact store 加 manifest；CLI 不取得 DB 或任意 artifact list 權限。

### 4. 雙 Profile 採原子完整性閘門

對支援 PPT 的 chart，該版本只有在兩個 profile 與 manifest 都持久化後才標示可匯出。單一 profile 失敗可以保留診斷 artifact，但不可形成可核准輸出。舊版本不回填猜測值，需重產才能進新流程。

## Architecture And Data Flow

1. Report transform 產生一次 canonical dataset 與 chart specification。
2. Renderer 以 `web`、`ppt` profiles 各執行一次，輸出兩個 raster/vector artifact。
3. Artifact store 保存兩份實體與同一 manifest lineage。
4. Report API 向前端提供 web asset 與 stable chart identity。
5. 匯出 API 驗證選取集合並解析 PPT assets，寫入 goal/evidence manifest。
6. Companion／CLI 只讀封裝後的全部選圖與證據；SlidePlan validator 檢查集合完全相等。

## Code And Test Boundaries

- Report definitions/transform/renderers：`backend/app/reports/`。
- Artifact persistence/API：report artifact store、report content/asset endpoints。
- Selection/planning：goal-driven report request、evidence manifest、SlidePlan validator。
- PPT composition：`skills/patent-report-ppt/` 只消費已驗證 asset。
- Tests：renderer parity、manifest schema/checksum、artifact persistence、selection set equality、舊版本錯誤、PPT 全頁渲染。

## Output Contract

每個 chart manifest entry 包含 chart identity、report/variant/chart keys、dataset/version identity、renderer version，以及 `web`／`ppt` profile 的媒介參數、artifact key、mime type、dimensions 與 checksum。選圖 manifest 另保存使用者選取順序與兩個 profile 的 lineage；CLI input 只暴露 PPT profile 實體與必要證據。

## Risks / Trade-offs

- [同圖渲染兩次增加時間與儲存] → 只對可選入 PPT 的圖產雙 profile，以 checksum/cache 避免無變更重算。
- [參數不同仍造成版面語意漂移] → parity tests 比對 canonical dataset、排序、legend/series identity 與 semantic color tokens。
- [舊版本不能直接匯出] → UI 明確標示需重產，不靜默使用低品質或來源不明圖。
- [選圖解析遭跨版本混用] → 所有 lookup 強制 report version 與 dataset identity，任一不符 fail loud。

## Migration Plan

1. 先定義 manifest/profile schema 與 parity tests，再改 renderer。
2. 對固定報表版本雙寫 web/PPT profiles，舊 API 仍讀 web profile。
3. 接選圖解析、evidence manifest 與 SlidePlan set-equality gate。
4. 重產驗收版本，完成網頁與 PPT 實物驗收後才啟用新匯出入口。
5. 回滾時停用新匯出 gate 並保留舊 web artifact 讀取；已產雙 profile 可留存，無 DB migration 或資料回填需逆轉。
