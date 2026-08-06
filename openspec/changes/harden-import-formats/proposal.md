## Why

現行 CSV/TXT/XML 解析仍有引號內換行、delimiter 猜錯、XML 安全與大檔全載風險。這些問題會造成資料列錯位、記憶體膨脹或不安全解析。

## What Changes

- 修正 CSV 引號內換行與 delimiter 偵測 fallback。
- 保留 TXT/CSV 編碼 fallback 與不同 WIPS 批次相容性。
- 加入 XML 外部實體防護並改為串流解析。
- 以逐列／逐節點處理降低大檔記憶體占用。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `patent-ingestion`：修改 CSV/TXT/XML 的解析正確性、安全與大檔行為。

## Scope

`wips_importer.py` 的 delimited/XML reader、格式偵測與相關匯入測試。

## Non-goals

- 不在本 change 重做 xlsx 圖像抽取。
- 不改 Web 格式白名單或新增新檔案格式。

## Impact

影響匯入列數、欄位對齊、錯誤訊息與記憶體使用；需用多編碼、多 delimiter、引號換行、惡意 XML 與大檔 fixture 回歸。

## Activation

純程式變更需部署 backend/worker image；不需 schema migration，既有錯匯資料若存在需另案重匯。

## Acceptance Gate

先以 regression tests 真實重現兩個 CSV bug，再完成最小 Green；格式矩陣與範圍回歸通過後才能驗收。

