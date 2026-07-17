# Deprecated Modules

本目錄集中保存已退出正式路線、但短期仍需要追溯的舊模組。

- `report-llm-agent/`：後端內建報表 LLM 問答 agent。新路線改由 Claude Code skill 或受控 LLM task service 調用報表資料。
- `clustering-dimension-compare/`：PCA 維度比較開發工具。分群維度已定案為 `IncrementalPCA=100D`，正式流程不再依賴此 runner。

廢棄目錄內檔案不得被正式後端、正式測試或部署流程 import。若需要恢復，先提出原因、影響範圍與驗證方式，再移回正式模組。
