# 專利資料庫分析工具架構摘要

## 1. 專案目標

本專案目標是建立一套「專利資料庫建置與分析工具」，可將 GPSS API、WIPS CSV 或其他專利資料來源匯入後，經過資料清理、去重、正式入庫、自動背景分類、報表分析，最後由 HTML 前端操作並輸出 Excel / PPT。

目前定位如下：

```text
PostgreSQL 輕量正式資料庫
+ FastAPI / Python 後端
+ 常駐背景分類 Worker
+ Claude AI Service
+ DuckDB / Python 報表引擎
+ HTML 前端
+ Excel / PPT Exporter
```

Obsidian 暫時不納入正式架構；前端先標記為 HTML，細節後續再討論。

---

## 2. 系統總架構

啟用初期建議使用 Docker Compose 部署在一台伺服器電腦上，使用者透過瀏覽器操作 HTML 前端。

```text
使用者電腦
└─ HTML 前端
      ↓
伺服器電腦 / Docker Compose
├─ frontend container
│  └─ HTML / JS / CSS
│
├─ backend-api container
│  ├─ FastAPI
│  ├─ Import Service
│  ├─ Clean / Dedup Service
│  ├─ Report API
│  ├─ Export API
│  └─ AI Service
│
├─ migrate container
│  ├─ 使用 backend image
│  ├─ 執行 alembic upgrade head
│  └─ migration 跑完即停止
│
├─ classifier-worker container
│  ├─ 常駐背景分類服務
│  ├─ 規則分類
│  ├─ Claude 分類
│  ├─ 低信心重檢
│  ├─ 分類版本監控
│  └─ 分類調整建議
│
├─ report-worker container
│  ├─ DuckDB / Python 報表分析
│  ├─ Claude 報告文字生成
│  ├─ Excel Exporter
│  └─ PPT Exporter
│
└─ postgres container
   └─ PostgreSQL
```

輕量版初期也可以先簡化成三個容器：

```text
frontend container
backend container
postgres container
```

其中 `backend container` 先同時包含 FastAPI、分類 worker、報表 worker 與 AI Service。

資料庫 migration 不併入 FastAPI 或 worker 啟動流程。即使輕量版把 API 與 worker 包在同一個 backend image，也要保留獨立 `migrate container`：

```text
migrate container
└─ 使用 backend image
   └─ alembic upgrade head
      └─ 跑完停止
```

此設計的目的：

```text
schema 變更由明確的 migration 任務處理。
FastAPI 啟動時不自動修改資料庫。
worker 啟動時不自動修改資料庫。
避免多個服務同時啟動時重複跑 migration。
server 部署時可先跑 migrate，再啟動 API / worker。
```

---

## 3. 核心資料流程

整體資料流程採用「先正式入庫，再背景分類，再報表分析，再輸出」的架構。

```text
Backend image build
        ↓
migrate container
        ↓
alembic upgrade head
        ↓
PostgreSQL schema ready
        ↓
GPSS API / WIPS CSV / 其他專利資料
        ↓
Import Service
        ↓
PostgreSQL staging tables
        ↓
Clean / Normalize / Dedup Service
        ↓
PostgreSQL 正式資料表
        ↓
自動建立 classification_jobs
        ↓
classifier-worker 常駐背景分類
        ↓
patent_classifications
        ↓
Report Engine
DuckDB / Python 統計
        ↓
AI Service / Claude 產生報告文字
        ↓
HTML 前端顯示
        ↓
Excel / PPT 輸出
```

重要原則：

```text
不要在匯入前預分類
不要讓使用者按「執行分類」才分類
分類應由背景 worker 持續自動執行
前端只連後端 API，不直接連 PostgreSQL
資料庫 schema migration 只能由 migrate container 執行
FastAPI / worker 不得自行執行 alembic upgrade head
```

---

## 3.1 資料庫分層架構

資料庫與查詢設計先採四層分工。現階段先完成 Layer 1 與 Layer 2；Layer 3 與 Layer 4 先在架構上預留，等報表、API 與前端需求穩定後再實作。

```text
Layer 1 原始層 Raw Layer
Layer 2 正規化核心層 Core Layer
Layer 3 衍生查詢層 Derived / Analytics Layer
Layer 4 應用輸出層 API / Report Layer
```

目前執行規則：

```text
本階段只定義資料庫層架構，不新增 table。
本階段不調整既有 table 欄位。
本階段不清空、不重寫、不修改資料庫內容。
現有資料表先歸類到 Layer 1 / Layer 2。
Layer 3 / Layer 4 只保留架構位置與責任邊界。
```

資料庫層整體關係：

```text
Layer 1 Raw Layer
  source_files
  raw_records
      ↓
Layer 2 Core Layer
  patents
  patent_people
  patent_attributes
  patent_sources
      ↓
Layer 3 Derived / Analytics Layer
  derived tables / views / materialized views
  目前預留，尚未實作
      ↓
Layer 4 API / Report Layer
  FastAPI / Report API / Exporter / Frontend query
  目前預留，尚未實作
```

Layer 1：原始層 Raw Layer

