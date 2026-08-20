# 自主取證：資料庫地圖與查詢守則

> 給產報告 CLI 的**地圖，不是白名單**。你（CLI）看著圖表與 `report_data.json`，
> 自行判斷還需要什麼證據來把分析寫到 `content_standard.md` 要求的深度，
> 然後用查詢工具直接取。查什麼、查多深，由你對「這頁要論證什麼」的判斷決定。

## 查詢工具

取證一律走 **MCP 唯讀工具**（工具清單即能力清單；你拿不到、也不需要任何資料庫憑證）。

### 先問「快照答不答得出來」

多數問題不必查資料庫——報表快照已經把彙總算好了：

| 工具 | 回答什麼 |
|---|---|
| `list_report_catalog()` | 有哪些報表、各自回答什麼問題（**第一步先看這個**） |
| `preview_report_rows(report_key, snapshot_id)` | 先看幾列與欄位長相，再決定要不要細查 |
| `query_report_evidence(report_key, snapshot_id, filters=…)` | 取可直接引用的數據列 |
| `get_chart_metadata(report_key, snapshot_id)` | 這張圖在畫什麼（寫判讀前要知道） |
| `lookup_company_evidence(applicant, snapshot_id)` | 單一公司的具名證據 |
| `lookup_topic_evidence(topic_key, snapshot_id)` | 單一主題的件數、家數、代表專利 |
| `lookup_patent_evidence(patent_ids, snapshot_id)` | 點名某幾件時的專利號與標題 |

以上都收 typed 參數、綁 `snapshot_id`，回傳帶 `evidence_ref` 可直接放進敘述。

### 快照答不出來才查資料庫

```
query_database(sql="SELECT ...", limit=500)
```

- Workspace scoped narrative rule: when `report_data.json.parameters.workspace_id`
  exists, `query_database` is limited to row-level patent evidence. Do not use it
  for aggregate claims (`COUNT`, `SUM`, `GROUP BY`, window functions). Use
  `query_report_evidence()` / snapshot rows for aggregate statements.
- Scoped `query_database` queries must return `patent_id` or `id`; rows outside
  the report workspace are filtered by the MCP server and must not be cited.
- 單句 `SELECT`／`WITH`；連線層**強制唯讀**＋30 秒逾時——你查不壞任何東西，放心探索
- 預設 500 列、最高 2000；輸出 `{columns, rows, row_count, truncated, evidence_ref}`
- ⚠ `truncated=true` 代表**還有沒給你的資料**，不要當成全部就下結論

什麼時候需要它：個別案件清單、完整同族、快照沒有的交叉統計
（例如「某公司在某年的每一件案子」）。這些是彙總表答不出來的問題。

### 關鍵字找專利：先用 search terms

若要用公司名、第二申請人、第二專利權人、受讓人、發明人、IPC/CPC All 任一分類碼、
法律狀態或專利號片段找專利，請查 `derived_layer.patent_search_terms`：

```sql
SELECT DISTINCT st.patent_id
FROM derived_layer.patent_search_terms st
WHERE st.term_lookup LIKE '%創科%'
ORDER BY st.patent_id
LIMIT 200
```

不要用 `core_layer.patents` + `core_layer.patent_people` 的多欄 ILIKE 掃大表；那會漏掉
WIPS `A | B | C` 欄位中的第二個以後的值，也繞過 trigram index。拿到 patent_id 後，
再 JOIN `core_layer.patents` 或 `derived_layer.report_patent_base` 取證。

⚠ 數字**只能**來自這些工具或你手上的選圖數據，不得自行推算或憑印象填。
每個帶數字的敘述都要有 `evidence_ref`。

## 界定範圍（第一步永遠是這個）

`report_data.json` 的 `parameters.workspace_id` 是本次報告的範圍。成員清單：

```sql
SELECT patent_ids_json FROM app_layer.workspaces WHERE workspace_id = <id>
```

之後所有查詢都應以 `WHERE p.id = ANY(ARRAY[...])`（或 JOIN 成員清單）限定範圍。
⚠ 不限範圍就查＝把別的 workspace 的資料混進報告。

## 資料地圖

### `core_layer.patents`（別名 p）——一專利一列，證據主倉

| 欄位 | 語意／用途 |
|---|---|
| `id` | 內部主鍵，關聯一切 |
| `title`／`abstract` | 中文標題／摘要 |
| `"摘要(原文)"` | 原文摘要（中文缺漏時的備援） |
| `"主權項"`／`"獨立項[KR,JP,US,CN,EP,IN]"`／`"所有權利要求[JP,KR,CN]"` | 請求項全文——**機構層技術描述的原料** |
| `"效果 摘要[US,EP,PCT,JP,KR,CN,TW]"`／`"解決課題 摘要[US,EP,PCT,JP,KR,CN,TW]"` | 功效／課題摘要 |
| `"文獻備註"` | 平台 AI 為每件寫的 2–3 句技術摘要——**快速掃讀整批專利的首選** |

### Key Player 深入：從 `patent_ids` 直接查

