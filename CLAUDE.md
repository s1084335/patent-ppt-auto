# CLAUDE.md

@AGENTS.md

本 repo 的 agent 共用原則見 `AGENTS.md`。

---

## 首次接手這個 repo（外部協作者請先讀這段）

如果你是剛 clone 下來、要把系統跑起來的 agent，**依序做這三件事**：

1. **讀 `README.md`** —— 那是唯一的啟動說明：工具版本、Supabase 連線、
   backend／worker／companion 三個元件怎麼開、怎麼確認活著。
2. **確認 `.env` 在專案根目錄** —— 連線設定**不在本 repo 內**，由專案負責人另外提供。
   沒有它就什麼都連不上。詳見 `README.md` 第 3.2 節。
3. **照 `README.md` 第四節起系統** —— `uv sync` → 放 `.env` → `alembic upgrade head`
   → 開三個終端機（backend／worker／companion）。

⚠ **前端不需要另外啟動**：它是單一 `backend/app/static/index.html`，由 backend 直接服務，
沒有 npm、沒有建置步驟。目前版面為工程過渡版，正在重新設計。

⚠ **PPT 交付線已於 2026-08-20 停產**，最終交付物是 HTML。
若在舊文件或註解看到 PPT／deck／pptx 相關敘述，那是歷史紀錄，
不要照著實作；殘餘文件見 `archive/deprecated/ppt-delivery-line/`。

### 本 repo 原始開發環境的路徑，對你不適用

`AGENTS.md` 中多處提到 `D:\力山\.agents\`、`D:\力山\.ai-rules\` 等絕對路徑，
那是原始開發機的工作紀錄與規則位置。**你的機器上不會有這些目錄**——

- 不要嘗試建立它們，也不要因為找不到而中斷。
- 那幾條規則講的是「工作紀錄寫哪裡」，與能不能把系統跑起來無關。
- 其餘內容（OpenSpec 流程、分支與交付閘門、TDD 要求）仍然適用。

需要記工作紀錄時，改用你自己 workspace 的慣例，或直接問專案負責人。
