# 2026-07-17 專利權人正規化原則

匯入流程不得改寫原有專利中的申請人、專利權人、受讓人等來源欄位值。原始專利資料照原樣入庫；公司名稱正規化只透過獨立的專利權人/公司對照表處理。

流程口徑：

```text
原始專利資料照原樣入庫
→ 匯入後掃描公司/專利權人名稱
→ 用專利權人對照表嘗試正規化
→ 找不到對照時建立補全任務
→ Claude Code CLI + Skills + Playwright MCP 查 WIPS 標準專利權人代碼
→ 將申請人代碼 / 公司名稱 / 別稱寫入專利權人對照表
→ 報表統計以正規化公司名稱計算
```

專利權人對照表格式沿用 `docs/reference/專利權人代碼對照表_合併.xlsx`，核心欄位為 `申請人代碼`、`公司名稱`、`別稱`。後續公司排名、專利權人排名、公司×國家矩陣、研發能量等報表，都以正規化公司名稱 group by；找不到對照時才 fallback 原始名稱並標記待補全。
# 專利資料匯入規則

最後更新：2026-07-02

## 核心原則

匯入流程的主要責任是把來源資料放到適合的資料表，不主動改變欄位語意。

除明確列出的去重規則外，匯入時不得做以下處理：

```text
不得混合欄位
不得替欄位改名後覆蓋原語意
不得從多個來源欄位擇一當成同一個欄位
不得拆多人名或多公司名
不得拆分類碼
不得把公告號或公開號混成單一資料欄位
```

## 欄位名稱正規化

WIPS mapping 以目前既有欄位語意為準，不因繁簡體改變分表邏輯。

匯入時允許把常見繁體欄名正規化到既有 mapping，例如：

```text
國家代碼 -> 国家代码
資料庫名稱 -> 数据库名称
申請號 -> 申请号
授權公告號 -> 授权公告号
主附圖 -> 主附图
文圖像文件(PDF)鏈結 -> 文图像文件(PDF)链接
```

注意：

```text
raw_records.raw_data 仍保留 Excel 原始欄名。
整理表分組與主欄位 mapping 使用正規化後欄名判斷。
patents / patent_people / patent_attributes 的實體欄位顯示使用繁體中文或英文。
資料庫實體欄位顯示使用繁體中文或英文，不使用簡體中文。
```

## 來源檔案格式

WIPS 匯入器支援以下來源格式：

```text
XLSX
CSV
TXT
XML
MDB
```

格式差異只影響讀取層，不影響後續分表、去重、欄位保留規則。

讀取後都會轉成同一種列資料：

```text
headers + records
```

再套用相同流程：

```text
patents
patent_people
patent_attributes
patent_sources
raw_records
source_files
```

TXT 依分隔文字處理，會自動偵測常見分隔符號。

XML 支援一般 record/field 結構，包含以 `name` / `field` / `label` / `title` 屬性表示欄位名的節點。

MDB 需本機具備：

```text
pyodbc
Microsoft Access ODBC Driver
```

若缺少 driver，程式會回報明確錯誤，不影響其他格式。

## 原始資料保留

每一列來源資料必須完整保留到：

```text
raw_records.raw_data
```

WIPS 目前為 148 欄，原始欄位與原始值都必須保留。

## 整理後欄位保留

整理後欄位層必須能對照 Excel 來源欄位，不得需要回到 `raw_records` 才知道欄位存在。

規則：

```text
patents:
每一列 Excel 專利資料對應一列 patents。
只放專利本身主欄位、year 欄位，以及識別用的四個號碼欄位。
四個號碼欄位在 patents 以繁體欄位名保存，不混成抽象欄位。

patent_people:
每一列 Excel 專利資料對應一列 patent_people。
欄位直接對應 WIPS 人員來源欄位，例如 申请人、发明人、最近专利权人。
即使某個人員欄位是空白，也保留該欄位，值為 NULL。

patent_attributes:
非 patents 欄位、非 patent_people 欄位，都寫入 patent_attributes。
每一列 Excel 專利資料對應一列 patent_attributes。
剩餘 WIPS 欄位各自成為 patent_attributes 的資料庫欄位。
即使來源值是空白，也保留該欄位，值為 NULL。
```

