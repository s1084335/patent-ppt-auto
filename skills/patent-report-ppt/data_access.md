# 自主取證：資料庫地圖與查詢守則

> 給產報告 CLI 的**地圖，不是白名單**。你（CLI）看著圖表與 `report_data.json`，
> 自行判斷還需要什麼證據來把分析寫到 `content_standard.md` 要求的深度，
> 然後用查詢工具直接取。查什麼、查多深，由你對「這頁要論證什麼」的判斷決定。

## 查詢工具

```bash
uv run --no-project --python 3.12 --with "psycopg[binary]" python <skill目錄>/scripts/query_patents.py \
    --sql "SELECT ..."            # 單句 SELECT/WITH；多行建議 --sql-file
```

- 輸出 JSON：`{columns, rows, row_count, truncated}`；預設 500 列上限（`--limit` 最高 2000）
- 連線層**強制唯讀**＋30 秒逾時——你查不壞任何東西，放心探索
- 需要環境變數 `DATABASE_URL`（執行環境已帶入；缺了會明確報錯）

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

### 分群主題

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
