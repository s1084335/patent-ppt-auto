# ai-companion（delta）

## ADDED Requirements

### Requirement: 解讀指引與報表定義保持同步

系統 SHALL 確保解讀指引文件所引用的報表，都存在於報表定義中；報表退場、
合併或改名後，指引 SHALL 一併更新。

#### Scenario: report_key 集合對帳

- **WHEN** 執行一致性檢查
- **THEN** 指引文件提到的每個 report_key SHALL 存在於報表定義
- **AND** 指引提到但定義中不存在時，檢查 SHALL 失敗
- **AND** 定義中存在但指引未涵蓋時，SHALL 列出提醒但不使檢查失敗

#### Scenario: 指引不複述報表結構

- **WHEN** 撰寫或修改解讀指引
- **THEN** 指引 SHALL 說明解讀重點與禁止超譯的範圍
- **AND** SHALL NOT 複述欄位清單或呈現形式（那屬報表定義的職責）

### Requirement: 文件權責邊界

系統 SHALL 為描述報表的各份文件定義單一職責，避免同一份知識散落多處：
報表定義負責存在性與欄位、解讀指引負責解讀重點、內容標準負責跨報表寫作規範、
簡報技能文件負責投影片呈現、交付線設計文件負責頁面盤點。

#### Scenario: 新增報表時的文件更新範圍

- **WHEN** 新增或改版一張報表
- **THEN** SHALL 依權責邊界判斷需更新哪幾份文件
- **AND** 一致性檢查 SHALL 能指出未更新的指引

## MODIFIED Requirements

### Requirement: 解讀取證的資料來源

解讀端 SHALL 透過報表列提供的內部識別碼自行取證，資料層 SHALL NOT 預先計算
敘述內容餵給解讀端。

#### Scenario: 設計保護標的由解讀端撰寫

- **WHEN** 解讀端撰寫設計保護策略的標的描述
- **THEN** SHALL 以報表列的設計專利識別碼查詢文獻備註
- **AND** SHALL 讀懂後改寫成可讀敘述，SHALL NOT 照抄整段
- **AND** 資料層 SHALL NOT 在報表層預先抽取或摘要該內容

#### Scenario: 家族判讀由解讀端取證

- **WHEN** 解讀端判斷申請成長屬真實新增或同族延伸
- **THEN** SHALL 依同族識別碼分組取證
- **AND** SHALL NOT 依賴數據表提供家族數欄位
