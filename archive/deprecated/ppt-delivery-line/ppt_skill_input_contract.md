# PPT Skill 輸入契約

## 2026-07-22 Market Evidence 線的正式用途

市場資料在本簡報契約中只用來補足外部市場、需求、玩家與痛點證據，不用來計算專利件數，也不得反推市場規模或市占率。

Market evidence 對應模板頁面：

- 第 2 頁「研發方向建議」：市場痛點與市場玩家只作為研發方向輔助訊號，仍須同時參照正式 topic、機會矩陣與代表性專利。
- 第 7 頁「專利訊號 × 客戶痛點」：每個正式 `topic_code` 對應使用者確認後的 `high / medium / low / unknown` 痛點等級與來源。
- 第 9 頁「專利 Key Players × 市場 Key Players」：專利 key players 來自報表引擎；市場 key players 來自已確認 market evidence 或使用者確認資料。
- 第 10 頁「市場規模、區域趨勢與銷售對象」：主資料來自已確認 market evidence 與彙總結果。

PPT Skill 的資料進入順序：

1. `report_context` 定義分析範圍與專利快照。
2. 報表引擎提供現有 report key 的結構化 rows 與 chart artifacts。
3. Claude CLI 只提供候選 market evidence。
4. 使用者確認後，market evidence 才進正式資料。
5. 報表引擎讀正式 market evidence 產生附錄 3 payload。
6. Claude CLI / PPT Skill 只根據正式資料寫敘事，不自行新增數字。

PPT Skill 若遇到市場資料不足，必須在 `warnings` 標記缺口，不得用 AI 推測補滿。

更新：2026-07-22  
狀態：第一版契約，供 Claude CLI / PPT Skill 依模板產生專利情報簡報。  
模板：`docs/reference/報告範例/自走式割草機_專利情報整合分析_20260710.pptx`

## 目標

PPT Skill 只負責把已確認的資料、圖表、AI 敘事草稿與模板版型組成簡報。  
報表引擎負責可重現的數據與圖表；Claude CLI / PPT Skill 負責文字敘事、限制說明、研發建議與頁面組裝。

原則：

- 數據為主、圖表為輔；傳給 Claude CLI / PPT Skill 時，必須同時提供圖表 artifact 與該圖表對應的結構化數據。
- 表格數據不透過 CSV 傳遞；以 API / workflow output / analysis output 的 JSON payload 傳遞。
- PPT Skill 不回寫 core 專利資料、不修改正式 topic、不自行新增 market evidence。
- Market evidence 必須先經候選暫存與使用者確認，才能進入正式報告。
- 案件比對 PDF/PPT 與專利情報報表 PPT 是獨立流程，不共用敘事模板。

## 最小輸入 Payload

```json
{
  "report_context": {
    "analysis_id": 1,
    "report_version": "report_trial_20260722_001036",
    "workspace_id": 2,
    "workspace_name": "範例 workspace",
    "scope_label": "自走式割草機",
    "generated_at": "2026-07-22T00:10:36+08:00",
    "patent_count": 226,
    "application_year_range": [2014, 2026],
    "publication_date_range": ["2020-01-01", "2026-05-27"],
    "filters": {},
    "patent_id_snapshot": ["US123...", "TW112..."]
  },
  "topics": {
    "topic_version": "final",
    "technical_topics": [],
    "effect_topics": []
  },
  "reports": [],
  "market_evidence": [],
  "user_confirmed_inputs": {
    "pain_points": [],
    "market_scope": {}
  },
  "artifacts": []
}
```

## Report Context

`report_context` 是整份 PPT 的追溯根。

必填欄位：

- `analysis_id`：本次分析版本。
- `report_version`：本次報表輸出版本或 run 目錄 key。
- `workspace_id`：workspace 報告填 workspace id；全庫報告可為 `null`。
- `scope_label`：顯示在封面的主題名稱。
- `generated_at`：報告產生時間。
- `patent_count`：本次分析範圍的 distinct patent 件數。
- `application_year_range`：申請年範圍。
- `publication_date_range`：公開或公告資料範圍。
- `filters`：使用者篩選條件。
- `patent_id_snapshot`：本次計算用專利號快照，使用專案既有專利號機制。

