## Context

`load_delimited_rows` 目前讀完整文字後 split，`csv.Sniffer` 可能誤判；XML 亦以完整樹解析。欄名 mapping、編碼 fallback、錯誤摘要與 importer 後段不得被重寫。

## Goals / Non-Goals

**Goals:** 正確處理引號換行與 delimiter、阻擋 XXE、降低大檔峰值記憶體，保持 WIPS 批次相容。

**Non-Goals:** 不改 xlsx 圖像流程、不新增格式、不改 DB schema。

## Decisions

1. **Delimited 使用 file-like streaming + `csv.reader/DictReader`。** 不先自行切行。
2. **Sniffer 只作候選。** 以欄數、專利 marker 與副檔名預設驗證，不接受無法形成合理 header 的猜測。
3. **XML 使用安全 iterparse。** 禁用／拒絕 DTD 外部實體，完成節點即 clear。
4. **錯誤要 fail clear。** 不把解析失敗降級成一欄大量錯資料。

## 程式與測試落點

- `backend/app/importers/wips_importer.py`
- `tests/test_import_format_fixes.py`
- `tests/test_wips_import_flow.py`
- `tests/test_wips_importer_0019_0021.py`

測試矩陣包含 UTF-8/BOM/Big5 fallback、comma/tab、quoted newline、escaped quote、空列、錯 delimiter、namespace XML、惡意 entity、零筆與大型 fixture。

## 輸出契約

Loader 仍回傳 headers、sheet/source name、records、warnings；warnings 必須指出 fallback，fatal parse error 不產生可匯入 records。

## Risks / Trade-offs

- [串流改變列號] → fixture 鎖來源列與錯誤定位。
- [過嚴 validation 拒絕合法異體] → 只驗結構與 patent marker，不寫死單一欄名全集。
- [效能測試不穩] → 驗證 streaming 行為與上限，不用脆弱絕對秒數。

## Migration Plan

無 DB migration。先建立 regression Red，再逐格式替換 reader；通過目標與匯入範圍回歸後部署 worker image。已錯匯資料不自動修復，需另行識別與重匯。

