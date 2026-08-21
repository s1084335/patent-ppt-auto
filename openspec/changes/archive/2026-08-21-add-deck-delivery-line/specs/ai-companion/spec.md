# ai-companion（delta）

## ADDED Requirements

### Requirement: 簡報撰稿派工（ai:report_deck）

Companion SHALL 支援 `ai:report_deck` 工作：runner 負責全部確定性步驟
（取料、排頁、字級適配、閘門、組版、回存），CLI 只承擔撰稿——
輸入為 plan 與報表素材，輸出唯一檔案 `content.json`。

#### Scenario: CLI 撰稿權限面

- **WHEN** runner 派 CLI 執行撰稿與目視
- **THEN** CLI SHALL 可讀撰稿素材與逐頁截圖、可經**唯讀 MCP 取證工具**查證
  個案（與 `ai:narrative` 同一通道；含請求項原文），SHALL 只寫 `content.json`
- **AND** SHALL 不具備 shell 執行、資料庫寫入或其他檔案寫入能力

#### Scenario: 目視迴圈（看了回去調）

- **WHEN** 組版完成產出逐頁截圖
- **THEN** CLI SHALL 依既有目視檢查清單逐頁檢視（不得抽樣）
- **AND** 發現問題時 SHALL 以修改 `content.json` 回應（縮寫、改寫、拆頁），
  runner SHALL 重組版重截圖供再次檢視
- **AND** 同一問題的修正 SHALL 以兩輪為上限，仍未通過 SHALL 標記任務
  failed 並附最後一輪目視發現；內容閘門報紅 SHALL 走同一迴圈
