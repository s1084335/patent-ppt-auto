# 專利資料庫分析工具架構摘要

## 1. 專案目標

本專案目標是建立一套「專利資料庫建置與專利侵權比對工具」，可將 WIPS Excel、GPSS API 或其他專利資料來源匯入後，經過原始保存、清理、去重合併、正式入庫與追溯記錄，優先支援主權項與獨立項的自動侵權比對，並由 HTML 前端操作、AI chat 協助分析，最後輸出可追溯的報告檔案。正式部署固定採五個 Docker Compose 容器，由 Nginx 作為唯一系統入口、Backend 提供 API 與 Job 管理、Worker 執行長時間運算。報表分析仍保留在架構中，但重要性低於侵權比對、匯入/匯出紀錄與報告生成；公司名稱標準化採獨立專利權人對照表作為報表統計與使用者端顯示的 mapping layer，不改寫資料庫原始欄位值。

目前定位如下：

```text
PostgreSQL 輕量正式資料庫
+ 固定五容器 Docker Compose（nginx / frontend / backend / worker / postgres）
+ Nginx 唯一系統入口
+ Backend API / 驗證 / Workspace / Job 建立與查詢
+ Worker 分群 / 報表 / Embedding / 案件比對
+ Import / Export Audit Service
+ Claim Comparison / Infringement Analysis Service
+ Company Alias / Owner Display Mapping Service
+ Claude Code CLI + Skills + Playwright MCP 專利權人代碼補全流程
+ Claude AI Service / AI chat 區塊
+ PostgreSQL SQL / views / materialized views 報表統計
+ HTML 前端
+ Excel / PPT / Report Exporter
```

Obsidian 暫時不納入正式架構；前端先標記為 HTML，並預留類似 Microsoft Copilot 側欄概念的 AI chat 區塊，細節後續再討論。

---

## 2. 系統總架構

正式部署固定使用 Docker Compose，穩態只能有五個 service／container：`nginx`、`frontend`、`backend`、`worker`、`postgres`。不得為分類、報表、Embedding、案件比對、migration 或 queue 再拆出第六個常駐容器。

```text
使用者電腦
└─ Browser
      ↓ HTTP / HTTPS
伺服器電腦 / Docker Compose
├─ nginx container
│  ├─ 唯一對外入口
│  ├─ / → frontend
│  └─ /api/ → backend
│
├─ frontend container
│  └─ HTML / JS / CSS
│
├─ backend container
│  ├─ API
│  ├─ request / schema / output 驗證
│  ├─ Workspace 建立與查詢
│  ├─ Job 建立、排程狀態與結果查詢
│  └─ 不執行長時間運算
│
├─ worker container
│  ├─ 分群
│  ├─ 報表
│  ├─ Embedding
│  └─ 案件比對
│
└─ postgres container
   ├─ PostgreSQL 正式資料庫
   └─ Workspace / Job queue / 狀態 / 結果
```

五個容器的責任不可因功能增加而再拆分。分類、報表、Embedding、案件比對共用同一個 `worker` runtime；第一版 Job queue 使用 PostgreSQL，不新增 Redis。`backend` 與 `worker` 可共用 backend image，但啟動命令與責任不同。

資料庫 migration 不併入 backend 或 worker 啟動流程，也不建立常駐 `migrate` service：

```text
docker compose run --rm backend alembic upgrade head
```

此設計的目的：

```text
schema 變更由明確的一次性 migration 命令處理。
backend 啟動或處理 API 時不自動修改資料庫 schema。
worker 啟動時不自動修改資料庫。
一次性 migration container 使用 --rm，完成後不留在 docker compose ps -a。
只有 schema 有變更時才按需啟動一次性 migration；一般啟動不必每次執行。
```

---

## 3. 核心資料流程

整體資料流程採用「先正式入庫並保留追溯，再整理權利要求資料並建立公司名稱對照映射，再做主權項/獨立項侵權比對，最後輸出報告」的架構；報表分析保留為衍生能力，不再作為最優先主軸。

```text
Backend image build
        ↓
docker compose run --rm backend alembic upgrade head
        ↓
PostgreSQL schema ready
        ↓
nginx → frontend / backend API
        ↓
Backend 建立 Workspace / Job
        ↓
PostgreSQL 保存 Job queue / 狀態
        ↓
Worker claim Job
        ↓
分群 / 報表 / Embedding / 案件比對
        ↓
PostgreSQL 保存結構化結果與追溯
        ↓
Backend 查詢 Job 狀態與結果
        ↓
nginx → frontend 顯示 / 檔案輸出
```

