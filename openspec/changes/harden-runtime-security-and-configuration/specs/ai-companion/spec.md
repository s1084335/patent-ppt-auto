## ADDED Requirements

### Requirement: AIC-008 持續 DB 失敗可觀察

AI Companion SHALL 在連續 DB 失敗達事前設定門檻時將 heartbeat/doctor 狀態標為 degraded，帶失敗計數與最後成功時間；恢復成功 claim/poll 後 SHALL 清除 degraded 並保留恢復事件。

#### Scenario: Companion 長時間無法連 DB
- **WHEN** 連續失敗達門檻且沒有成功 poll
- **THEN** 外部 doctor/heartbeat SHALL 可辨識「程序仍在但不能領 job」，不得只顯示 running
