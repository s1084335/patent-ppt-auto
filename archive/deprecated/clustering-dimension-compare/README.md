# Clustering Dimension Compare

此目錄保存舊的分群維度比較工具。

退役原因：
- 正式分群空間已定案為 `IncrementalPCA=100D`。
- 正式產品不再比較 `768D`、`PCA100D`、`PCA50D` 三種空間。
- 正式分群流程應走 `backend/app/clustering/runner.py`、`model.py`、`db_writer.py` 與 workspace service。

保留內容：
- `dimension_compare_runner.py`：早期用 Excel / cache 測試降維方案的開發 runner。

若未來要重新評估維度，應另開實驗，不直接讓正式測試或正式後端引用本目錄檔案。