## Topic Payload

只使用使用者選定、合併／改名後的正式 topic 版本，不使用候選方案。

每個 topic 至少包含：

```json
{
  "topic_code": "T01",
  "label": "電池熱管理",
  "source_field": "wips_independent_claims",
  "patent_count": 18,
  "top_applicants": [
    {"company": "Company A", "patent_count": 5},
    {"company": "Company B", "patent_count": 3},
    {"company": "Company C", "patent_count": 2}
  ],
  "representative_patents": [
    {
      "patent_number": "US12345678",
      "title": "Example title",
      "abstract": "Short abstract",
      "applicants": ["Company A"],
      "assignees": ["Company A"],
      "application_year": 2023
    }
  ]
}
```

規則：

- 技術主題與功效分類分開給。
- 未分類專利保留為 `未分類` topic，供使用者後續人工歸類。
- `representative_patents` 預設每 topic 取 topic probability 最大的前 5 筆；不截斷單筆文字，由上游控制 payload 長度。

## Report Payload

每個報表固定給「結構化數據 + 圖表 artifact」。

```json
{
  "report_name": "applicant_country_distribution",
  "title": "競爭者布局：公司 × 國家交叉表",
  "description": "報表用途與統計口徑",
  "data": {
    "columns": ["company", "US", "CN", "EP", "TW"],
    "rows": []
  },
  "chart_artifacts": [
    {
      "artifact_key": "reports/v1/applicant_country_distribution.html",
      "format": "html",
      "sha256": "...",
      "title": "公司 × 國家交叉表"
    }
  ],
  "notes": []
}
```

規則：

- 報表引擎輸出完整 rows，不只輸出圖。
- PPT Skill 可摘要表格，但不得丟失原始統計口徑。
- 圖表 artifact 使用相對 artifact key，不寫死本機路徑。

## 現有報表整合口徑

PPT Skill 必須優先使用報表引擎已存在的 report key 與 chart artifact，不得只照模板文字自行想像資料。

目前可直接整合的報表：

| 現有 report key / chart row key | 用途 | PPT 使用方式 |
|---|---|---|
| `application_trend` | 申請年趨勢 | 第 3 頁申請趨勢、封面年份範圍輔助 |
| `publication_trend` | 公開年趨勢 | 第 3 頁補充公開延遲與公開節奏 |
| `country_distribution` | 國家／專利局分布 | 第 5 頁競爭者布局輔助，或附錄補充 |
| `family_country_layout` | 專利家族國家布局 | 第 5 頁保護布局輔助，不取代公司 × 國家矩陣 |
| `ipc_main_distribution` | IPC 分布 | 第 4 頁技術分布輔助 |
| `cpc_main_distribution` | CPC 分布 | 第 4 頁技術分布輔助 |
| `applicant_ranking` | 申請人排名，含最新受讓人件數與名稱 | 第 5 頁競爭者布局、附錄 2 專利 key players |
| `owner_ranking` | 專利權人排名 | 第 5 頁競爭者布局輔助 |
| `recent_assignee_ranking` | 最新受讓人排名 | 第 5 頁公司分析頂部與受讓人變化說明 |
| `applicant_country_distribution` | 公司 × 國家交叉表 | 第 5 頁競爭者布局主表 |
| `applicant_year_matrix` | 申請人 × 申請年泡泡矩陣 | 第 5 頁或附錄的年度布局 |
| `owner_year_matrix` | 專利權人 × 申請年泡泡矩陣 | 第 5 頁或附錄的年度布局 |
| `lifecycle` | 年度申請人家數 × 專利件數 | 第 3 頁或附錄補充布局生命週期 |
| `cluster_topic_table` | 正式主題／功效分類統計表 | 第 4 頁與附錄 1 主資料 |
| `opportunity_quadrant` / `opportunity_quadrant_tech` / `opportunity_quadrant_effect` | 專利密度 × 競爭者結構強度 | 第 6 頁 |
| `pain_point_quadrant` / `pain_point_quadrant_tech` / `pain_point_quadrant_effect` | 專利訊號 × 客戶痛點 | 第 7 頁 |

目前仍需外部或上游補齊的資料：

