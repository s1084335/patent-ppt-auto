## Why

應用在 `DATABASE_URL` 缺失時仍可能靜默連向 `localhost:5433`，AI API token 未設定時會放行，而前端 `aiAuthHeaders()` 尚無正式 token 注入；Companion 連續 DB 失敗只寫 log/heartbeat。這些行為在部署環境會把設定錯誤偽裝成程式故障，並可能暴露可消耗 AI 額度的端點。

## What Changes

- 依 runtime role 區分本機開發與部署：部署 role 缺必要 DB、AI auth 或 secret 時啟動即 fail-fast。
- AI endpoint 預設採受保護模式；前端以不落 localStorage、不洩漏 log 的方式取得並注入 credential，或由受信反向代理完成認證。
- readiness 回報 DB、artifact、worker/Companion 依賴狀態，但遮罩敏感值。
- Companion 連續 DB 失敗達門檻時將 heartbeat 標成 degraded 並提供可接告警的明確輸出；恢復後自動清除 degraded。
- 保留明確 opt-in 的 local development 模式，不能由缺少設定意外觸發。
- 將 goal-driven report planning 的 read-only MCP/DB reader 視為獨立 deployment role：缺 reader credential、tool profile 或權限驗證時 fail-fast，不得改用一般應用 DB credential。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `platform-runtime`: 增加角色感知設定驗證、AI endpoint 保護、readiness 與 degraded 狀態契約。
- `ai-companion`: 增加安全 credential 使用與持續 DB 失敗可觀察性。

## Scope

處理應用、Companion 與 report-research MCP 的啟動設定、AI API 認證與依賴健康；MCP HTTP 既有 bearer transport 不重做，唯讀工具內容與 DB grants 由 `enable-goal-driven-readonly-report-planning` 實作。

## Non-goals

- 不在本 change 導入完整企業 IAM/SSO。
- 不把 secret 寫進前端 bundle、repo、URL query 或一般 log。
- 不取消本機無 token 的開發能力，但必須明確啟用。
- 不把現有混合讀寫 MCP 直接降權後交給報告 CLI，也不在本 change 重複定義 report evidence tools。

## Impact

- `db/connection.py`、FastAPI/MCP startup/readiness、AI auth dependency、前端認證入口、Companion heartbeat、reader credential validation 與部署設定。
- 現有未設 token 的環境在正式模式會成為 breaking deployment requirement，需先補 secret/config。

## Activation

先提供設定檢查與 audit 模式，再逐環境啟用 enforced mode；部署前列出 required variables，並驗證 reverse proxy／前端 credential 路徑。

## Acceptance Gate

以決策表驗證 local/deployment × runtime role × DB/reader credential/token × dependency health；確認錯誤在啟動或 readiness 明確現形、AI 未授權回 401/403、report-research 不得 fallback 到一般 DB identity、secret 不出現在產物與 log、Companion degraded/recovery 可觀察。
