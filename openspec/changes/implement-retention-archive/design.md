# Design: 保留與封存政策

## Context

jobs、events、report artifacts、AI artifacts 與中間輸出會持續累積；若沒有一致政策，資料庫與儲存空間會無界成長。此 change 建立先標記、再封存、最後依政策刪除的生命週期，並保留專利主資料、追溯關係與稽核證據。

## Goals / Non-goals

- 目標：每種資料類別有 owner、保留期、封存格式、刪除條件與 legal hold 行為。
- 目標：cleanup 可 dry-run、可重入、可觀察，部分失敗不造成孤兒資料。
- 非目標：刪除專利事實主資料、繞過 workspace 隔離、把備份當成可查詢封存。

## Decisions

### 1. 先建立資料分類，再啟用刪除

第一階段只產生 inventory 與 dry-run。只有已定義 owner、retention days、archive target、referential order 的類別才能進入 destructive mode。

### 2. 封存 manifest 是必要輸出

每批封存包含 batch id、workspace、資料類別、時間範圍、筆數、checksum、schema version、artifact location 與執行結果。沒有成功且可讀回的 manifest，不得刪來源。

### 3. 依參照順序清理且保留 legal hold

先處理依賴/子資料，再處理 job/event 等父資料；legal hold、進行中 job、latest report 指標與仍被 workspace 引用的 artifact 一律排除。

## Architecture And Data Boundaries

- policy registry：資料類別與保留規則的唯一來源。
- inventory/planner：計算候選、排除原因、預估容量。
- archiver：輸出不可變 artifact 與 manifest，並執行 read-back/checksum。
- cleaner：以小批次 transaction 刪除，記錄 audit row 與 metrics。
- scheduler/admin command：控制 dry-run、execute、resume，不在 API request 內長時間執行。

## Output Contract

- dry-run：候選數量、大小、日期範圍、排除數與原因，不修改資料。
- archive：manifest、checksum、read-back 結果與失敗項目。
- cleanup：刪除筆數、保留筆數、batch cursor、耗時與錯誤。
- audit：誰、何時、依哪個 policy/version 執行，可由 workspace 與 batch id 追查。

## Test Strategy

- 單元：cutoff、legal hold、latest pointer、依賴順序、batch resume。
- DB 整合：FK、transaction rollback、併發新增、部分 artifact failure。
- artifact：archive write/read/checksum 與 manifest schema。
- 驗收：先在測試資料庫 dry-run，核對筆數後才 execute；保留前後 SQL 與 storage inventory 證據。

## Risks And Migration

- 風險：誤刪仍被引用資料；execute 前再次查詢排除條件並由 FK 作最後防線。
- 風險：DB 與 object storage 不一致；採 archive-confirm-delete 與可重入 batch state。
- 遷移：policy registry 與 dry-run 先上線，觀察至少一個週期，再由人工閘門啟用特定資料類別；預設 destructive mode 關閉。