重要原則：

```text
不要在匯入前做不可追溯的比對或分類\r\n不要讓 AI 直接修改正式資料\r\n分類與報表是衍生能力，優先順序低於侵權比對與報告輸出
Nginx 是唯一對外入口，frontend / backend / worker / postgres 不直接暴露給使用者端
前端不直接改 PostgreSQL；由 backend 建立 Job，worker 執行長時間任務
資料庫 schema migration 使用 backend image 的一次性 --rm 命令
backend / worker 啟動流程不得自行執行 alembic upgrade head
穩態 docker compose ps -a 只能看見五個容器
```

---

## 3.1 資料庫分層架構

資料庫與查詢設計先採四層分工。現階段先完成 Layer 1 與 Layer 2；Layer 3 之後作為通用報表基礎資料與單次分析結果保存層，Layer 4 作為應用輸出層。Layer 3 以後先在架構上預留，等侵權比對、公司對照、runner 與前端需求穩定後再實作。

```text
Layer 1 原始層 Raw Layer
Layer 2 正規化核心層 Core Layer
Layer 3 衍生查詢與分析層 Derived / Analytics Layer
Layer 4 應用輸出層 Runner / Report Layer
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
Layer 3 Derived Layer
  report_patent_base
  analysis_table
  reusable derived tables / views / materialized views
  目前預留，尚未實作
      ↓
Layer 4 Runner / Report Layer
  Nginx / Frontend / Backend API / Worker / Exporter
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

Layer 3：衍生查詢與分析層 Derived / Analytics Layer

目前狀態：設計中，尚未實作。

定位：

```text
derived_layer 同時承擔通用報表基礎資料與版本化結果保存。
report_patent_base 提供可重複使用、可查詢、可重算的乾淨專利寬表。
版本化結果固定每件專利一列，使用既定專利號對齊，以 workspace 名稱與輸入 fingerprint 識別版本，不新增無業務用途的流水 id。
同一 workspace 新增專利時建立新版本列，不覆蓋舊數據；總量 scope 也必須跟進。
derived_layer 可由 Raw / Core / relation layer 重算，不取代原始資料與核心資料。
```

主要母表：

```text
report_patent_base            報表與分析共用的乾淨專利寬表
versioned_patent_snapshot     每件專利一列的總量／workspace 報表數據與永久版本
```

`report_patent_base` 預計整合：

```text
專利基本資料
標準化申請人 / 專利權人顯示名稱
IPC / CPC
國別
日期
family_id / family key
法律狀態
權利要求 / 主權項 / 獨立項摘要欄位
引用與被引用基礎欄位
```

未來可能包含：

```text
patent_classifications        IPC / CPC / FI / F-term / USPC 拆碼後查詢表
patent_citations              引用 / 被引用 / 自引 / 他引查詢表
patent_family_members         WIPS / EPO 同族與國家布局查詢表
patentability_*               可專利性比對結果，可併入 analysis_table 或依量體拆表
infringement_*                主權項 / 獨立項侵權比對結果，可併入 analysis_table 或依量體拆表
report_\* views                報表查詢 view
report_\* materialized views   大量資料時的報表快取
```

Layer 3 寫入原則：

```text
Layer 3 只能由 Raw / Core / relation layer 衍生或重算。
Layer 3 不作為原始資料來源。
Layer 3 可建立公司名稱顯示映射、處理專利家族去重/不去重查詢口徑，也可以用 PostgreSQL SQL / views / materialized views 為報表拆 IPC / CPC、整理引用、彙總申請人、計算競爭力指標。
公司名稱標準化只用於報表統計與使用者端顯示，不回寫或覆蓋 Core Layer 的原始公司/專利權人欄位值。
報表統計的公司維度應以專利權人/公司對照表解析出的正規化公司名稱計算；若找不到對照，才 fallback 到原始公司名稱並標記為待補全。
版本化 snapshot 不得覆蓋 Raw / Core 的原始資料，並以專利號追溯回 report_patent_base 與 core_layer.patents。
業務時間只保留兩欄：最早來源檔匯入時間與最終報告／PDF 匯出時間；中間分群、報表不保存進出時間。
報表值必須落到對應專利列；固定欄位或 JSON 欄位的最小 schema 另行確認，不得把 scope 聚合值重複灌進每件專利列。
版本永久保存；PPT 第一版只比較數據差異，不比較 AI 文案。
舊分群／報表紀錄表先保留但停止新增不必要的中間歷程；新版驗收前不移除。中間運算失敗只在 processing_jobs 保存最終狀態與錯誤，不另建事件表。
Layer 3 的表、view、materialized view 需等侵權比對與使用者端流程確認後再建立。
```

Layer 4：應用輸出層 Runner / Report Layer

目前狀態：設計中，尚未實作。

預留用途：

```text
Backend API / Workspace / Job 查詢入口
Worker Claim Comparison Job
Worker Report Job
Dashboard / HTML 前端查詢
AI chat 區塊
PPT / Report Exporter
案件比對 PDF Exporter
AI Service 報告文字生成入口
```

讀取原則：

```text
Runner / Report Layer 優先讀 Derived / Analytics Layer，必要時追溯 Core / Raw Layer。
Raw Layer 主要用於追溯與重建，不作為一般前端查詢入口。
Report Layer 不直接修改 Raw / Core 資料。
```

Layer 4 邊界：

```text
資料庫容器只提供 PostgreSQL 與 SQL 查詢能力。
正式 API 與查詢由 backend 實作；分群、報表、Embedding、案件比對由單一 worker 實作；frontend 經 nginx 使用系統功能。
PPT／報表與案件比對 PDF 讀取 Derived / Analytics 結果，不直接改 Raw Layer。檔案統一放 `data/report_artifacts/`：PPT／報表放 `ppt/`，案件比對 PDF 放 `comparison/`。
Claude AI Service 只能透過後端服務讀取整理後資料、比對結果或報表結果，不直接操作資料庫 schema。
```

建議核心資料流：

```text
core_layer / relation_layer
    ↓
