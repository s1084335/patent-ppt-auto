## 1. 模式與威脅基準

- [ ] 1.1 盤點 backend/worker/Companion/report-research MCP/local/installer 的實際啟動入口、環境變數、AI write endpoints、reader credential 與 secret 流向
- [ ] 1.2 事前固定 local/deployment × role × DB/reader credential/token × dependency health 決策表及 degraded 門檻
- [ ] 1.3 記錄現行缺設定 fallback、未授權 AI request 與 Companion 長期 DB failure 的確認性基準

## 2. TDD：設定與 API 認證

- [ ] 2.1 Red：新增 deployment role 缺 DB/secret、report-research 缺 reader credential／誤用一般 DB identity、local explicit mode、非法 mode 與 redacted error 測試
- [ ] 2.2 Green：建立集中 runtime validator 並在各 entrypoint fail-fast；report-research 不得 credential fallback，不改明確 local mode
- [ ] 2.3 Red：新增所有 AI write endpoint 的無 token、錯 token、有效 token／proxy identity 與 job 未建立測試
- [ ] 2.4 Green：完成 server-side auth 單一 enforcement 與前端安全 credential injection，不使用 URL/localStorage

## 3. TDD：Readiness 與 Companion

- [ ] 3.1 Red：新增 liveness/readiness 分離、DB DNS/connection/artifact failure 與 secret redaction 測試
- [ ] 3.2 Green：完成 role-aware readiness/degraded response
- [ ] 3.3 Red：新增 Companion 門檻前 retry、門檻後 degraded、last success 與 recovery event 測試
- [ ] 3.4 Green：完成 heartbeat/doctor 狀態與告警可消費輸出
- [ ] 3.5 Refactor：全綠後去除 entrypoint 重複驗證與狀態計算，保持設定唯一來源

## 4. 部署驗收

- [ ] 4.1 執行 connection/settings/API/auth/health/Companion 目標測試、回歸、secret scan 與 `scripts/verify_module.py`
- [ ] 4.2 staging 先以 audit mode 列缺項，再逐角色 enforce；驗證未授權 401/403、缺 DB 不啟動、report-research reader identity 不 fallback、斷線 degraded、恢復清除
- [ ] 4.3 提交設定矩陣、遮罩 log/readiness 與 rollback；使用者核准前不得在正式環境強制切換或 archive
