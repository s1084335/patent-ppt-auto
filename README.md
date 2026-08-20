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
| **PostgreSQL** | **18 ＋ pgvector 0.8.5** | Supabase 已內建；自架時用 `pgvector/pgvector:0.8.5-pg18-trixie` |

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
  來源是本 repo 的 GitHub Release `patentsberta-v1`。
  ⚠ **這個 repo 是 private**——你必須先被加為 collaborator，否則下載會 404。
- **HuggingFace Token**（選用）：只有分群候選說明／主題標籤走 HF router 時需要。

---

## 三、資料庫：連到 Supabase

### 3.1 取得連線資訊

Supabase 主控台 → 你的專案 → **Connect** → 取 **Transaction pooler** 連線字串，
形如：

```
postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
```

| 欄位 | 值 | 注意 |
|---|---|---|
| host | `aws-0-<region>.pooler.supabase.com` | |
| port | **6543**（transaction pooler） | 5432 是 direct connection，本專案用 6543 |
| database | `postgres` | |
| user | `postgres.<project-ref>` | ⚠ 含專案 ref，不是單純 `postgres` |
| sslmode | `require` | 必填 |

### 3.2 寫進 `.env`

在專案根目錄 `cp .env.example .env`，然後填：

```dotenv
# 連線字串優先於 PG* 個別變數（見 backend/app/db/connection.py::get_database_url）
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require

PGHOST=aws-0-<region>.pooler.supabase.com
PGPORT=6543
PGDATABASE=postgres
PGUSER=postgres.<ref>
PGPASSWORD=<password>

# 必填：守 AI 任務端點的 bearer token。留空會讓 /api/v1/ai-tasks* 一律回 503（fail closed）。
# 產生方式：python -c "import secrets; print(secrets.token_urlsafe(32))"
PATENT_API_TOKEN=<隨機長字串>
```

⚠ `.env` **不進版控**（`.gitignore` 已擋）。不要把它 commit 上去。

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

---

## 四、起一套臨時系統

### 路線 A：Supabase ＋ 本機 Python（推薦，最快）

```powershell
git clone https://github.com/s1084335/patent-ppt-auto.git
cd patent-ppt-auto

uv sync                       # 建 .venv 並依 uv.lock 裝套件（鎖定版本，不會漂）
copy .env.example .env        # 然後照第三節填 Supabase 連線與 PATENT_API_TOKEN
uv run alembic upgrade head   # 建 schema
```

開**三個**終端機（各自常駐，不要關）：

```powershell
# 1) backend
uv run python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# 2) worker（匯入／分群／報表）
uv run python -m backend.app.worker.runner serve --poll-seconds 3 --log-level INFO

# 3) companion（AI 解讀；需要已登入的 Claude Code CLI）
uv run python -m backend.app.worker.ai_bridge serve --poll-seconds 3 `
    --stale-after-seconds 1800 --log-level INFO --log-file var\ai_bridge.log
```

開瀏覽器 → <http://127.0.0.1:8000/>

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
| repo 是 **private** | 沒有 collaborator 權限就 clone 不了，模型權重也下載不到 |
| `PATENT_API_TOKEN` 未填 | AI 任務端點一律 503（刻意 fail closed，不是壞掉） |
| PatentSBERTa 未就位 | 向量化／分群跑不動；worker 啟動時會自動下載 |
| `data/raw/` 為空 | 系統起得來但沒有資料，要自己匯入 WIPS 檔 |
| companion 非 Windows | 排程腳本不適用，改手動跑 `ai_bridge serve` |
| HTML 版面 | 目前為工程過渡版，正在重新設計 |
