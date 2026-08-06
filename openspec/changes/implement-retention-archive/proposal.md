## Why

匯入 blobs、報表 artifacts、workspace 文件、模型 artifacts 與本機 cache 會持續累積；目前只有部分孤兒清理與 cache prune，缺少一致的 retention、pin、archive 與可恢復政策。

## What Changes

- 為暫存、正式版本、被引用與被 pin 的資料定義保留期限。
- 清理前驗證 reference、狀態與最小年齡，避免刪除仍可下載或可重現所需資料。
- 提供 dry-run、批次上限、操作摘要與失敗隔離。
- 將可重建 cache 與不可替代 artifact 分開處理。

## Capabilities

### New Capabilities

- `retention-and-archive`：定義各類資料的保留、清理、pin、dry-run 與可恢復性。

### Modified Capabilities

- `platform-runtime`：背景清理工作必須可追蹤、可重試且不影響正常工作。

## Scope

`import_blobs`、report artifacts/cache、workspace documents、model artifacts 與相應 DB reference。

## Non-goals

- 不在第一版自動刪除 core patent 或 workflow audit history。
- 不以單一 TTL 套用所有資料類型。
- 不在沒有 reference 檢查時做 recursive filesystem delete。

## Impact

可能影響 DB、物件／檔案儲存與排程；需 migration 或 maintenance job，並需明確設定預設關閉／dry-run 策略。

## Activation

需先定案 retention 參數；正式排程啟用前先跑 dry-run 與受控小批次，部署時提供可停用設定。

## Acceptance Gate

驗證未完成工作、被 pin、仍被版本引用與未達年齡資料不刪；孤兒／過期暫存會清，失敗可重跑且留下摘要。