```text
source_files
raw_records
```

責任：

```text
保存來源檔案資訊、file_hash、匯入時間與原始列資料。
raw_records.raw_data 完整保存 WIPS / Excel 原始欄位名稱與原始值。
不做報表拆碼、不改原始欄名、不作為前端查詢最佳化表。
提供追溯、重跑清理與重建後續衍生層的依據。
```

Layer 1 關聯：

```text
source_files.id
  └─ raw_records.source_file_id
```

Layer 2：正規化核心層 Core Layer

```text
patents
patent_people
patent_attributes
patent_sources
```

責任：

```text
patents 保存專利主資料，例如三個號碼、日期、標題、摘要、權利要求、主權項、獨立項。
patent_people 保存申請人、發明人、代理人、專利權人、受讓人、轉讓人等人與公司欄位。
patent_attributes 保存其他 WIPS 欄位寬表，包含分類、引用、同族、圖檔、連結、法律/行政欄位。
patent_sources 串接 patents、raw_records、source_files，保存 dedupe_key 與追溯關係。
```

Layer 2 關聯：

```text
patents.id
  ├─ patent_people.patent_id
  ├─ patent_attributes.patent_id
  └─ patent_sources.patent_id

raw_records.id
  └─ patent_sources.raw_record_id

source_files.id
  └─ patent_sources.source_file_id
```

Layer 2 寫入原則：

```text
patents 保存專利主資料，不保存 dedupe_key、source_summary、imported_at 等系統追蹤欄位。
patent_people 保存人與公司相關來源欄位，欄位本身不可被寫成 value。
patent_attributes 保存其他 WIPS 欄位寬表，欄位即使目前空值也保留。
patent_sources 保存 dedupe_key 與來源追溯關係。
raw_records.raw_data 仍是最完整來源，不被正規化表取代。
```

Layer 3：衍生查詢層 Derived / Analytics Layer

目前狀態：設計中，尚未實作。

預留用途：

```text
支援常態報表、查詢、篩選、統計、排行與交叉分析。
可由 Raw / Core Layer 重算，不取代原始資料與核心資料。
```

未來可能包含：

```text
patent_classifications        IPC / CPC / FI / F-term / USPC 拆碼後查詢表
patent_citations              引用 / 被引用 / 自引 / 他引查詢表
patent_family_members         WIPS / EPO 同族與國家布局查詢表
report_* views                報表查詢 view
report_* materialized views   大量資料時的報表快取
```

Layer 3 寫入原則：

```text
Layer 3 只能由 Raw / Core Layer 衍生或重算。
Layer 3 不作為原始資料來源。
Layer 3 可以為報表拆 IPC / CPC、整理引用、彙總申請人、計算競爭力指標。
Layer 3 的表、view、materialized view 需等報表需求確認後再建立。
```

Layer 4：應用輸出層 API / Report Layer

目前狀態：設計中，尚未實作。

預留用途：

```text
FastAPI 查詢 API
Report API
Dashboard / HTML 前端查詢
Excel Exporter
PPT Exporter
AI Service 報告文字生成入口
```

讀取原則：

```text
API / Report Layer 優先讀 Core Layer 與 Derived / Analytics Layer。
Raw Layer 主要用於追溯與重建，不作為一般前端查詢入口。
Report Layer 不直接修改 Raw / Core 資料。
```

Layer 4 邊界：

```text
資料庫容器只提供 PostgreSQL 與 SQL 查詢能力。
正式查詢功能由 FastAPI / Report API / Frontend 實作。
PPT / Excel 輸出讀取 Core / Derived 查詢結果，不直接改 Raw Layer。
Claude AI Service 只能透過後端服務讀取整理後資料或報表結果，不直接操作資料庫 schema。
```

現階段結論：

```text
目前資料庫表已足夠支撐 Layer 1 與 Layer 2。
Layer 3 / Layer 4 先保留架構位置，不新增資料表或服務。
等第一版報表與查詢需求確認後，再補 derived tables、views、materialized views 與 API。
```

---

## 3.2 資料庫 Migration 架構

server 化後，資料庫 schema 版本管理採用 Alembic，但 migration 執行責任必須獨立於 API 與 worker。

目標架構：

```text
backend image
├─ FastAPI runtime
├─ worker runtime
├─ importer / report runtime
└─ Alembic migration runtime

migrate container
└─ image: backend image
   command: alembic upgrade head
   lifecycle: run once, then stop

backend-api container
└─ image: backend image
   command: start FastAPI
   不執行 migration

classifier-worker / report-worker container
└─ image: backend image
   command: start worker
   不執行 migration
```

部署順序：

```text
1. 啟動 postgres container。
2. 執行 migrate container：alembic upgrade head。
3. migrate 成功結束。
4. 啟動 backend-api / worker / frontend。
```

設計規則：

```text
Alembic migration 檔案放在 backend 專案內。
migrate container 使用與 API / worker 相同的 backend image。
API / worker 啟動流程不得包含 alembic upgrade head。
Docker PostgreSQL init SQL 只負責最小初始化或空資料庫準備，不作為長期 schema 更新機制。
既有資料庫要納入 Alembic 管理時，需先評估 stamp head 或專門 migration，不能直接覆蓋資料。
```

