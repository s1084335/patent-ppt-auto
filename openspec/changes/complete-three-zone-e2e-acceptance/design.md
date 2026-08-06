# Design: 三區 E2E 驗收

## Context

現有 `e2e-test-checklist-three-zones.md` 是人工清單，tests 主要為 API、DB 與靜態 HTML 契約，沒有 browser E2E。可用工具為 `D:\vscode\playwright`；專案資料庫測試已有 production URL 防護，但 E2E 還需要獨立 manifest 與 cleanup gate。

## Goals / Non-Goals

**Goals:** 真服務、固定 fixture、桌面/行動、API/DB/畫面交叉驗證、證據保存、清理需核准。

**Non-Goals:** 不在正式 workspace 跑；不以 screenshot-only 取代數據 assert；不在 E2E change 夾帶 bug fix。

## Decisions

### 1. 一次 suite 建一個 run identity

run id 寫入 workspace 名、檔案名/object prefix 與 evidence 目錄。所有建立資料記 manifest，對帳與 cleanup 都只接受該 manifest，避免模糊條件刪資料。

### 2. API setup 最小化，核心流程必須走 UI

可用 API 建立不屬於測試目標的前置，但上傳、workspace 切換、瀏覽、分類、排除與刷新必須由 browser 操作。每個關鍵 UI assert 同時核對 API/DB，定位顯示錯或資料錯。

### 3. deterministic 與 real-AI profiles 分離

required profile 以 deterministic fixture/fake runner 驗流程；real Companion 為 opt-in acceptance，保存 model/prompt/version。兩者結果不得混稱。

### 4. 視覺採全頁掃描加程式化檢查

桌面/行動每個三區狀態都截圖；檢查 console error、元素 bounding box overlap、水平捲軸可達、圖片非空白。不是只抽首頁。

## Test And Data Boundaries

- Playwright tests 與 helpers 放 `tests/e2e/` 或專案既有測試架構相容位置。
- fixture 使用小型 WIPS 檔與隔離 DB/storage。
- evidence：screenshots、trace、console/network、API/SQL summary、manifest、environment versions。

## Risks / Trade-offs

- [AI/分群耗時且不穩] → required deterministic + opt-in real profile。
- [資料清理誤傷] → exact manifest、target preflight、使用者核准。
- [flaky timing] → 以 job/SSE observable state 等待，不用固定 sleep。
- [視覺差異跨機] → 固定 browser/version/font/viewport，像素與結構檢查分層。

## Migration Plan

先跑唯讀 browse smoke，再隔離匯入，接分類治理，最後 real-AI profile。測試資料第一次保留供使用者檢視；核准 cleanup 流程後才自動化清理。
