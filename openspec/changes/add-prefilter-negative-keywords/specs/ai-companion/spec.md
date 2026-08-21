## ADDED Requirements

### Requirement: AIC-008 關鍵字轉換為無工具的建議型任務

系統 SHALL 提供將負面關鍵字轉換為英文比對詞的 AI 任務。

該任務 SHALL 以最小權限執行：待轉換的關鍵字 SHALL 內嵌於指令中，任務 SHALL NOT
取得檔案、資料庫或網路工具。

該任務的輸出 SHALL 為建議草稿，SHALL NOT 直接成為生效的比對詞。

#### Scenario: 任務不取得任何工具

- **WHEN** 系統為關鍵字轉換任務組裝執行指令
- **THEN** 指令 SHALL NOT 授予檔案、資料庫或網路工具

#### Scenario: 輸出為草稿

- **WHEN** 關鍵字轉換任務成功結束
- **THEN** 產出的比對詞 SHALL 標記為未確認
- **AND** 未確認的比對詞 SHALL NOT 被比對作業採用

#### Scenario: 任務失敗不阻斷篩選

- **WHEN** 關鍵字轉換任務失敗
- **THEN** 系統 SHALL 明確回報失敗原因
- **AND** 使用者 SHALL 仍可自行輸入比對詞完成篩選

### Requirement: AIC-009 AI 工作類型的註冊落點必須一致

AI 工作類型的白名單、執行器派發表與工具權限政策 SHALL 對同一組工作類型保持一致。

任一落點新增或移除工作類型而其餘落點未同步時，系統 SHALL 於自動化檢查中失敗，
SHALL NOT 於執行期才顯現。

#### Scenario: 新增工作類型未同步派發表

- **WHEN** 工作類型白名單新增一個型別，但執行器派發表未新增對應項
- **THEN** 自動化檢查 SHALL 失敗

#### Scenario: 移除工作類型未同步權限政策

- **WHEN** 工作類型自白名單移除，但工具權限政策仍列出該型別
- **THEN** 自動化檢查 SHALL 失敗
