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

### 公司顯示名正規化鏈（2026-07-15 加入代碼對照層）

`refresh_report_patent_base` 產生 display name 的優先序：

1. `derived_layer.company_aliases` 別稱表（人工維護，最優先）
2. **代碼對照**：同一 WIPS 人名代碼（申請人側 `申請人代表碼`、權利人側 `標準當前專利權人代碼[...]`）
   在庫內選一種統一寫法輸出——優先取該碼最常見的標準化名，沒有就取最常見的原始名
   （`mode()`，平手依排序取第一個，deterministic）。保證**同碼必同顯示名**，跨檔匯出的
   名稱漂移不會分裂統計。
3. WIPS 標準化欄（`標準化申請人`／`標準當前專利權人`）
4. 原始名（`申請人`／`最近專利權人`）

無代碼的列（例如精簡匯出）自然落到 3→4，行為與舊版相同。
| Family Country Layout (Active Protection) | 國家佈局：這些發明目前在哪些國家有效 | 來源改為 `derived_layer.report_family_country`（`family_id`, `country_code`） | 依 `country_code` group by，計算 `family_id` 數量（**輸出 alias 沿用 `patent_count`，語意是家族數**）；家族×國家已在 refresh 時去重 | 可支援（2026-07-15 新增） |
| Family Coverage Quality Detail | 家族完整性核對與異常現形 | 來源 `derived_layer.report_family_quality` 全欄 | detail 型；`family_incomplete` 排前面 | 可支援（2026-07-15 新增） |
| Top Cited Patents | 高被引用專利排名 | `(F1)引用文獻數`（migration 0009）＋號碼/標題/申請年/申請人 | detail 型，被引用數 DESC、預設前 50；無引用欄的批次被 exclude_blank 排除；**被引用數是下載時點快照** | 可支援（2026-07-15 新增） |
| Company R&D Energy | 企業研發能量（氣泡圖資料） | `applicant_display_name`＋aggregates：Σ`(F1)引用文獻數`、Σ`發明人數`、引用非空列數 | aggregate 型＋額外聚合欄（`cited_total`/`inventor_total`/`cited_rows`）；`cited_rows=0`＝該公司整批無引用資料，圖表端標「無資料」不畫 X=0 | 可支援（2026-07-15 新增） |
| Patent Lifecycle | 專利生命週期（年度×申請人家數 vs 件數） | `application_year`＋COUNT(DISTINCT `applicant_display_name`) | aggregate 型＋`count_distinct` 聚合（`applicant_count`）；階段判讀由分析者依軌跡判斷 | 可支援（2026-07-15 新增） |

### 額外聚合欄機制（2026-07-15 引擎擴充）

`ReportDefinition.aggregates`＝`(函式, 來源欄, 輸出別名)` tuple 清單，函式白名單在
`report_engine.AGGREGATE_FUNCTIONS`（`sum`／`count`（非空列數）／`count_distinct`／`avg`／`max`），
白名單外直接 raise。聚合別名可用於 `default_order`。年增率折線（`application_growth.svg`）
不是報表定義，是 `chart_runner.compute_yoy_growth` 由申請趨勢 rows 衍生計算（連續年才計）；
技術別成長折線待分群引擎產出 topic 後再加。

### 國家佈局口徑（現有保護，2026-07-14 定案；2026-07-15 降為申請國層級）

- 統計單位＝**同族（發明）×申請國（受理局）**：`refresh_report_family_country` 從 `report_patent_base` 逐列計算——存活列貢獻 `country_code`×家族（EP 以「EP」桶貢獻，地圖上以橘色區域標示呈現）；group by `WIPS同族ID` 去重。
- **EP 展開暫不做**（2026-07-15 使用者裁決）：`build_family_country_dataset(expand_ep=True)` 保留完整 EPC 生效國展開與三規則邏輯（① 成熟件用 `EPC有效國家`；② 剛授權隔離「生效程序進行中」；③ 到期件貢獻 0），含測試——要啟用時改 refresh 呼叫即可。
- **状态正規化**：`legal_status` 經 `backend/app/mappings/legal_status.py` 分為 alive/dead/pending/unknown；只有 alive 貢獻佈局；unknown 計數現形。
- **完整性核對**：`WIPS同族各國家文獻數量(申請為準)` 固定 7 桶（US/EP/PCT/JP/KR/CN/etc），只比對 5 個國家桶；expected≠actual 的家族標 `family_incomplete`。
- `WIPS同族ID` 空的存活件以 surrogate 家族（`P{patent_id}`）保留並標 `is_surrogate_family`。
- 兩張家族報表 `supports_patent_ids=False`：filters／analysis 快照由引擎轉譯成「選中專利所屬家族」的家族集合，回這些家族的**完整**佈局（含家族全體成員，可能出現篩選外的國家）；不帶篩選＝全庫。surrogate 家族 id 規則（`P{patent_id}`）SQL 端與 `transforms/family_layout` 一致，由測試釘住。
- 執行順序：`refresh_report_patent_base` → `refresh_report_family_country` → 報表/圖表。

## 報表引擎 key

```text
application_trend
publication_trend
country_distribution
ipc_main_distribution
cpc_main_distribution
applicant_ranking
owner_ranking
family_country_layout
family_quality_detail
top_cited_patents
company_rd_energy
lifecycle
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
未審查的公開號(轉換後)
申請號
申請號(轉換後)
主權項
獨立項[KR,JP,US,CN,EP,IN]
所有權利要求[JP,KR,CN]
比對用權利要求
```

未審查公開號與申請號的原值／轉換後欄在 `report_patent_base` 中相鄰；案件比對、
前端與 PPT 使用 `(轉換後)` 欄，原值只供來源追溯。

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

> 變更（2026-07-15）：`legal_status`、`WIPS同族ID` 因國家佈局報表需要，已自本清單移出並納入
> `report_patent_base`（migration 0005，同時納入 `WIPS同族各國家文獻數量(申請為準)`、
> `EPC有效國家[EP]`、`EPC無效國家[EP]`）。

```text
source_file_id
raw_record_id
source_file_name
imported_at
database_name
document_kind
patent_type
abstract
申請人代表碼
標準當前專利權人代碼[US,JP,KR,CN,CA,AU]
申請人公司代碼
專利權人公司代碼
受讓人公司代碼
refreshed_at
```
