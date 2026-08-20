# 專利分析平台（patent-ppt-auto）

把 WIPS 匯出的專利資料，變成一份**可交付的 HTML 分析報告**。

```
匯入 WIPS 檔 → 正規化／歸戶 → 向量化 → 分群出技術／功效主題
            → 產製報表（圖＋數據表）→ AI 解讀每張圖 → 匯出自包單檔 HTML
```

> **交付物就是那份 HTML。** PPT 交付線已於 2026-08-20 停產，
> 前端「匯出報告」工作台整塊移除，殘餘文件見 `archive/deprecated/ppt-delivery-line/`。
> **HTML 的版面正在重新設計中**——目前的樣式是工程過渡版，不是最終外觀。

---

## 一、你需要跑起來的三個東西

| 元件 | 是什麼 | 沒有它會怎樣 |
|---|---|---|
| **backend** | FastAPI，提供 Web UI 與 REST API | 什麼都打不開 |
| **worker** | 跑重活：匯入、向量化、分群、產報表 | 按下去的任務永遠停在 queued |
| **companion**（AI bridge） | 取 AI 任務 → driving 本機 Claude Code CLI → 回存結果 | 其餘都正常，只有「AI 解讀」不會動 |

三者都連同一個 PostgreSQL（**本專案用 Supabase**）。任務靠 DB 佇列傳遞，
彼此不直接呼叫——所以可以只開 backend + worker 先驗證，companion 之後再說。

⚠ **companion 只有 Windows**（走工作排程器，因為它必須以「你這個使用者」的身分執行，
才拿得到你自己的 Claude CLI 登入狀態）。Linux／macOS 可以手動跑同一支 Python 模組。

---

## 二、工具與版本

### 必要

| 工具 | 版本 | 說明 |
|---|---|---|
| **Python** | **3.12**（`pyproject` 要求 `>=3.11`；正式容器用 `3.12.13-slim-bookworm`） | 3.13+ 未驗證過，請用 3.12 |
| **uv** | **0.11.27**（正式容器同版） | 套件管理與執行器；本專案**不用** pip／poetry |
| **Git** | 任意近版 | |
| **PostgreSQL** | 連 Supabase 者**不必自己裝**（實測該實例為 **17.6**，pgvector 已內建） | 自架時用 `pgvector/pgvector:0.8.5-pg18-trixie`（PG 18 ＋ pgvector 0.8.5） |

