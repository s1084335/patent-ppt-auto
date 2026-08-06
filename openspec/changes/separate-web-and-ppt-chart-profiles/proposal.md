## Why

同一張圖直接同時服務網頁與 PPT，會讓字級、長寬比與可讀性互相牽制；但另建兩套圖表邏輯又會造成資料與視覺語意漂移。系統需要以同一 chart identity、dataset 與版面邏輯產生 web／PPT 兩種 rendering profile，並確保使用者選中的圖仍是 CLI 唯一可用圖源。

## What Changes

- 每個可匯出的 chart 以同一資料、排序、色彩語意與 layout logic 產生 `web` 與 `ppt` profile，只允許尺寸、DPI、字級與必要邊距不同。
- chart manifest 保存 report/variant/chart identity、profile、dataset/version 與 checksum，避免兩種 profile 配錯資料。
- 使用者在網頁選圖後，系統解析並傳入同一 chart identity 的 PPT profile asset；全部選圖仍必須進 CLI，CLI 不得自行替換、增減或重畫圖表。
- 缺少、過期或 identity 不符的 PPT profile 必須 fail loud，不得退回任意舊圖或讓 CLI 自行選圖。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `patent-reporting`：同一 chart contract 支援可驗證的 web／PPT rendering profiles。
- `report-export`：使用者選圖解析為同 identity 的 PPT profile，並完整交給唯讀規劃 CLI。

## Scope

報表 chart renderer、artifact naming/manifest、選圖 payload、evidence manifest 與 PPT 組版入口；不改資料分析口徑。

## Non-goals

- 不建立兩套獨立 chart engine 或資料 transform。
- 不允許 CLI 自行查找、生成或替換圖片。
- 不藉此新增圖表種類、插圖或固定 PPT 頁序。

## Impact

影響 `backend/app/reports/` renderer 與 artifact contract、report content/selection API、goal-driven planning payload、PPT asset resolver 與相關測試；不需 DB migration，既有單 profile artifact 需有明確相容或重產策略。

## Activation

先為指定 report version 重產兩種 profile 與 manifest，再啟用新的選圖解析；舊版本缺少 PPT profile 時應明確標示需重產，不得靜默 fallback。

## Acceptance Gate

以固定 report version 驗證 web／PPT profiles 的 dataset identity、排序、語意色彩與 checksum lineage 一致，尺寸／字級符合各自門檻；選取多張圖後逐一核對 CLI payload 與輸出 PPT 全數使用對應 PPT profile，並以全頁渲染檢查裁切、重疊與可讀性。
