# report-export（delta）

## ADDED Requirements

### Requirement: 簡報（deck）產製與回存

系統 SHALL 提供由報表版本產製簡報（PPTX）的能力：自報表種類頁版本區觸發、
經佇列派工執行既有 deck 流程；產物回存 SHALL 為「DB 紀錄＋NAS 檔案」，
不提供自動下載到使用者本機。

#### Scenario: 由版本產製簡報

- **WHEN** 使用者於版本區按「產製簡報」
- **THEN** 系統 SHALL 建立 `ai:report_deck` 任務並顯示進度
- **AND** 完成後 pptx SHALL 位於 deck artifact root（環境變數解析，DB 只存
  相對 key），DB SHALL 有 manifest（based_on_version、SHA-256、閘門摘要）
- **AND** 撰稿取證 audit 與目視迴圈紀錄 SHALL 隨 deck 紀錄回存
  （與 narrative 線同格式，可回放每段內容的證據鏈與修正輪次）
- **AND** 版本區 deck 紀錄 SHALL 不需手動重新整理即出現

#### Scenario: 先看到成品，使用者決定何時下載

- **WHEN** deck 產製完成
- **THEN** 前端 SHALL 先呈現**逐頁預覽**（產線目視同一批 PNG），不自動下載
- **AND** 使用者按「下載」時 SHALL 取得 pptx 檔（backend 自 artifact root
  串流該檔），下載與否、何時下載由使用者決定

#### Scenario: 閘門未過不落成品

- **WHEN** 內容閘門（check_content／組版裕度／audit）任一未通過且重撰稿一次仍未過
- **THEN** 任務 SHALL 標記 failed 並附原因
- **AND** artifact root SHALL 不出現該次的任何半成品檔
