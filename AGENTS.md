# Agent 工作原則（本 repo 專屬）

適用 Claude Code、Codex、OpenCode 等所有 AI agent，不論以哪個目錄為 workspace root 開 session。

- 工作紀錄與長期 context 一律記到 `D:\力山\.agents\`：
  - 當日紀錄：`D:\力山\.agents\work-logs\專利_ppt自動\{日期}.md`（三 agent 共用同一檔，依線分節）。
  - 穩定背景：`D:\力山\.agents\context\`（路由見該目錄 `README.md`）。
- 本 repo 內**不建立** `.agents/` 目錄；若發現殘留，先併回中央再移除。
- 全域規則唯一來源：`D:\力山\.ai-rules\`（shared.md 為主）。

## 精準 TDD（本專案試行，自 2026-07-21）

- Red → 最小 Green → **必要時才** Refactor；不是每輪都硬做第三步。
- Red 必須真實執行並如實記錄失敗原因；不得先改檔後補造 Red。
- 錯誤修正先建 regression test；migration 先建契約測試。
- 每個增量先設停止點，達成即停止回報，等驗收再進下一段。

## Token 節制（本專案試行，自 2026-07-21）

- 小型**已定位**任務以約 3,000 output tokens 為軟目標；migration、跨表、資料安全相關工作以**安全完成優先，不設硬上限**。
- 只削減四樣：全庫掃描、重複讀檔、長 traceback、冗長回報。
- **不得削減**：必要契約測試、資料搬移驗證、downgrade 驗證。
- 同一失敗最多修兩輪，仍失敗即停止並回報阻塞。

（試行條款：先只作本專案層級規則；跑一段時間驗收後再決定是否升格 `D:\力山\.ai-rules\`。）