| 資料 | 來源 | 用途 |
|---|---|---|
| 研發方向建議草稿 | Claude CLI / PPT Skill 根據正式報表與代表性專利產生 | 第 2 頁 |
| 痛點等級與來源 | Claude CLI 產候選，使用者確認後傳入報表引擎 | 第 7 頁 |
| 市場規模、區域趨勢、銷售對象 | Market evidence 候選流程，使用者確認後使用 | 第 10 頁 |
| 市場 key players | Market evidence 或使用者確認資料 | 第 9 頁 |
| PPT 最終敘事文字 | PPT Skill | 全文敘事、限制、建議行動 |

## 章節映射

依模板 10 頁先固定如下：

| 頁次 | 模板章節 | 主要使用現有資料 | 仍需 AI / 使用者輸入 |
|---|---|---|
| 1 | 封面：專利情報分析 | `report_context`、`application_trend`、`publication_trend` | 封面副標與口徑文字 |
| 2 | 研發方向建議 | `cluster_topic_table`、`opportunity_quadrant*`、`pain_point_quadrant*`、代表性專利 | AI 研發方向與建議專利行動，使用者可再修 |
| 3 | 申請趨勢 | `application_trend`、`publication_trend`、`lifecycle` | 高峰、回落與公開延遲的敘事 |
| 4 | 技術分布 | `cluster_topic_table`、`ipc_main_distribution`、`cpc_main_distribution` | 技術結構解讀 |
| 5 | 競爭者布局 | `applicant_country_distribution`、`applicant_ranking`、`owner_ranking`、`recent_assignee_ranking`、`applicant_year_matrix`、`owner_year_matrix` | 競爭者結構摘要 |
| 6 | 機會評估：專利密度 × 競爭者結構強度 | `opportunity_quadrant*` | 象限判讀與研發優先序 |
| 7 | 使用者痛點交叉驗證：專利訊號 × 客戶痛點 | `pain_point_quadrant*` | 使用者確認後的痛點等級、來源與判讀 |
| 8 | 附錄 1：分類技術指標總表 | `cluster_topic_table` 完整 rows | 無；只整理表格與註解 |
| 9 | 附錄 2：專利 Key Players × 市場 Key Players | `applicant_ranking`、`owner_ranking`、`recent_assignee_ranking` | 市場 key players evidence 與口徑限制 |
| 10 | 附錄 3：市場規模、區域趨勢與銷售對象 | 已確認 `market_evidence` 與彙總結果 | 市場範圍確認、區域趨勢與銷售客群敘事 |

## Market Evidence Payload

Market evidence 僅接受已確認資料：

```json
{
  "kind": "market_size",
  "scope": "robot mower",
  "target": "US",
  "source_url": "https://example.com/report",
  "summary": "公開摘要內容",
  "payload_json": {
    "source_name": "Example Research",
    "source_url": "https://example.com/report",
    "published_on": "2025-03-01",
    "reliability": "industry_gov_corp",
    "summary": "公開摘要內容",
    "evidence_excerpt": "Short verifiable excerpt",
    "value": {
      "year": 2025,
      "market_definition": "electric mower",
      "market_size": 12.5,
      "currency": "USD",
      "unit": "billion"
    }
  }
}
```

PPT Skill 可使用 market evidence 做：

- 市場規模區間。
- 區域趨勢。
- 銷售對象。
- 痛點來源說明。
- 專利布局與市場訊號是否一致的敘事。

PPT Skill 不可使用 market evidence 做：

- 推論未公開的付費報告內容。
- 用專利件數推算市場規模或市占率。
- 把 unknown 痛點誤算成 low。

## 輸出要求

PPT Skill 輸出後，至少回傳：

```json
{
  "ppt_artifact_key": "ppt/report_v1.pptx",
  "source_report_version": "report_trial_20260722_001036",
  "source_analysis_id": 1,
  "slides": [
    {
      "slide_no": 1,
      "section": "封面",
      "data_sources": ["report_context"]
    }
  ],
  "warnings": []
}
```

若資料不足：

- 不得硬產結論。
- 在 `warnings` 標記缺口。
- 該頁可產生「待補資料」版面，但要明確列出缺什麼。
