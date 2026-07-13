# 報表欄位對照

資料來源：`derived_layer.report_patent_base`

## 第一版報表

| Report | Purpose | Fields | Rule | Status |
| --- | --- | --- | --- | --- |
| Patent Application Trend | 統計申請年度趨勢 | `application_year`, `patent_id` | 依 `application_year` group by，計算 `patent_id` 數量 | 可支援 |
| Patent Publication Trend | 統計公開/公告年度趨勢 | `publication_year`, `patent_id` | 依 `publication_year` group by，計算 `patent_id` 數量 | 可支援 |
| Patent Jurisdiction Distribution | 統計專利布局國別 | `country_code`, `patent_id` | 依 `country_code` group by，計算 `patent_id` 數量 | 可支援 |
| IPC Classification Distribution | 統計 IPC Main 分布 | `Curr. IPC(Main)`, `patent_id` | 先查完整 main code，再由報表 runner 依使用者選擇輸出 4 階或 5 階 | 可支援 |
| CPC Classification Distribution | 統計 CPC Main 分布 | `Curr. CPC(Main)`, `patent_id` | 先查完整 main code，再由報表 runner 依使用者選擇輸出 4 階或 5 階 | 可支援 |

### IPC / CPC 階層定義

報表 runner 依使用者選擇的階層 collapse 完整 main code，來源 `Curr. IPC(Main)` / `Curr. CPC(Main)` 不改寫。

| 階 | 語意 | IPC 範例 `A01D-034/416` | CPC 範例 `A01D-0034/416` |
| --- | --- | --- | --- |
| 4 階 | subclass | `A01D` | `A01D` |
| 5 階 | main group | `A01D-034`（group 3 碼） | `A01D-0034`（group 4 碼） |

- 4 階取前 4 個英數字（section + class + subclass）。
- 5 階取 subgroup 分隔符 `/` 之前的 main group 字串，保留 IPC 3 碼 / CPC 4 碼原始格式，不截斷。
- 測試報表時 4 階與 5 階兩種都會輸出（`chart_runner` 預設 `--ipc-levels 4 5 --cpc-levels 4 5`），檔名為 `ipc_main_distribution_L4.svg`、`ipc_main_distribution_L5.svg`、`cpc_main_distribution_L4.svg`、`cpc_main_distribution_L5.svg`。
- 使用者要單一階層時，可指定 `--ipc-levels 4` 或 `--cpc-levels 5`。
| Top Patent Applicants | 統計主要申請人 | `applicant_display_name`, `申請人`, `標準化申請人`, `patent_id` | 優先使用 `applicant_display_name`，計算 `patent_id` 數量；預設上限 100，可由前端/CLI 指定 | 可支援 |
| Current Patent Assignee Ranking | 統計目前專利權人/權利人 | `current_assignee_display_name`, `最近專利權人[US,JP,KR,CN,CA,AU]`, `標準當前專利權人[US,JP,KR,CN,CA,AU]`, `patent_id` | 優先使用 `current_assignee_display_name`，計算 `patent_id` 數量；來源專利權人空值不拿申請人混補；預設上限 100，可由前端/CLI 指定 | 可支援 |

## 報表引擎 key

```text
application_trend
publication_trend
country_distribution
ipc_main_distribution
cpc_main_distribution
applicant_ranking
owner_ranking
```

`owner_ranking` 是內部 key，對外顯示名稱使用 `Current Patent Assignee Ranking`。

案件比對需要 AI 介入，暫不放進統計型報表引擎；目前只列出預計欄位。

## 案件比對預計欄位

```text
patent_id
title
授權公告號
審查的公告號
未審查的公開號
申請號
主權項
獨立項[KR,JP,US,CN,EP,IN]
所有權利要求[JP,KR,CN]
比對用權利要求
```

## 可篩選欄位

| Display | Field |
| --- | --- |
| Patent ID | `patent_id` |
| Application Year | `application_year` |
| Publication Year | `publication_year` |
| Application Date | `application_date` |
| Jurisdiction | `country_code` |
| IPC Main | `Curr. IPC(Main)` |
| CPC Main | `Curr. CPC(Main)` |
| Applicant Display Name | `applicant_display_name` |
| Current Assignee Display Name | `current_assignee_display_name` |

## 不納入 `report_patent_base` 的欄位

下列欄位第一版報表暫不使用，避免 `report_patent_base` 過寬。若後續報表需要，再從 Raw/Core 追溯或新增對應 derived 欄位。

```text
source_file_id
raw_record_id
source_file_name
imported_at
database_name
document_kind
patent_type
legal_status
WIPS同族ID
abstract
申請人代表碼
標準當前專利權人代碼[US,JP,KR,CN,CA,AU]
申請人公司代碼
專利權人公司代碼
受讓人公司代碼
refreshed_at
```
