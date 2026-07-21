# Frontend Interface Plan

## 文件目的

本文件只整理專利 PPT 自動化工具的前端介面規劃：頁面版位、顯示內容、主要功能與使用者操作流程。

不在本文件討論：

- MCP Server 架構
- 後端容器架構
- 資料庫 schema 細節
- Claude CLI 實作方式
- 報表引擎內部實作

## 整體版位

前端採三欄工作台布局，上方保留 workspace 狀態列。

```text
┌──────────────────────────────────────────────┐
│ Workspace / 專案狀態列                         │
├──────────────┬──────────────────────┬────────┤
│ 左側功能選單   │ 中央主要工作區          │ AI 區  │
│              │                      │        │
│ 匯入專利       │ 表格 / 分群 / 報表操作   │ Claude │
│ Workspace    │                      │ 建議   │
│ 分群          │                      │ 摘要   │
│ 報表          │                      │ 說明   │
│ PPT           │                      │        │
└──────────────┴──────────────────────┴────────┘
```

## 版位規劃表

| 區域 | 目的 | 主要顯示 | 主要操作 | 備註 |
|---|---|---|---|---|
| Workspace 狀態列 | 顯示目前工作上下文 | workspace 名稱、專利數、技術分群狀態、功效分群狀態、報表狀態、最後更新時間 | 切換 workspace、刷新狀態 | 應固定在頁面上方 |
| 左側功能選單 | 串起使用者流程 | 匯入專利、Workspace、分群、AI 標籤、報表、PPT 輸出 | 切換功能頁 | 選單項目需顯示完成/待處理/錯誤狀態 |
| 中央主要工作區 | 承載主要操作與資料檢視 | 表格、候選方案、topic 清單、報表預覽、PPT 大綱 | 執行、選擇、合併、改名、套用 | 所有正式修改都在中央區由使用者確認 |
| 右側 AI 助手區 | 顯示 Claude CLI 產出的建議 | 候選方案說明、topic label 建議、summary、報表摘要、PPT 文案草稿 | 套用建議、重產建議、複製文字 | AI 不直接改資料，需使用者套用 |

## 左側功能流程

| 步驟 | 功能名稱 | 目的 | 主要輸入 | 主要輸出 |
|---|---|---|---|---|
| 1 | 匯入專利 | 將 WIPS Excel 匯入資料庫 | Excel 檔、資料來源設定 | 匯入結果、欄位檢查、去重結果 |
| 2 | Workspace | 建立或選擇分析範圍 | 專利清單、篩選條件、workspace 名稱 | workspace 專利集合 |
| 3 | 分群 | 對技術/功效資料執行分群 | workspace、source field | 三組候選方案、分群品質指標 |
| 4 | AI 標籤 | 產生 topic label 與摘要 | topic keywords、代表性專利 | AI label、AI summary |
| 5 | 人工整理 | 讓使用者定案分類結果 | topic 清單、AI 建議 | 合併、復原、重新命名、排序 |
| 6 | 報表 | 選擇並預覽報表內容 | workspace、topic 結果、報表模板 | 報表資料、圖表預覽 |
| 7 | PPT 輸出 | 產生正式簡報 | 報表選項、PPT 模板、AI 文案 | PPT 檔案 |

## Workspace 狀態列

建議顯示格式：

```text
Workspace：健身器材專利分析
專利數：205
技術分群：已完成
功效分群：待執行
報表：未產生
最後更新：2026-07-16 15:36
```

狀態應至少包含：

| 狀態 | 意義 |
|---|---|
| 未開始 | 尚未執行該步驟 |
| 執行中 | 後端任務正在處理 |
| 待確認 | 已有結果，等待使用者選擇或套用 |
| 已完成 | 使用者已定案 |
| 失敗 | 任務錯誤，需要查看錯誤訊息 |

## 中央主要工作區規劃

### 匯入專利頁

| 區塊 | 顯示內容 | 操作 |
|---|---|---|
| 上傳區 | WIPS Excel 檔案名稱、檔案大小 | 選擇檔案、開始匯入 |
| 欄位檢查 | 四種專利號、country_code、獨立項、功效、申請人、分類號 | 查看缺漏欄位 |
| 匯入結果 | 新增筆數、更新筆數、略過筆數、錯誤筆數 | 查看錯誤明細 |
| 去重結果 | 依專利號機制識別的重複資料 | 展開比對明細 |

### Workspace 頁

