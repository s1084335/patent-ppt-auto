# Patent Core Field Reclassification Spec

> Legacy source：本檔只保留 A1-A4 與 A5 驗收的詳細原始需求。該變更已驗收並 archive（`openspec/changes/archive/complete-core-field-reclassification/`）；現行契約見 `openspec/specs/`（DAT-006／RPT-008／EXP-007），兩者衝突時以 OpenSpec 為準。

目標讀者：Claude / coding agent

本規格只描述要做什麼與驗收標準。開始實作前，請先讀專案根目錄的 `AGENTS.md`、全域規則，以及目前 migration head、schema、mapping、importer、derived/report/query 程式碼。不得只依本文件推測現況。

本規格的穩定背景落點：`D:\力山\.agents\context\patent-db-claude-plan.md`。若實作時查到現況與本規格不一致，必須先更新本規格與該 context，再繼續動工。

## 目標

整理 WIPS 專利欄位的資料表歸屬：

```text
會被任何程式用到的專利欄位 -> core_layer.patents 或 core_layer.patent_people
完全沒有被分析、分群、報表、查詢、案件比對使用的 WIPS 欄位 -> core_layer.patent_attributes
完整原始列資料 -> raw_records.raw_data
```

這裡的「會被用到」包含：

```text
分群輸入
AI note / AI 補分輸入
報表 derived layer
app layer 查詢與前端顯示
案件比對 / PDF 取得
統計、篩選、排序、group by
```

完成後，`patent_attributes` 不應再保存任何目前程式會讀取的專利欄位；它只作為未使用 WIPS 欄位的整理後保存區。`raw_records.raw_data` 仍必須完整保留原始匯入列，作為歷史追溯來源。

## 資料表歸屬規則

### core_layer.patents

放單一專利本身的欄位，只要目前或已規劃功能會用到，就應放在 `patents`。

包含但不限於：

```text
專利識別號碼
申請日 / 公開日 / 公告日等專利日期
title / abstract / claims
分群與 AI 分析使用的文字欄位
legal status
family / EPC / citation 等報表用專利事實
PDF / detail link 等案件比對或前端需要的專利連結
Main classification
```

### core_layer.patent_people

放與人、公司、權利人、代理人、受讓人、讓與人、宣告人相關的欄位。

包含但不限於：

```text
申請人
標準化申請人
發明人
代理人
最近專利權人
目前專利權人
最近受讓人
讓與人
宣告人
申請人代碼 / 國家等人員或公司附屬資訊
申請人數
發明人數
```

### core_layer.patent_attributes

只保留目前不被程式使用的 WIPS 欄位。若某欄位未來開始被報表、分析、分群或查詢使用，必須先搬出 attributes，再讓新功能讀取。

## 本次必須搬出的欄位

以下欄位目前從 `patent_attributes` 被讀取或已屬於近期分析輸入，必須改為 core table 欄位。

### 搬到 core_layer.patents

```text
摘要(原文)
未審查的公開日
授權公告日
優先權號
優先權國家
優先權日
詳細查看連結(登入)
文圖像文件(PDF)連結
WIPS同族各國家文獻數量(申請為準)
EPC有效國家[EP]
EPC無效國家[EP]
(F1)引用文獻數
(B1)引用文獻數
解決課題 摘要[US,EP,PCT,JP,KR,CN,TW]
```

注意：

```text
grant_year / 授權公告年可以由 授權公告日 衍生。
若現有 derived/report table 需要 materialized 年份，應在 derived layer 產生，不必把 grant_year 當成 WIPS 原欄位塞回 patents。
```

### 搬到 core_layer.patent_people

```text
發明人數
申請人數
```

`發明人數` 目前已被報表 derived 使用。`申請人數` 語意上屬於 people 統計，應與人員資料放在一起。

## 暫不處理或需另案決策

本節是動工前的決策閘門。Claude 不得自行替使用者吸收決策；若實作需要跨過本節列出的邊界，必須先停下回報選項、建議答案與影響。

### All classification 欄位

`Orig. IPC(All)`、`Orig. CPC(All)`、`Curr. IPC(All)`、`Curr. CPC(All)` 等 All classification 欄位不應在本次為了分析而直接塞進 `patents`。