這樣 `主附图`、`摘要(原文)`、`审查的公告号` 等目前空白欄位，也會在整理後欄位層存在。

## 公告號與公開號

公告號與公開號欄位必須照原來源欄位保存，不得混成 `patents.publication_number`。

WIPS 欄位保存規則：

```text
申请号 -> patents."申請號"
授权公告号 -> patents."授權公告號"
未审查的公开号 -> patents."未審查的公開號"
审查的公告号 -> patents."審查的公告號"
```

不得做：

```text
從 授权公告号 / 未审查的公开号 / 审查的公告号 擇一塞進 publication_number
```

## 公告日與公開日

公告日與公開日欄位必須照原來源欄位保存，不得混成 `patents.publication_date`。

WIPS 欄位保存規則：

```text
授权公告日 -> patent_attributes."授權公告日"
未审查的公开日 -> patent_attributes."未審查的公開日"
审查的公告日 -> patent_attributes."審查的公告日"
```

不得做：

```text
從 授权公告日 / 未审查的公开日 / 审查的公告日 擇一塞進 publication_date
```

## 去重規則

去重使用 WIPS 四種專利識別號碼做 identifier lookup，不使用多欄組合字串完全相等作為合併條件。

WIPS 識別欄位：

```text
授权公告号
审查的公告号
未审查的公开号
申请号
```

規則：

```text
匯入一列時，依序用下列非空欄位查找既有 patents：
1. 授权公告号 -> patents."授權公告號"
2. 审查的公告号 -> patents."審查的公告號"
3. 未审查的公开号 -> patents."未審查的公開號(轉換後)"
4. 申请号 -> patents."申請號(轉換後)"；此項會同時比對相容的 country_code / database_name，降低跨資料庫誤合併。

如果四個識別欄位都空白：
dedupe_key = WIPS_ROW|source_file_id|row_number
```

注意：

```text
dedupe_key 只用於來源追蹤與除錯，不作為專利合併的唯一判準。
授权公告号 保存為 patents."授權公告號"。
审查的公告号 保存為 patents."審查的公告號"。
未审查的公开号 保存為 patents."未審查的公開號"。
申请号 保存為 patents."申請號"。
patents."未審查的公開號(轉換後)" 與 patents."申請號(轉換後)" 為 generated columns，不由 importer 直接寫入。
country_code=TW 時，四位西元年前綴減 1911；非 TW 時轉換後值等於原值。
dedupe 與後續功能一律讀取轉換後欄位，原值只供 WIPS 來源追溯。
```

## 重複資料與差異解決規則

同一個 identifier lookup 命中既有 `patents` 時，不建立新的 `patents` 主資料列。

主資料更新採用來源優先權策略：

```text
resolution_strategy = incoming_source_priority
```

規則：

```text
既有值為 NULL，新來源有值 -> 寫入新值
既有值有值，新來源為 NULL -> 不更新
既有值有值，新來源相同 -> 不更新
既有值有值，新來源不同 -> 主資料更新為新來源值，來源差異由 raw_records / patent_sources / patent_attributes 保留追溯
```

適用資料表：

```text
patents
patent_people
```

不同來源原始資料仍透過 `raw_records`、`patent_sources`、`patent_attributes` 保留追溯。
`patent_attributes` 依來源列保存，一筆 raw_record 對應一列 attributes 寬表，不用來覆蓋主資料。

目前不新增或刪除既有資料表。若未來需要把主表欄位差異做成可直接查詢的事件紀錄，再於資料穩定後另行設計 `import_runs` 或差異紀錄表。

## 原始資料保留與清理策略

短期策略：

```text
完整保留 raw_records + patent_attributes。
進 server 前先確保正確性與追溯能力。
```

重複檔案策略：

```text
同 source_system + file_hash 已存在時，預設跳過整個檔案。
不再重複寫 source_files / raw_records / patents / patent_people / patent_attributes。
```

長期策略：

```text
資料穩定後 90 天再設計 raw_records 壓縮、封存或清理流程。
import_runs 先列為後續需求，目前不新增資料表。
```

## 人員欄位

多人或多公司不得拆分。

