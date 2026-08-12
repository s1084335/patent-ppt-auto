# ai-companion（delta）

## ADDED Requirements

### Requirement: 簡報撰稿派工（ai:report_deck）

Companion SHALL 支援 `ai:report_deck` 工作：runner 負責全部確定性步驟
（取料、排頁、字級適配、閘門、組版、回存），CLI 只承擔撰稿——
輸入為 plan 與報表素材，輸出唯一檔案 `content.json`。

#### Scenario: CLI 撰稿權限面

- **WHEN** runner 派 CLI 執行撰稿
- **THEN** CLI SHALL 可讀撰稿素材、可經**唯讀 MCP 取證工具**查證個案
  （與 `ai:narrative` 同一通道；含請求項原文），SHALL 只寫 `content.json`
- **AND** SHALL 不具備 shell 執行、資料庫寫入或其他檔案寫入能力

#### Scenario: 撰稿未過閘門的重試上限

- **WHEN** 內容閘門對 CLI 輸出報紅
- **THEN** runner SHALL 將閘門輸出回饋 CLI 重撰稿至多一次
- **AND** 仍未通過 SHALL 標記任務 failed，不得無限重試
