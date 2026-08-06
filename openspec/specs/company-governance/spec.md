# Company Governance Specification

## Purpose

定義申請人／專利權人代碼歸戶、名稱收斂、中文名確認與顯示優先序，讓 importer、derived、API 與報表共用一致公司身分，同時保留來源原文、AI 草稿與人工正式值之間的追溯邊界。

## Requirements

### Requirement: CMP-001 有代碼依代碼歸戶

系統 SHALL 將具有 WIPS 公司代碼的名稱依代碼歸戶；未建組代碼可自動建立待確認群組，但不得自動填入臆測中文名。

#### Scenario: 新代碼首次出現

- **WHEN** 匯入資料含尚未建組的公司代碼
- **THEN** 系統 SHALL 建立 `review_required` 群組
- **AND** 中文名保持空白

### Requirement: CMP-002 無代碼不做模糊自動寫入

系統 SHALL 只對完全命中的無代碼名稱自動歸戶；疑似相近名稱只能提示人工處理，不得以相似度直接改正式資料。

#### Scenario: 名稱只有部分相似

- **WHEN** 新名稱與既有公司名稱僅部分相似
- **THEN** 系統 SHALL 不自動加入既有群組

### Requirement: CMP-003 AI 中文名先草稿後確認

系統 SHALL 將 AI 產生的公司中文名保存為草稿，只有使用者 confirm 或 edit 後才可成為正式顯示名稱；reject 不得污染正式顯示。

#### Scenario: AI 草稿尚未確認

- **WHEN** 中文名草稿已產生但尚未確認
- **THEN** 專利列表與報表 SHALL 不使用該草稿作正式顯示名

### Requirement: CMP-004 顯示名稱優先序

系統 SHALL 依「已確認中文名、正規化收斂名、標準化名、來源原文」的順序產生顯示名稱，並保留原文欄位。

#### Scenario: 已確認中文名存在

- **WHEN** 公司群組有 confirmed 中文名
- **THEN** derived、列表與報表 SHALL 優先顯示中文名
- **AND** 原始申請人／專利權人字面仍可查詢

### Requirement: CMP-005 維護操作可追溯

系統 SHALL 提供待確認、既有群組、未歸戶名稱、變體、promote、edit 與 delete 等維護操作，並在改動後刷新受影響 projection。

#### Scenario: 確認或編輯群組

- **WHEN** 使用者完成公司治理寫入
- **THEN** 系統 SHALL 觸發或要求 derived refresh
- **AND** 後續顯示使用更新後的唯一收斂結果
