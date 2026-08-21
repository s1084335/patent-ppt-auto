# Deprecated Modules

本目錄集中保存已退出正式路線、但短期仍需要追溯的舊模組。

- `report-llm-agent/`：後端內建報表 LLM 問答 agent。新路線改由 Claude Code skill 或受控 LLM task service 調用報表資料。
- `clustering-dimension-compare/`：PCA 維度比較開發工具。分群維度已定案為 `IncrementalPCA=100D`，正式流程不再依賴此 runner。
- `ppt-delivery-line/`：PPT 交付線殘餘文件。2026-08-20 定案不再產出 PPT，最終交付檔案為 HTML；前端「匯出報告」工作台同日整塊移除。
- `deck-delivery-line/`：deck（簡報）交付線。同一個停產定案的**第二段**——deck 是後來另起、想取代舊 PPT 線的新線，2026-08-21 自主線移除。含敘事品質標準（其中 16 項 HTML 線沒有，可評估回收）與抓回方法；程式碼在 tag `archive/2026-08-21/deck-line-at-merge`。

廢棄目錄內檔案不得被正式後端、正式測試或部署流程 import。若需要恢復，先提出原因、影響範圍與驗證方式，再移回正式模組。