| 區塊 | 顯示內容 | 操作 |
|---|---|---|
| Workspace 清單 | 名稱、專利數、狀態、建立時間 | 開啟、建立、封存 |
| 專利清單 | 專利號、標題、申請人、國別、獨立項/功效完整度 | 勾選、篩選、加入 workspace |
| 資料完整度 | 技術文本可用數、功效文本可用數、embedding 狀態 | 查看缺漏 |

### 分群頁

| 區塊 | 顯示內容 | 操作 |
|---|---|---|
| Source tabs | 技術、功效 | 切換資料來源 |
| 執行區 | 文件數、embedding 狀態、候選 k 範圍 | 執行分群 |
| 候選方案 | 保守、平衡、細分三組；coherence、diversity、balance、score | 選定候選方案 |
| 品質提示 | 過小 topic 比例、balance、coherence 警示 | 查看說明 |

前端原則：

- score 只作排序輔助，不應單獨作為決策依據。
- 候選方案需同時顯示指標與 AI 說明。
- 使用者選定候選後才正式寫入 top-level topics。

### Topic 整理頁

| 區塊 | 顯示內容 | 操作 |
|---|---|---|
| Topic 列表 | label、doc_count、keywords、代表性專利 | 選取、改名、排序 |
| Topic 詳細 | 前五筆代表性專利、文本節錄、摘要 | 查看明細 |
| 合併工具 | 已選 topic、合併後名稱、建議相近 topic | 合併 |
| 合併紀錄 | 合併時間、來源 topics、結果 topic | 復原合併 |
| AI 建議 | Claude CLI 產生的 label / summary | 套用、重產 |

限制：

- AI 不可直接改 assignment。
- 合併需由使用者明確執行。
- 復原只根據 merge history 做，不做任意 split。

### 報表頁

| 區塊 | 顯示內容 | 操作 |
|---|---|---|
| 報表模板清單 | 可用報表、報表類型、是否支援篩選 | 勾選報表 |
| 報表預覽 | 表格資料、筆數、主要欄位 | 查看、排序 |
| 圖表預覽 | 趨勢圖、排名圖、分類圖、地圖 | 切換圖表 |
| AI 摘要 | 報表重點摘要、PPT 文字草稿 | 套用到 PPT |

目前報表候選：

- 專利申請趨勢
- 專利公告趨勢
- 專利受理局分布
- IPC 主分類分布
- CPC 主分類分布
- 主要申請人排名
- 現專利權人排名
- 高被引用專利排名
- 企業研發能量
- 專利生命週期
- 國家佈局
- 家族完整性明細

### PPT 輸出頁

| 區塊 | 顯示內容 | 操作 |
|---|---|---|
| PPT 模板 | 模板名稱、用途 | 選擇模板 |
| 章節大綱 | 封面、研究範圍、分類結果、報表分析、結論 | 調整順序 |
| Slide 預覽 | 每頁標題、圖表、摘要文字 | 查看預覽 |
| 輸出控制 | 檔名、輸出位置、版本 | 產生 PPT、重新產生 |

## 右側 AI 助手區

AI 助手區只顯示建議，不直接修改資料。

| 情境 | AI 顯示內容 | 使用者操作 |
|---|---|---|
| 候選方案選擇 | 三組候選差異說明 | 選擇候選 |
| Topic 命名 | label / summary 建議 | 套用、重產、手動改 |
| Topic 合併 | 相近 topic 說明 | 使用者決定是否合併 |
| 報表摘要 | 報表重點、異常提示 | 套用到 PPT 文案 |
| PPT 草稿 | slide 標題、段落文字 | 套用、修改 |

## 操作權限原則

| 動作 | AI 是否可直接執行 | 原則 |
|---|---:|---|
| 產生候選方案說明 | 否 | AI 只產生文字，由使用者看 |
| 選定分群候選 | 否 | 必須使用者選 |
| 產生 topic label/summary | 否 | AI 產生建議，使用者套用 |
| 合併 topic | 否 | 必須使用者確認 |
| 復原合併 | 否 | 必須使用者確認 |
| 產生報表摘要 | 否 | AI 產生草稿，使用者套用 |
| 產生 PPT | 否 | 使用者按下輸出 |

## 第一版前端優先範圍

第一版不追求完整正式 UI，先做可驗收流程：

1. Workspace + 專利清單
2. 分群候選選擇
3. Topic 整理與合併/復原
4. AI label/summary 套用
5. 報表選擇與圖表預覽
6. PPT 輸出入口

## Backend Contract Readiness Matrix