derived_layer.report_patent_base
    ↓
derived_layer.versioned_patent_snapshot
    ↓
data/report_artifacts/ppt/ 或 data/report_artifacts/comparison/
```

現階段結論：

```text
目前資料庫表已足夠支撐 Layer 1 與 Layer 2。
Layer 3 / Layer 4 先保留架構位置，不新增資料表或服務。
等第一版侵權比對、公司對照、匯入/匯出紀錄與 AI chat 流程確認後，再補 report_patent_base、analysis_table、views、materialized views 與 runner 入口。
```

---

## 3.2 資料庫 Migration 架構

server 化後，資料庫 schema 版本管理採用 Alembic；migration 執行責任獨立於 backend 與 worker 的啟動流程，但不再建立第六個 Compose service。

目標架構：

```text
backend image
├─ Backend API runtime
├─ Worker runtime
├─ Job / Workspace application service
└─ Alembic migration runtime

backend container
└─ image: backend image
   command: start API server
   不執行 migration

worker container
└─ image: backend image
   command: start worker
   workloads: clustering / reporting / embedding / case comparison
   不執行 migration

one-off migration
└─ docker compose run --rm backend alembic upgrade head
   由資料庫更新／部署流程自動觸發
   每次新建、完成即移除，不使用 restart policy
   不形成第六個常駐 service / container
```

按需 migration 操作：

```text
1. 一般啟動直接啟動 nginx / frontend / backend / worker / postgres 五個 service。
2. 只有 schema 有變更時，先確保 postgres 可連線。
3. 按需執行 docker compose run --rm backend alembic upgrade head。
4. 更新／部署流程可自動執行此命令；migration 失敗時停止更新，不啟動新版 backend / worker。
5. 確認 migration 成功且一次性 container 已移除。
6. 確認 docker compose ps -a 穩態只有五個容器。
```

設計規則：

```text
Alembic migration 檔案放在 backend 專案內。
一次性 migration 使用與 backend / worker 相同的 backend image。
backend / worker 啟動流程不得包含 alembic upgrade head。
Docker PostgreSQL init SQL 只負責最小初始化或空資料庫準備，不作為長期 schema 更新機制。
既有資料庫要納入 Alembic 管理時，需先評估 stamp head 或專門 migration，不能直接覆蓋資料。
```

目前狀態：

```text
Alembic 已導入，作為正式 schema 管理機制。
alembic/ 目錄、env.py、baseline migration 0001_baseline_schema 已建立。
baseline 由 sql/001-012 的最終狀態（pg_dump --schema-only）整合而成。
fresh DB 用 alembic upgrade head 建出完整四層 schema；既有 DB 用 alembic stamp head 標記，不重跑、不動資料。
sql/001-012 保留為歷史紀錄；後續 schema 變更改用新的 alembic revision，不再新增 sql/ 檔。
backend image、固定五個 service 與一次性 migration 命令待 Docker 階段建立；不再建立獨立 migrate service。
```

---

## 3.3 專利分類樹（概念與導流）

單一 `worker` 的分群能力，除了規則分類與 Claude 單標籤分類外，另規劃一套**多維度技術分類樹**，用來把專利依技術概念自動歸類、可動態成長。整體架構只記概念，細節見獨立設計檔。

概念要點：

```text
純文本驅動：從專利文字建，不用 IPC/CPC。
多維度：問題/手段/功效/應用/元件；MVP 先做 手段 × 功效（技術功效矩陣）。
Box 為主、LLM 為輔：LLM 抽片語+分維度，box embedding 學階層(is-a=包含)與分類。
監督定位：weakly-supervised (LLM-guided)，box 建樹子步驟 self-supervised。
節點=片語級概念；專利=其片語所屬節點的聯集（多標籤）。
動態成長 + 人工確認版本化，對應 classification_suggestions 與 taxonomy_version。
```

與整體架構的邊界一致：不改 Raw/Core 原始資料、分類結果屬衍生/應用層、AI 不直接改正式分類表（只出 `classification_suggestions` 經人工版本化）、與主權項/獨立項侵權比對分開。

詳細設計（Pipeline、資料模型、風險、MVP 步驟）見 → [docs/patent_taxonomy_design.md](docs/patent_taxonomy_design.md)。

---

## 4. Claude AI 的定位

Claude 不直接接前端，也不直接碰資料庫，而是包在後端的 AI Service 裡。

```text
Backend / Worker
    ↓
