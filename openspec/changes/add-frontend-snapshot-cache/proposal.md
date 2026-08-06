## Why

目前瀏覽與分類畫面依賴即時 backend 查詢，重新整理或斷線時容易清空內容、卡在 loading，長任務完成後又可能重畫整區並丟失使用者的展開／選取狀態。

## What Changes

- 為瀏覽專利、workspace 專利、分類主題與主題專利建立版本化 snapshot。
- 先顯示最近成功 snapshot，再背景 refresh；refresh 失敗保留舊資料並標示 stale。
- 將前端更新切為有固定 identity 的區塊，保存選單、展開、編輯與選取狀態。
- 建立寫入操作到受影響區塊的唯一 mapping。

## Capabilities

### New Capabilities

- `frontend-snapshot-cache`：定義 snapshot schema、讀取／刷新狀態、區塊更新與 stale 行為。

### Modified Capabilities

- `workspace-and-browse`：瀏覽與分類讀取改為 snapshot-first 並保存互動狀態。

## Scope

前端四個主要資料區、snapshot API/storage、區塊刷新 mapping、長任務完成後同步。

## Non-goals

- 不把所有 API 無條件快取。
- 不在第一批擴到與瀏覽／分類無關的管理畫面。
- 不讓 GET snapshot 隱含執行昂貴 refresh。

## Impact

影響前端 state model、API、DB snapshot storage、寫入操作後同步與 E2E 測試；可能需要 migration。

## Activation

若新增 snapshot table 需 migration 與部署；舊客戶端無 snapshot 時須能顯示空狀態並允許 refresh。

## Acceptance Gate

涵蓋首次無快照、命中快照、背景刷新、刷新失敗、斷線、重分群、展開狀態與選定值保存的完整決策表，並以實際瀏覽器流程驗收。