目前建議：

```text
Main classification -> patents
All classification 未使用時 -> patent_attributes
All classification 若要分析 -> 另設 patent_classifications 展開表
```

原因：All 欄位是多值分類碼，若整串文字直接放進 `patents` 做 group by 或統計，會造成統計失真。若使用者明確要求不得新增 classification table，再改採把 All 文字放到 `patents`，但必須在 PR/交付說明標註風險。

建議答案：本次不要搬 All classification。若後續真的要分析 All classification，另案設計 `patent_classifications` 展開表。

### 其他 AI 摘要欄位

以下欄位若目前沒有任何程式或規格使用，先不要搬：

```text
AI摘要[US,EP,PCT,JP,KR,CN,TW]
技術領域 摘要[US,EP,PCT,JP,KR,CN,TW]
解決手段 摘要[US,EP,PCT,JP,KR,CN,TW]
特徵 摘要[US,EP,PCT,JP,KR,CN,TW]
```

例外：若實作前確認 AI note、分群或報表已經使用其中任一欄位，該欄位必須跟著搬到 `patents`，不能留在 attributes。

建議答案：本次只搬 `解決課題 摘要[US,EP,PCT,JP,KR,CN,TW]`。其他 AI 摘要欄位等程式或規格明確使用時再搬。

### 文獻備註

`文獻備註` 目前已由 migration 搬入 `core_layer.patents`，且作為 AI note / 前端顯示欄位使用。實作時不得再把它視為 `patent_attributes` 欄位，也不得新增第二份落點。

建議答案：維持 `patents."文獻備註"` 為唯一整理後落點。

### attributes 歷史追溯

本次目標會讓已搬欄位不再存在於 `patent_attributes`，因此 attributes 不再保存這些欄位的 per raw_record 寬表快照。完整來源歷史仍由 `raw_records.raw_data` 保存。

建議答案：接受上述追溯邊界；不要為已搬欄位保留第二份 attributes 副本，避免同一資訊兩處落點。

## 實作範圍

必須同步更新以下層級：

```text
alembic migrations
backend/app/mappings/wips.py
backend/app/importers/wips_importer.py
backend/app/app_layer/patent_queries.py
backend/app/comparison/target_source.py
backend/app/derived/refresh_report_patent_base.py
backend/app/reports/*
tests/*
docs/import_rules.md
openspec/specs/patent-data-model/ 或其他現行 schema 文件
```

如果 repo 內有新 migration 尚未合併或尚未上線，先確認 active head，再決定新的 migration revision 要接在哪一個 revision 後面。

## Migration 要求

新增 migration 時必須：

```text
1. 在 core_layer.patents 新增本規格指定的 patents 欄位。
2. 在 core_layer.patent_people 新增 發明人數、申請人數。
3. 從 core_layer.patent_attributes 回填既有資料。
4. 回填時採用每個 patent 的最新非空值，不得用空值覆蓋既有非空值。
5. 對 EPC有效國家[EP] / EPC無效國家[EP] 這類成對欄位，必須避免取到不同 raw_record 造成語意不一致。
6. 程式都改讀 core table 後，才從 patent_attributes 移除已搬出的欄位。
7. downgrade 必須能把欄位加回 patent_attributes，並從 core table 回填。
```

注意：`patent_attributes` 原本是一筆 raw_record 對一列的寬表；搬到 `patents` / `patent_people` 後，這些欄位會變成每件專利的 canonical current value。原始歷史仍保留在 `raw_records.raw_data`，不得刪除 raw data。

## Importer / Mapping 要求

更新 `backend/app/mappings/wips.py`：

```text
把本規格列為 patents 的 WIPS source 欄位移入 PATENT_FIELDS。
把本規格列為 people 的 WIPS source 欄位移入 PEOPLE_GROUPS / PEOPLE_FIELD_COLUMNS。
確保 ATTRIBUTE_FIELDS 不再包含已搬出的欄位。
```

更新 `backend/app/importers/wips_importer.py`：