WIPS 一格內容直接存一格。

例如：

```text
发明人 = Hiroshi Nojiri | Yoshifumi Morita | Toshikazu Migita
```

匯入後仍應是一格：

```text
patent_people."发明人" = Hiroshi Nojiri | Yoshifumi Morita | Toshikazu Migita
```

不得拆成多列。

人員欄位只做分 table，不做語意合併或跨欄位改寫。

例如：

```text
申请人 -> patent_people."申請人"
标准化申请人 -> patent_people."標準化申請人"
最近专利权人[US,JP,KR,CN,CA,AU] -> patent_people."最近專利權人[US,JP,KR,CN,CA,AU]"
最近受让人[US,KR,CN] -> patent_people."最近受讓人[US,KR,CN]"
```

簡體或繁體來源欄名可透過 mapping 對到繁體整理表欄位，但來源值不做繁簡轉換。
`raw_records.raw_data` 仍保留來源檔的原始欄名與原始值。

## 分類欄位

IPC / CPC / FI / F-term 不拆碼。

分類欄位不另開 table。

WIPS 一個分類來源欄位保存為 `patent_attributes` 的一個欄位：

```text
一件專利 + 一個 raw_record = patent_attributes 一列
分類來源欄位 = patent_attributes 對應欄位
```

欄位值原樣放入：

```text
patent_attributes 的對應欄位
```

不得把同一欄位內多個分類碼拆成多列。

分類欄位用以下欄位辨識：

```text
Orig. CPC(Main) -> patent_attributes."Orig. CPC(Main)"
Curr. IPC(All) -> patent_attributes."Curr. IPC(All)"
```

## 日期年份

`申请日` 可以轉成：

```text
patents.application_date
patents.application_year
```

原因：這是同一欄位的標準型別轉換，不是跨欄位擇一。

公告日/公開日目前不進 `patents.publication_date`，只保留原欄位到 `patent_attributes`。

## 資料表定位

```text
source_files              每次匯入事件，只記來源檔案、hash、筆數、匯入時間
raw_records               原始列完整 JSON
patents                   專利本身欄位，year 欄位可放在此表
patent_sources            patents 與 raw_records / source_files 的對應，保存 dedupe_key
patent_people             人員來源欄位，欄位值原樣不拆、不合併
patent_attributes         其他 WIPS 原欄位寬表，包含分類欄位、圖檔、連結、權利、行政、引用等欄位
patent_source_summary     view，即時計算每筆專利來源檔案與匯入時間
```

## 匯入來源追蹤

系統追蹤欄位只保留能回答兩件事的資料：

```text
這筆資料來自哪個檔案？
這個檔案是哪一次匯入？
```

`source_files` 每次匯入都新增一列，即使是同一個檔案、同一個 hash 也一樣。

保留欄位：

```text
source_system
file_name
file_path
file_hash
record_count
imported_at
```

同一筆專利來自多個檔案或同一檔案多次匯入時，底層用 `patent_sources` 保留每次來源關聯。

給查詢或前端顯示時，使用 view：

```text
patent_source_summary.source_summary
```

`source_summary` 是查詢時由 `patent_sources + source_files` 算出的 JSON 陣列，不實體存進 `patents`。

## 專利欄位與系統欄位分離

`patents` 不放系統追蹤欄位。

允許放在 `patents` 的是專利本身欄位，以及由同一日期欄位抽出的年份欄位：

```text
application_year
publication_year
```

不得放在 `patents` 的系統欄位：

```text
dedupe_key
source_summary
created_at
updated_at
```

`dedupe_key` 放在 `patent_sources`，不放在 `patents`；它保存本列匯入時看到的識別碼快照，不取代四個獨立號碼欄位。

來源檔案與匯入時間不存進 `patents`，由 `patent_source_summary` view 查詢。

## 分類欄位

分類欄位不另開 table。

IPC / CPC / FI / F-term / USPC 等分類欄位因目前規則是不拆碼，所以直接保存到：

```text
patent_attributes
```

並用欄位直接保存：

```text
Orig. CPC(Main) -> patent_attributes."Orig. CPC(Main)"
Curr. IPC(All) -> patent_attributes."Curr. IPC(All)"
```


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
