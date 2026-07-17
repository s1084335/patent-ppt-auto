# Clustering LLM Adapter Deprecated

這裡封存原本由 Python 直接呼叫 Hugging Face / LLM API 的分群標籤與摘要 adapter。

目前正式方向已調整為：

- 分群模組只負責計算、儲存、查詢與輸出結構化 payload。
- Claude CLI / Claude Code 透過後續 MCP tools 讀取 payload，產生候選方案說明、topic label 與 summary。
- 分群模組再透過 `apply-topic-labels` 或 API 回寫 Claude CLI 產出的 label/summary。

因此這份 adapter 不再屬於正式執行路徑，只保留作為開發歷史與 fallback 參考。