安裝 uv（Windows PowerShell）：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv --version   # 應顯示 0.11.27 以上
```

### 依用途才需要

| 工具 | 版本 | 什麼時候需要 |
|---|---|---|
| **Claude Code CLI** | 近版，且已 `claude login` | 只有 companion（AI 解讀）需要 |
| **Docker Desktop** | 含 Compose v2 | 只有走「全 Docker」路線時需要 |
| **NVIDIA driver + CUDA** | 支援 cu130 的驅動 | 只有要用 GPU 加速向量化時需要；CPU 也能跑，只是慢 |

⚠ `torch` 由 `pyproject` 綁 **cu130** index（Linux／Windows）。沒有 NVIDIA GPU 也裝得起來、
也跑得動，只是 embedding 會走 CPU。

### 資料庫以外的外部相依

- **PatentSBERTa 模型權重（405 MB）**：不進版控，worker 第一次啟動時自動下載，
  來源是本 repo 的 GitHub Release `patentsberta-v1`（公開可下載）。
- **HuggingFace Token**（選用）：只有分群候選說明／主題標籤走 HF router 時需要。

---

## 三、資料庫：連到 Supabase

### 3.1 取得連線資訊

Supabase 主控台 → 你的專案 → **Connect** → 取 **Transaction pooler** 連線字串，
形如：

```
postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:<port>/postgres?sslmode=require
```

Supabase 有三個連線位址，本專案走 **pooler**：

| 位址 | port | 說明 |
|---|---|---|
| `db.<ref>.supabase.co` | 5432 | direct connection，**本專案不用** |
| `aws-0-<region>.pooler.supabase.com` | **5432** | **session pooler** |
| `aws-0-<region>.pooler.supabase.com` | **6543** | transaction pooler |

⚠ user 是 `postgres.<project-ref>`（含專案 ref），不是單純 `postgres`。`sslmode=require` 必填。

### 3.2 取得 `.env`

**連線設定不放在這個 repo 裡，由專案負責人另外給你一份 `.env`。**

拿到後放在**專案根目錄**（與 `pyproject.toml` 同層），檔名就叫 `.env`，不要改名、
不要加副檔名。裡面已經填好可直接使用的值，你不需要自己拼連線字串、也不需要註冊
Supabase 帳號。

該檔包含：

| 變數 | 用途 | 沒有會怎樣 |
|---|---|---|
| `DATABASE_URL` | Supabase 連線字串（優先於下列 `PG*`） | 什麼都連不上 |
| `PGHOST`／`PGPORT`／`PGDATABASE`／`PGUSER`／`PGPASSWORD` | 同上的個別欄位，供工具鏈使用 | — |
| `PATENT_API_TOKEN` | 守 AI 任務端點的 bearer token | `/api/v1/ai-tasks*` 一律回 **503**（fail closed，不是壞掉）；Web UI 的「AI 任務金鑰」欄位要貼同一個值 |
| `CLUSTERING_LLM_*`／`HF_TOKEN` | 分群候選說明與主題標籤用的 LLM | 分群本身照跑，只是沒有 AI 產的說明文字 |
| `ANTHROPIC_API_KEY` | 正式切換 Claude 供應商時才用，目前留空 | — |

⚠ **`.env` 絕對不要 commit**（`.gitignore` 已擋，別用 `-f` 硬加）。
⚠ 值不要加引號；`DATABASE_URL` 結尾 `require` 後不可有空白或換行，
否則會報 `invalid sslmode value`。

⚠ **這組連的是共用的正式資料庫，不是沙箱。** 匯入／分群會動到真實資料，
測試性質的操作請先跟專案負責人確認。

#### port 該用 5432 還是 6543？

目前 `.env` 用的是 **6543（transaction pooler）**，日常查詢與報表都正常。

⚠ 但實測記錄指出 **6543 在大量寫入時會斷線**（例如匯入大批專利）。遇到匯入中途斷開，
把 `DATABASE_URL` 與 `PGPORT` 的 `6543` 改成 **5432（session pooler）** 再試一次。
兩個 port 的帳號密碼相同，只有連線模式不同。

### 3.3 兩個會讓你查很久的坑

**坑一：`search_path` 少了 `extensions` → embeddings 整條炸**

pgvector 在 Supabase 裝在 `extensions` schema。少了它，`vector` 型別找不到，
寫入 embeddings 會 `UndefinedObject`，而**你看到的錯誤訊息卻是下游的**
「no patents with reusable embeddings」——完全聯想不到根因。

程式已有預設值（`backend/app/db/connection.py::_DEFAULT_PG_OPTIONS`）：

```
-c search_path=core_layer,raw_layer,public,extensions
```

⚠ 那是**預設值不是 fallback**：psycopg 的 `options` 會**覆蓋** DB 端設定。
若你自行設 `PGOPTIONS`，務必把 `extensions` 帶著。

**坑二：transaction pooler 忽略連線層 startup options**

6543 的 pooler 會**忽略**連線字串裡的 `-c` 參數——實測 `statement_timeout` 沒作用、
唯讀設定也沒作用。所以程式改成把限制綁在**交易**上
（`SET TRANSACTION READ ONLY`／`SET LOCAL`），pooler 換後端連線也帶得過去。
你自己寫查詢腳本時要注意同一件事。

### 3.4 建立 schema

```powershell
uv run alembic upgrade head
```

54 個 migration，會建出 `raw_layer` / `core_layer` / `derived_layer` / `app_layer` 四層 schema。
重跑安全（冪等）。

⚠ 這個 Supabase 已經是**跑過的正式庫**，schema 通常已在 head——這一步多半只會顯示
「已是最新」。它的用途是確認你連得上、且版本對得起來。

---

## 四、起一套臨時系統

### 路線 A：Supabase ＋ 本機 Python（推薦，最快）

```powershell
git clone https://github.com/s1084335/patent-ppt-auto.git
cd patent-ppt-auto

uv sync                       # 建 .venv 並依 uv.lock 裝套件（鎖定版本，不會漂）
# 把專案負責人給你的 .env 放進專案根目錄（見 3.2 節）
uv run alembic upgrade head   # 確認連得上、schema 版本對得起來
```

開**三個**終端機（各自常駐，不要關）：

```powershell
# 1) backend（同時服務 API 與前端頁面）
uv run python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# 2) worker（匯入／向量化／分群／產報表）
uv run python -m backend.app.worker.runner serve --poll-seconds 3 --log-level INFO

# 3) companion（AI 解讀；需要已登入的 Claude Code CLI）
uv run python -m backend.app.worker.ai_bridge serve --poll-seconds 3 `
    --stale-after-seconds 1800 --log-level INFO --log-file var\ai_bridge.log
```

開瀏覽器 → <http://127.0.0.1:8000/>