本節依「產品操作順序」盤點每個步驟的後端契約現況，供前端判斷哪些流程可直接串接、哪些還缺 API。

判斷依據與規則：

- 狀態一律**依實際程式**判定（`backend/app/api/*`、`backend/app/mcp_server/tools_clustering.py`、`backend/app/clustering/workspace_service.py`），不因規劃文件寫過就標 ready。
- **FastAPI endpoint** 與 **MCP tool** 分欄，不混稱同一入口。FastAPI 前綴為 `/api/v1`。
- 狀態定義：
  - `ready`：Web 前端可直接用現有 FastAPI endpoint 完成此步驟。
  - `partial`：功能只在 MCP（Claude 用）或只覆蓋部分需求，對應的 Web API 尚缺。
  - `missing`：無任何 FastAPI／MCP 契約可完成此步驟（引擎函式是否已存在另記於「現有可用邊界」）。
- 報表相關契約不在本節盤點（由另一路維護），本 14 步亦不含報表步驟。
- 復原合併只能依 merge history 對「曾合併」的主題還原，不設計任意 topic split。

| # | 操作步驟 | 前端需要的資料／命令 | 現有 FastAPI endpoint | 現有 MCP tool | 狀態 | 現有可用邊界 | 尚缺契約 | 建議驗收點 |
|---|---|---|---|---|---|---|---|---|
| 1 | 匯入專利 | 上傳 WIPS Excel、觸發匯入、回新增／更新／略過／去重結果 | 無 | 無 | missing | 目前匯入靠 CLI／worker 腳本，非 Web；MCP 無匯入工具 | `POST` 上傳＋建 import job、匯入結果與去重明細回傳 | 上傳 Excel→建 import job→查進度→回四類筆數與去重明細 |
| 2 | 建立一般 workspace | 用專利清單／篩選建立 workspace，回 workspace_id | 無（只有 compose） | 無 | missing | 引擎 `workspace_service.create_workspace()` 已存在，但未接任何入口（僅測試／內部呼叫） | `POST /api/v1/workspaces`（帶 patent_ids／filter） | 建立後於清單／詳情看到、patent_count 正確 |
| 3 | 查看 workspace 清單 | 分頁清單：名稱／專利數／狀態／建立時間／is_composed | `GET /api/v1/workspaces`（limit／offset／status，含 patent_count、is_composed、created_at／updated_at） | `list_workspaces`（僅 active、含 patent_count、無分頁／無 status filter） | ready | Web 支援分頁＋status filter＋is_composed；MCP 僅列 active | 無（前端用 FastAPI 即可） | 三種 status filter＋分頁切片一致、total 對 DB 實數 |
| 4 | 查看 workspace 詳情 | 單一 workspace 欄位、is_composed、直接組合來源 | `GET /api/v1/workspaces/{id}`（含 compose_sources，不含 patent 明細） | `get_workspace_dashboard`（更完整：雙通道 topics＋專利列表，但無 compose_sources 來源鏈） | ready | Web 回基本欄位＋一層組合來源；MCP dashboard 另含 topics／專利但無來源鏈 | 無（基本詳情足夠） | 一般 workspace compose_sources=[]、組合 workspace 兩來源件數正確 |
| 5 | 查看 workspace 專利成員 | workspace 內專利號／標題／申請人／完整度清單（分頁） | 無（詳情明確不回 patent 明細） | `get_workspace_dashboard`（內含專利列表，無分頁） | partial | 只有 MCP dashboard 帶專利列表（Claude 用）；Web 無專屬分頁成員 API | `GET /api/v1/workspaces/{id}/patents`（分頁＋欄位＋技術／功效完整度） | 分頁回專利成員、帶完整度旗標 |
| 6 | 多 workspace 合併 | ≥2 來源建組合 workspace，聯集去重，回件數 | `POST /api/v1/workspaces/compose` | 無 | ready | 來源不動、不繼承 topics／artifact、不自動分群；已有 lineage | 無 | 兩／三來源聯集去重、404／409／422、rollback（已測 10/10） |
| 7 | 建立 clustering calibrate job | source_field、idempotency_key，回 job_id | `POST /api/v1/workspaces/{id}/clustering/calibrate`（白名單 source_field、未知 workspace 404） | 無（calibrate 屬 Web→FastAPI 重負載） | ready | 建 job（queued）不執行，交 worker；回 job_id | 無 | POST→job queued→查進度→完成 result 帶 run_id |
| 8 | 查 job 進度 | status／progress／current_stage／result／attempt | `GET /api/v1/jobs/{job_id}`；另 `GET /api/v1/ready` 帶 worker heartbeat | 無 | ready | 單筆查詢；無「列出某 workspace 全部 jobs」清單 | （選配）`GET /workspaces/{id}/jobs` 清單或事件串流 | 建 job 後輪詢 queued→running→succeeded、result 帶 run_id |
| 9 | 取得三組候選 | 候選 type／k／coherence／diversity／balance／score／說明／是否選定 | `GET /api/v1/clustering/runs/{run_id}/candidates` | `get_candidate_review_payload`（供 Claude 產差異說明） | ready | 需先有 run_id（來自 calibrate job result）；FastAPI 給指標＋既存 llm_explanation，MCP 給產說明用 payload | 無 | run 回三型候選、指標齊全；run 不存在 404 |
| 10 | 使用者選擇並 finalize | 選定 candidate_id、selected_by，建 finalize job | `POST /api/v1/clustering/runs/{run_id}/finalize`（驗 candidate 屬於 run） | `apply_candidate_explanations` 只寫說明、不代選 | ready | 使用者選 candidate→建 finalize job；candidate 不屬於 run 回 422 | 無 | finalize job 完成後寫入 top-level topics、is_selected 標記 |
| 11 | incremental clustering | source_field，處理 workspace 新專利 | `POST /api/v1/workspaces/{id}/clustering/incremental` | 無 | ready | 建 incremental job 處理新專利，與 calibrate 同樣 job 化 | 無（哪些是新專利屬前端顯示邏輯） | 加新專利後 incremental job 完成、reused／new count 正確 |
| 12 | 主題重新命名 | 使用者手動改 topic label（manual、AI 不覆蓋） | 無 rename endpoint | `apply_topic_labels` 只寫 label_source<>'manual' 的 AI label | missing | `workspace_service` 無 rename 函式；只有 AI label 寫回（MCP，且刻意不覆蓋 manual），不能當使用者改名 | `PATCH /api/v1/topics/{id}`（label_source='manual' 手動命名＋guard） | 使用者改名寫入 manual label、AI apply 不覆蓋 |
| 13 | 主題合併 | 選來源 topics、合併後名稱，寫結果與 merge history | 無 merge endpoint | 無 merge 工具（`get_merge_history` 只讀） | missing | 引擎 `workspace_service.merge_workspace_topics()` 已存在，但未接任何入口 | `POST /api/v1/workspaces/{id}/topics/merge`（來源 topics＋結果名稱） | 合併寫入結果 topic＋merge history 一筆、doc_count 正確 |
| 14 | 依 merge history 復原合併 | 讀 merge history／可否復原，並執行還原 | 無 unmerge endpoint | `get_merge_history`（可讀合併歷史與可否獨立復原，但無執行復原） | partial | 讀取半段有：MCP `get_merge_history` 給復原判斷所需資料，引擎 `workspace_service.unmerge_workspace_topics()` 也存在；但無執行入口 | `POST /api/v1/workspaces/{id}/topics/unmerge`（依 merge history 復原，不做任意 split） | 對「可復原」的合併執行 unmerge 還原來源 topics；未曾合併者不可 split |