`report_data.json` 的 `applicant_strength_profile` 每列都帶 `patent_ids`
（該申請人**全部**專利 id）。要寫出機構層的技術描述，就從這裡下去：

```sql
SELECT id, "公開公告號", "發明名稱", "文獻備註"
FROM core_layer.patents
WHERE id IN (101, 205, 337)
```

⚠ 先讀 `文獻備註` 掃全貌，鎖定幾件之後再撈 `請求項`／`獨立項` 看細節——
反過來先撈長文字欄會撐爆 context（見下方守則 4）。
| `application_year`／`application_date` | 申請年／日 |
| `"授權公告日"`／`"未審查的公開日"` | 授權／公開日期（文字格式） |
| `legal_status` | 法律狀態 ⚠ 可能是簡體字面（如「审查中」），寫進報告前轉繁 |
| `patent_type` | P＝發明、U＝新型 ⚠ 設計案也標 P，判設計看 document_kind |
| `document_kind` | 文獻種類；**S＝外觀設計**（三分法唯一判準） |
| `country_code` | 受理國 |
| `"WIPS同族ID"` | 同族識別——**同族合併（件→族）靠它 GROUP BY** |
| `"WIPS同族各國家文獻數量(申請為準)"` | 同族在各國的文獻數（文字描述欄） |
| `"(F1)引用文獻數"`／`"(B1)引用文獻數"` | 前向／後向引用數（文字，轉數字要驗） |
| `"優先權號"`／`"優先權國家"`／`"優先權日"` | 優先權鏈 |
| `"Orig. IPC(Main)"`／`"Curr. IPC(Main)"`（CPC 同構） | 主分類碼 |
| `"授權公告號"`／`"申請號"`／`"申請號(轉換後)"` | 對外引用專利號用 |

### `core_layer.patent_people`（別名 pp，patent_id 一對一）

`"申請人"`（`A | B` 多值分隔）／`"發明人"`／`"最近專利權人[US,JP,KR,CN,CA,AU]"`／
`"最近受讓人[US,KR,CN]"`／`"發明人數"`／`"申請人數"`

### `derived_layer.report_patent_base`（別名 rpb，patent_id 一對一）

**收斂後顯示名**在這裡：`applicant_display_name`（主申請人）、
`current_assignee_display_name`、`recent_assignee_display_name`、`"授權公告年"`。
⚠ 報告中的公司名一律用收斂名，不用原始字面（同公司多寫法會被當成多家）。

### `derived_layer.report_patent_applicant_expanded`——一（專利×申請人）一列

共同申請展開：`patent_id`、`applicant_display_name`（每列一位、已收斂）、
`is_primary`（是否第一順位）。**算「某申請人涉入哪些案」用這張**，
⚠ 件數加總會大於專利總數（共同申請重複計），屬預期行為、引用時要註明口徑。

### 技術／功效主題

`derived_layer.topic_assignments`：`run_id`／`patent_id`／`topic_key`。
`run_id` 對應 `parameters.topic_run_id`（技術／功效兩通道各一）。
⚠ **主題標籤、摘要、代表專利直接用 `report_data.json` 的 cluster 報表**，
不要去 DB 解 `topic_state_json`（結構複雜且非穩定契約）；
DB 端只拿 assignments 做「某主題有哪些專利」的展開，再 JOIN patents 讀內容。

### 其他

- `derived_layer.company_aliases`：`"別稱"` → `"公司中文名稱"`／`"正規化名稱"`
  （`review_status='confirmed'` 才算數）——判斷歸戶關係用
- ⚠ `core_layer.patent_attributes` **不要碰**：只剩沒被任何功能使用的欄位

## 查詢要領（坑）

1. 中文／含符號欄名一律雙引號：`p."WIPS同族ID"`
2. 空值多為空白字串：判有值用 `NULLIF(BTRIM(col), '') IS NOT NULL`
3. 數字欄多為文字型別：`CASE WHEN BTRIM(col) ~ '^[0-9]+$' THEN col::int END`
4. 長文字欄（請求項）一次撈太多會撐爆 context——先用 `"文獻備註"` 掃讀全貌，
   再對**要深寫的少數專利**逐件取請求項

## 取證的判斷方式（示範思路，不是清單）

看到圖表出現「值得寫」的訊號時，追進去把證據拿到手：

- 某申請人件數領先 → 撈它的逐年×主題×文獻備註 → 才寫得出「技術演進邏輯」
- 某年件數暴增 → 撈該年清單看 `WIPS同族ID` 分組 → 分辨真爆發 vs 同族延伸
- 某主題件數集中 → 撈該主題全部案的獨立項 → 判斷是一個平台還是多個方向
- 兩家常一起出現 → 查展開 VIEW 的共同案＋各自獨立案 → 寫合作結構
- 想講「牆」→ 把該區塊每件的權利人／狀態／地區攤開 → 確認牆是活的（排除失效案）

## 證據紀錄（可追溯性，硬要求）

每一頁用到自查證據時，把「查了什麼、看了哪幾件」記進 narratives.json 的
`evidence`（格式見 `report-narrative-flow.md`）。沒有記錄的深入描述，
驗收時視同沒有依據。
