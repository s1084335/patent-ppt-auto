## ADDED Requirements

### Requirement: PRT-012 報告研究工具具雙層唯讀邊界

系統 SHALL 以獨立 report-research MCP profile 暴露唯讀工具，且其 DB connection MUST 使用只具核准 SELECT 權、預設 read-only transaction、statement timeout 與列數限制的 reader identity；現行混合讀寫 MCP token 不得授予此工作。

#### Scenario: 工具缺陷嘗試 UPDATE

- **WHEN** report-research 工具因程式缺陷送出 INSERT、UPDATE、DELETE、DDL 或具副作用函式
- **THEN** DB SHALL 拒絕操作並回滾交易
- **AND** 權限測試 SHALL 顯示正式資料未變

#### Scenario: Reader grant 漂移

- **WHEN** migration 或部署讓 reader identity 取得非核准 schema/table 的寫入或 execute 權
- **THEN** 權限守門測試 SHALL 失敗並阻止部署