AI Service
    ↓
Claude API
    ↓
後端驗證輸出
    ↓
PostgreSQL
```

Claude 主要支援後端已整理好的專利資料、PostgreSQL 統計結果與比對任務，核心用途改為協助主權項與獨立項的侵權比對說明、差異摘要、風險描述、報告文字生成，以及前端 AI chat 區塊中的問答輔助。規則分類、報表文字與 PPT 文字仍可使用 Claude，但優先順序低於 claim comparison workflow。

Claude 不負責資料匯入、資料清理、去重、SQL 寫入、正式判定、報表統計、PPT / Excel / 最終報告檔案產生。

### 4.1 案件比對兩階段架構

案件比對與報表／PPT 獨立，分成兩個人工閘門階段。第一階段由 Claude 讀取所有權利要求（後備為獨立項＋從屬項），辨識全部獨立項、從屬引用鏈、技術要素與關鍵 Claim 用語，輸出專利理解稿；使用者核准後，第二階段才把已核准理解與產品文字、照片及結構圖逐要素比對。後端固定執行 all-elements rule，使用者覆核後由專用 exporter 產生案件比對 PDF。

圖片不是全量處理。只有 Claim 結構關係不清楚時，Worker 才從專利 PDF 產生 contact sheet，由 Claude 建議相關頁／圖並由使用者確認。所有資產集中在 `data/patent_assets/<patent_number>/<pdf_sha256>/`；DB 只保存最終選用圖片的相對路徑。

AI 輸出必須採用固定 JSON schema。分類任務可使用下列格式，例如：

```json
{
  "patent_id": "xxx",
  "category_key": "brushless_motor_control",
  "confidence": 0.86,
  "reason": "The patent describes control of a brushless motor drive system.",
  "needs_review": false
}
```

後端必須驗證 `category_key` 是否存在、信心分數是否合理、欄位是否完整，以及是否需要人工確認。案件比對任務也必須以結構化結果回傳，先完成專利理解人工核准，再區分各獨立項與其從屬分支逐要素比對；AI chat 顯示的回答只能引用後端允許的資料範圍與比對結果，不得直接修改正式資料。分類樹調整不能由 AI 自動修改正式分類表，只能先產生 `classification_suggestions`，再經人工確認與版本化處理。

---

## 5. 報表與輸出定位

報表重要性調整為低於侵權比對與最終報告生成。第一版仍保留專利分析常用圖表與表格能力，但定位是支援使用者理解資料與補充報告內容，不再是最優先交付主軸。

保留的第一版報表內容：

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
  短期可從 patents 的 Curr./Orig. IPC/CPC Main 欄位與 patent_attributes 的 Curr./Orig. IPC/CPC All 欄位查詢。
  若要常態支援篩選、分布、排行與交叉分析，後續應建立 patent_classifications 這類 derived relation table。

主要申請人分析圖
  主要使用 patent_people 的申請人、專利權人等來源欄位，並透過公司對照映射產生報表/前端顯示名稱；Core Layer 原始欄位值不因標準化而被改寫。

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
常態報表需要的拆碼、統計、排行，優先在 PostgreSQL 建立 derived / relation tables、views 或 materialized views。
derived table / view / materialized view 是查詢加速與報表用，不取代原始 WIPS 欄位；DuckDB 先不納入第一版報表統計路徑。
第一版先確保資料欄位來源、侵權比對依據、匯入/匯出紀錄與統計邏輯可追溯，再做視覺化與 PPT / 最終報告版型。
```