**前端不需要另外啟動、也沒有建置步驟。** 它是單一 `backend/app/static/index.html`，
由 backend 直接服務——沒有 npm、沒有 node_modules、沒有 dev server。
改了檔案重整瀏覽器就看得到。

⚠ 這份前端是**工程過渡版**，版面正在重新設計中，不是最終外觀。

⚠ worker 第一次啟動會下載 PatentSBERTa（405 MB），需要幾分鐘且需要 repo 存取權。

### 路線 B：全 Docker（含自架 PostgreSQL）

```powershell
copy .env.example .env    # 用預設的本機 postgres 設定即可，不必改 Supabase
docker compose up -d --build
docker compose exec backend python -m alembic upgrade head
```

服務：`postgres`（127.0.0.1:5433）、`backend`（127.0.0.1:8000）、`worker`。
GPU 另加 `-f docker-compose.gpu.yml`；要接 Supabase 用 `docker-compose.supabase.yml`。

⚠ companion **不在 compose 裡**——它必須跑在使用者自己的機器上（要拿本機 Claude CLI 的登入狀態）。

### companion 註冊為開機常駐（Windows，選用）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\companion_install.ps1
# 移除：scripts\companion_uninstall.ps1
```

用工作排程器而非 Windows 服務：服務跑 LocalSystem，拿不到你的 CLI 登入狀態。
腳本冪等，重跑會覆蓋同名工作。日誌落 `var\ai_bridge.log`（輪替，5 MB × 5 份）。

---

## 五、確認真的活著

| 檢查 | 怎麼做 | 應該看到 |
|---|---|---|
| 連線 | `uv run python -c "import os,psycopg;print(psycopg.connect(os.environ['DATABASE_URL']).execute('select version()').fetchone())"` | `PostgreSQL 17.6 …`（該 Supabase 實例的實測版本） |
| backend | 開 <http://127.0.0.1:8000/api/v1/ready> | JSON，DB 連線 ok |
| DB | `uv run alembic current` | 顯示 head revision |
| worker | 看第 2 個終端機 | 每 3 秒輪詢，無例外堆疊 |
| companion | `var\ai_bridge_heartbeat.json` 的時間戳在更新 | 持續前進 |
| 端到端 | Web UI 匯入一個 WIPS 檔 → 等分群 → 產報表 → 匯出 HTML | 下載到單一 `.html`，離線可開、圖都在 |

⚠ **repo 裡沒有專利資料**（`data/raw/` 不進版控）。要驗完整流程得自己匯入一份 WIPS 匯出檔。

---

## 六、目錄結構

```
backend/app/
  api/          REST 端點
  main.py       FastAPI 進入點
  static/       前端（單一 index.html，無建置步驟、無 npm）
  worker/       runner（重活）、ai_bridge（companion）、prompts/（AI 解讀規格）
  reports/      報表引擎：chart_runner 產圖與 index.html
  clustering/   向量化與分群
  importers/    WIPS 匯入
  mcp_server/   給 Claude Code 的唯讀取證 MCP 工具
  db/           連線與 job queue
alembic/versions/   54 個 migration
openspec/           規格與變更管理（specs＝現行能力，changes＝進行中）
scripts/            companion 安裝、模組驗證等
docs/               架構參考與 runbook
archive/deprecated/ 已退場模組（含 PPT 交付線）
```

前端是**單一 `index.html`**，沒有打包工具、沒有 `node_modules`。改了直接重整就看得到。

---

## 七、開發

```powershell
uv run pytest tests -q                      # 全部
uv run pytest tests/test_api_frontend.py -q # 單檔
uv run python scripts/verify_module.py      # 模組驗證
```

規格與變更流程走 OpenSpec（`openspec/`）。改行為前先看 `AGENTS.md` 與 `openspec/config.yaml`。

---

## 八、已知前置條件

| 事項 | 影響 |
|---|---|
| **沒拿到 `.env`** | 連不上資料庫，什麼都跑不起來。向專案負責人索取（見 3.2） |
| PatentSBERTa 未就位 | 向量化／分群跑不動；worker 啟動時會自動下載（405 MB，第一次要等幾分鐘） |
| DB 是**共用正式庫** | 匯入／分群會動到真實資料，不是沙箱 |
| transaction pooler（6543） | 大量寫入時可能斷線，改用 5432 session pooler |
| companion 非 Windows | 排程腳本不適用，改手動跑 `ai_bridge serve` |
| Claude Code CLI 未登入 | 只有 AI 解讀不會動，其餘功能正常 |
| HTML 版面 | 目前為工程過渡版，正在重新設計 |
