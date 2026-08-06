## Purpose

為可重建暫存與不可替代的正式 artifact 建立不同保留政策，讓清理可預覽、可限制、可追蹤且不破壞仍被引用的版本。

## ADDED Requirements

### Requirement: RET-001 資料類型分級

系統 SHALL 分別定義 import blob、report artifact、local cache、workspace document、model artifact 與 audit state 的保留政策，不使用單一 TTL。

#### Scenario: 可重建 cache 過期

- **WHEN** cache 超過保留數且正式 artifact 仍存在
- **THEN** cache MAY 被清除
- **AND** 指定版本仍可由 artifact store materialize

### Requirement: RET-002 Reference 與 pin 保護

系統 SHALL 在刪除前確認資料沒有被 active job、workspace、版本、artifact metadata 或 pin 引用。

#### Scenario: 版本已被 pin

- **WHEN** retention 掃描命中被 pin 的報告版本
- **THEN** 該版本及必要子 artifact SHALL 保留

### Requirement: RET-003 Dry-run 與批次上限

系統 SHALL 預設提供 dry-run 結果，列出候選、原因、預估大小與排除項；正式清理須有批次上限。

#### Scenario: 執行 dry-run

- **WHEN** 操作者未啟用 delete mode
- **THEN** 系統 SHALL 不刪任何資料
- **AND** 回傳將會處理的精確項目

### Requirement: RET-004 清理冪等且失敗隔離

系統 SHALL 讓清理可重跑；單一項目失敗不得使其他獨立項目狀態不明。

#### Scenario: 一個 artifact 刪除失敗

- **WHEN** 批次中一個項目因 I/O 失敗
- **THEN** 結果 SHALL 記錄該項失敗
- **AND** 已成功或未處理項目的狀態 SHALL 可判斷

### Requirement: RET-005 不自動刪核心與稽核資料

系統 SHALL 不由第一版 retention 自動刪除 core patents、正式人工決策或 workflow audit history。

#### Scenario: Audit history 過期

- **WHEN** workflow audit row 超過一般暫存 TTL
- **THEN** 第一版清理 SHALL 保留該資料