---

## 6. 第一版 MVP 開發順序

建議開發順序如下：

```text
1. 建立 Docker Compose
   - nginx
   - frontend
   - backend
   - worker
   - postgres
   - 固定五個 service，不新增 Redis 或 migrate service

2. 建立系統入口與 Job 執行邊界
   - nginx 唯一對外入口
   - backend API / 驗證 / Workspace / Job 建立與查詢
   - worker 分群 / 報表 / Embedding / 案件比對
   - PostgreSQL Job queue / 狀態 / 結果
   - container health check
   - DB connection
   - config / env
   - 一次性 --rm migration 執行邊界

3. 建立 PostgreSQL schema / Alembic migration
   - raw_layer
   - core_layer
   - derived_layer
   - app_layer
   - import / export audit records

4. 做資料庫檔案匯入
   - 上傳 WIPS Excel / 其他來源檔
   - 記錄 source file / import run
   - 保存 raw_records
   - 寫入 core tables

5. 做清理 / 去重 / 正式入庫
   - dedupe_key 去重
   - 專利家族去重口徑預留
   - patents / people / attributes / sources 追溯

6. 做公司對照表第一版
   - 專利權人名稱顯示對照
   - 公司 alias 對照
   - 使用者端與報表顯示標準化後專利權人名稱
   - 報表統計以正規化公司名稱 group by
   - 匯入後掃描未對照公司，必要時由 Claude Code CLI + Skills + Playwright MCP 查 WIPS 標準專利權人代碼並補入對照表
   - 不改寫 patent_people / raw_records 的原始公司名稱值

7. 做主權項 / 獨立項侵權比對第一版
   - 主權項比對
   - 獨立項比對
   - 比對結果保存
   - 比對依據可追溯到 patent / raw record

8. 接 Claude AI Service 與 AI chat 區塊
   - claim comparison 說明
   - 差異摘要
   - 風險描述
   - 前端 AI chat 類 Copilot 區塊
   - JSON schema 驗證

9. 做最終報告匯出
   - 報告產生紀錄
   - Excel / PPT / Report Exporter
   - 匯出結果與來源資料追溯

10. 做報表 runner 與進階報告口徑
   - 專利申請趨勢圖
   - 專利布局國家分析圖
   - IPC / CPC 技術分類統計圖
   - 主要申請人分析圖
   - 企業研發能量及競爭力圖
   - 高被引用專利表
   - 可區分專利家族去重 / 不去重的報告
```
一句話總結：

> 本工具正式部署固定使用 nginx、frontend、backend、worker、postgres 五個 Docker Compose 容器：Nginx 是唯一系統入口，Backend 負責 API、驗證、Workspace 與 Job 建立／查詢，Worker 統一執行分群、報表、Embedding 與案件比對，PostgreSQL 保存正式資料、Job 狀態與可追溯結果；不再增加 Redis、migrate 或拆分型 worker 容器。














## 2026-07-17 最終定案：專利權人正規化使用兩個 MCP

最終架構採兩個 MCP 並行，不把 Playwright 瀏覽器操作硬塞進 Central Patent MCP Server。

```text
Claude Code
├─ Central Patent MCP Server
│  ├─ clustering tools
│  ├─ reporting tools
│  └─ assignee normalization DB tools
│
└─ Playwright MCP
   └─ WIPS browser automation
```

分工固定如下：

- `Central Patent MCP Server`：負責資料庫與業務工具，包括掃描未正規化公司、讀取唯一一張專利權人/公司對照表、建立待補全任務、寫入對照表、提供報表使用的正規化公司名稱。
- `Playwright MCP`：只負責瀏覽器自動化，包括開啟 WIPS、搜尋公司名稱、展開標準申請人結果、讀取 WIPS 畫面資料。
- `Claude Code`：負責協調兩個 MCP，將 Playwright MCP 讀到的 WIPS 結果整理成固定 schema，再交給 Central Patent MCP Server 寫入資料庫。

資料庫對照表原則：DB 內只能有一張專利權人/公司對照表。原有專利的公司、申請人、專利權人、受讓人等來源欄位值不動；報表統計透過這張對照表取得 `normalized_company_name` 後再 group by。