```text
normalize_record 要把新欄位放入 patent 或 people。
upsert_patent insert/update SQL 要包含新 patents 欄位。
people replace/update 邏輯要包含 發明人數、申請人數。
replace_attributes 不得再寫入已搬出的欄位。
_UPDATE_COLUMN_PARAMS 必須與 patents 欄位同步。
```

主資料更新規則維持既有策略：

```text
既有值為 NULL，新來源有值 -> 寫入新值
既有值有值，新來源為 NULL -> 不更新
既有值有值，新來源相同 -> 不更新
既有值有值，新來源不同 -> 依既有 incoming_source_priority 策略更新，差異由 raw_records 保留追溯
```

## Reader / Derived 要求

更新所有讀取端，避免 runtime 再為已搬出的欄位讀 `patent_attributes`。

至少檢查：

```text
backend/app/app_layer/patent_queries.py
backend/app/comparison/target_source.py
backend/app/derived/refresh_report_patent_base.py
backend/app/reports/report_definitions.py
backend/app/reports/chart_runner.py
```

完成後，`rg "patent_attributes" backend/app` 只應剩下：

```text
importer 寫入真正未使用欄位
schema / migration / diagnostic 程式
仍明確保留 attributes 的非 runtime 路徑
```

若 app query、comparison、derived/report 還需要讀 `patent_attributes`，必須確認該欄位是否漏搬；不能用「因為比較快」或「暫時先 join」規避本次目標。

## 測試要求

先補測試，再實作。

至少需要：

```text
preflight evidence:
  重新查目前 migration head、patents / patent_people / patent_attributes 實際欄位。
  重新查 runtime 對 patent_attributes 的讀取點。
  把查證結論回寫到 D:\力山\.agents\context\patent-db-claude-plan.md。

mapping contract test:
  本規格列出的欄位不在 ATTRIBUTE_FIELDS。
  patents 欄位存在於 PATENT_FIELDS。
  people 欄位存在於 PEOPLE_FIELD_COLUMNS。

importer normalization test:
  WIPS input row 會把欄位寫進 patent / people。
  attributes payload 不含已搬出的欄位。

upsert/update test:
  新 patents 欄位 insert/update 行為正確。
  空值不得覆蓋非空值。

migration test 或 migration smoke:
  upgrade 後欄位存在。
  既有 attributes 資料能回填到 core table。
  downgrade 能回填 attributes。

reader tests:
  patent_queries 不再依賴 attributes 取已搬出欄位。
  target_source 的 PDF URL 從 patents 取得。
  refresh_report_patent_base 從 patents / patent_people 取得報表欄位。
```

## 驗收標準

完成時必須能提供以下證據：

```text
1. 新 migration 檔案與 head 狀態。
2. 已搬出欄位的 mapping 歸屬清單。
3. rg 結果：runtime reader 不再用 patent_attributes 讀已搬出欄位。
4. 測試結果。
5. 若有 DB 可用，提供 alembic upgrade head 與 derived refresh smoke 結果。
6. 說明 patent_attributes 剩下哪些欄位類型，以及為何判定目前未使用。
```

建議驗證指令：

```powershell
uv run pytest tests/test_wips_mapping.py tests/test_wips_importer.py
uv run pytest tests/test_applicant_split.py tests/test_report_analysis_types.py tests/test_chart_sections.py
uv run pytest
```

若本機 DB 可用，再執行：

```powershell
uv run alembic upgrade head
uv run python -m backend.app.derived.refresh_report_patent_base
```

實際指令名稱以 repo 目前 scripts / modules 為準；若上述 module path 已改，請先用 `rg` 查證後再跑。

## 不可做事項

```text
不得刪除 raw_records.raw_data。
不得把授權公告號、未審查公開號、審查公告號、申請號混成單一 publication_number。
不得為了通過測試把 reader 暫時留在 patent_attributes。
不得把未確認會用到的欄位大量塞進 patents。
不得重寫 unrelated dirty files。
不得改變既有 company normalization 規則。
不得拆多人名、多公司名或多分類碼，除非另案明確要求。
```

## 交付格式

交付時請用繁體中文摘要：

```text
修改摘要
資料表欄位變更
搬移欄位清單
attributes 剩餘欄位定位
驗證方式與結果
風險 / 後續決策，特別是 All classification 是否另建展開表
```