### 主要缺口與建議實作順序

依產品流程與相依性，缺口集中在**流程頭尾兩段**（進資料、整理 topic）：

1. **匯入專利 API（#1）** — 流程入口，目前只有 CLI；沒有它前端無法自助進資料。
2. **建立一般 workspace API（#2）** — 引擎函式已備，缺一個 `POST /workspaces`；是 #3～#11 的前置。
3. **workspace 專利成員 API（#5）** — 前端清單／勾選必需；MCP dashboard 有資料可參考 shape。
4. **主題合併 API（#13）** — 引擎 `merge_workspace_topics()` 已備，接 endpoint 即可。
5. **復原合併 API（#14）** — 引擎 `unmerge_workspace_topics()` 已備，且只依 merge history 還原（不設計 split）。
6. **主題重新命名 API（#12）** — 需先定義 manual label guard（AI 不覆蓋 manual）再接 `PATCH /topics/{id}`。

中段（#6 合併 workspace、#7～#11 分群到 finalize、#3／#4 清單與詳情）已 `ready`，前端可先串接這段先跑通「建 workspace→合併→分群→選候選→finalize→incremental」主幹。

## 暫不處理

- MCP Server 內部架構
- Claude CLI 安裝與設定
- 後端容器編排
- SQL Server 正式部署
- 使用者權限系統
- 多人協作鎖定
- 任意 topic split