目前狀態：

```text
此架構已列入設計，但尚未實作。
目前仍以 sql/005_six_table_schema.sql 與 scripts/db_reset_and_import.ps1 管理開發資料庫重建。
等使用者確認 server 內容後，再建立 Alembic 目錄、migration revision、backend image 與 migrate service。
```

---

## 4. Claude AI 的定位

Claude 不直接接前端，也不直接碰資料庫，而是包在後端的 AI Service 裡。

```text
FastAPI / Worker
    ↓
AI Service
    ↓
Claude API
    ↓
後端驗證輸出
    ↓
PostgreSQL
```

Claude 主要負責兩件事：

1. 規則分類分不出來、低信心或多分類衝突時，協助做技術分類判斷。
2. 根據 DuckDB / Python 已經算好的報表結果，撰寫分析摘要、報告文字與 PPT 文字內容。

Claude 不負責資料匯入、資料清理、去重、SQL 寫入、報表統計、PPT / Excel 檔案產生。

分類輸出必須採用固定 JSON schema，例如：

```json
{
  "patent_id": "xxx",
  "category_key": "brushless_motor_control",
  "confidence": 0.86,
  "reason": "The patent describes control of a brushless motor drive system.",
  "needs_review": false
}
```

後端必須驗證 `category_key` 是否存在、信心分數是否合理、欄位是否完整，以及是否需要人工確認。分類樹調整不能由 AI 自動修改正式分類表，只能先產生 `classification_suggestions`，再經人工確認與版本化處理。

---

## 5. 第一版預計報表

第一版報表目標是支援專利分析簡報常用圖表與表格，先以可穩定查詢、可輸出 Excel / PPT 為主，視覺細節後續再調整。

預計第一版包含：

```text
1. 專利申請趨勢圖
2. 專利布局國家分析圖
3. IPC / CPC 技術分類統計圖
4. 主要申請人分析圖
5. 企業研發能量及競爭力圖
6. 高被引用專利表
```

資料需求：

```text
專利申請趨勢圖
  主要使用 patents.application_date / application_year。

專利布局國家分析圖
  主要使用 patents.country_code，必要時再結合同族國家欄位。

IPC / CPC 技術分類統計圖
  短期可從 patent_attributes 的 Curr./Orig. IPC/CPC 欄位查詢。
  若要常態支援篩選、分布、排行與交叉分析，後續應建立 patent_classifications 這類 derived relation table。

主要申請人分析圖
  主要使用 patent_people 的申請人、標準化申請人、申請人代表碼等欄位。

企業研發能量及競爭力圖
  會綜合申請量、年度趨勢、技術分類分布、同族/國家布局、引用或被引用資料。
  此報表屬於複合指標，指標公式需另外定義並版本化。

高被引用專利表
  主要使用引用/被引用相關欄位。
  若要穩定排序與篩選，後續可建立 patent_citations 或 citation metrics derived table。
```

設計原則：

```text
報表查詢不得破壞 raw_records 與 patent_attributes 的原始保存。
常態報表需要的拆碼、統計、排行，可建立 derived / relation tables。
derived table 是查詢加速與報表用，不取代原始 WIPS 欄位。
第一版先確保資料欄位來源與統計邏輯可追溯，再做視覺化與 PPT 版型。
```

---

## 6. 第一版 MVP 開發順序

建議開發順序如下：

```text
1. 建立 Docker Compose
   - frontend
   - backend
   - postgres

2. 建立 FastAPI 基礎專案
   - /api/health
   - DB connection
   - config / env

3. 建立 PostgreSQL schema
   - patents
   - staging_patents
   - import_batches
   - categories
   - patent_classifications
   - classification_jobs

4. 做 CSV 匯入
   - 上傳 CSV
   - 存 staging
   - 建立 import batch

5. 做清理 / 去重 / 正式入庫
   - staging → patents
   - 建立 classification_jobs

6. 做背景 classifier worker
   - 定時掃 classification_jobs
   - 先做規則分類
   - 寫入 patent_classifications

7. 接 Claude AI Service
   - 只處理規則分不出來或低信心資料
   - JSON schema 驗證
   - 寫入分類結果

8. 做報表 API
   - 專利申請趨勢圖
   - 專利布局國家分析圖
   - IPC / CPC 技術分類統計圖
   - 主要申請人分析圖
   - 企業研發能量及競爭力圖
   - 高被引用專利表

9. 做 HTML 前端初版
   - 專利清單
   - 分類狀態
   - 報表頁
   - 匯出按鈕

10. 做 Excel / PPT Exporter
   - 先產簡單版
   - 後續再套公司模板
```

一句話總結：

> 本工具以 PostgreSQL 作為輕量正式資料庫，FastAPI / Python 作為後端；資料正式入庫後，由常駐背景分類 worker 自動分類，必要時呼叫 Claude；報表由 DuckDB / Python 統計，Claude 只負責撰寫分析文字，最後由 HTML 前端操作並輸出 Excel / PPT。
