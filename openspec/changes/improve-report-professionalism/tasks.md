## 1. 基準與版面契約

- [x] 1.1 固定代表性 workspace、report run、資料版本與目前 HTML/PPT 輸出，建立可重現基準

  基準：workspace 3（滑雪機）、技術通道，產在 `output/_verify/report_baseline/`
  共 29 個檔案（含 web／ppt 雙 profile、`report_data.json`、`artifact_manifest.json`、
  `index.html`）。

  ⚠ **必須帶 `cluster_data` 才是完整基準**：直接跑 `chart_runner` CLI 不帶分群
  資料時，`kp_quadrant`／`opportunity_quadrant` 等分群類圖表**靜默跳過不產**。
  我第一次跑就漏了它們，差點把「Key Player 圖沒產出」誤判成產品缺陷。正式流程
  走 `handle_report_generate` → `_resolve_report_cluster_data` 會帶。

  ⚠ 另發現 `chart_runner` CLI **直接執行連不上 DB**（`.env` 載入藏在
  `clustering/runner.py` 的 import side effect 裡，CLI 沒 import 它就退回
  `localhost:5433`）。已記入 `harden-runtime-security-and-configuration` 的既有
  問題（該 change 的 proposal 明列「靜默連向 localhost:5433」）。
- [x] 1.2 依「刪除優先、改造其次、新增最後」逐張確認報表 catalog 的保留、改造與淘汰；不建立固定全報表頁序或要求每次全部出頁

  ### 盤點結果（2026-08-09，13 張 → 11 張）

  | 線 | 報表 | 回答的問題 | 處置 |
  |---|---|---|---|
  | 時間 | `application_trend`（件數＋家族數） | 何時投入、是真爆發還是同族延伸 | 留 |
  | 時間 | `publication_trend` | 何時獲證 | 留 |
  | 國別 | `country_distribution`（國別×法律狀態） | 各國保護還有效嗎 | 留 |
  | 國別 | `family_country_layout`（同族×申請國） | 保護範圍涵蓋哪些國家 | 留 |
  | 國別 | `applicant_country_distribution`（公司×國家） | 誰在哪些國家布局 | 留（⚠ 使用者明示不動） |
  | 分類 | `ipc_main_distribution` | 技術領域分布 | 留 |
  | 分類 | `cpc_main_distribution` | 技術領域細分 | 留 |
  | 申請人 | `applicant_ranking` | 誰是主要玩家 | 留 |
  | 申請人 | `applicant_year_matrix` | 誰在何時投入 | 留 |
  | 申請人 | `lifecycle`（申請人×法律狀態） | — | **刪** |
  | 申請人 | `applicant_strength_profile`（雷達圖） | — | **刪** |
  | 主題 | `cluster_topic_table` | 有哪些技術主題 | 留 |
  | 主題 | `opportunity_quadrant` | 主題的機會定位 | 留 |

  ### 刪除理由（AGENTS.md 要求留痕）

  **`lifecycle`（申請人×法律狀態）**：兩個維度分別已由 `country_distribution`
  （法律狀態）與 `applicant_ranking`（申請人）回答。⚠ 交叉之後每格件數極少
  （本樣本 60 件、十餘家申請人），圖上看不出任何模式。它原本要回答的
  「誰的專利還有效」，改由 `applicant_ranking` 加註有效件數承接。

  **`applicant_strength_profile`（雷達圖）**：三維強度已在先前收斂（「權利範圍」
  該維度已否決）。⚠ 申請人少於 5 個時雷達圖讀不出東西，而本專案的典型
  workspace 就是這個規模。它要回答的「誰比較強」由 `applicant_ranking` 的
  排序與 `applicant_year_matrix` 的時間分布共同承接。

  ### 國別線為何三張全留（2026-08-09 使用者裁決：照既有測試結論）

  ⚠ 我原本提議合併 `country_distribution` 與 `family_country_layout`——**那是錯的**。
  查 `decisions.md`（2026-07-14／07-15）後確認兩者**單位不同**：前者是專利件數×
  法律狀態，後者是**同族數**×申請國（`decisions.md:2779` 明載「性質不同——換單位
  而非排除」）。合併會把兩種單位混在一張圖上，那比多一張圖更糟。

  ### 先例

  `family_quality_detail` 已於先前用同樣標準刪除（「資料品質稽核不給決策者看，
  家族完整性併入國家佈局頁註記」），本次沿用同一判準：**這張圖是否在回答決策者
  的問題**。
- [x] 1.3 確認單位、分母、時間粒度、申請/公開/核准漏斗、技術/功效雙通道、family ID 與標籤長度契約

  ### 已有唯一定義處（沿用，不重新定義）

  | 口徑 | 定義處 | 定案 |
  |---|---|---|
  | 單位 | `reports/population.py` | 同族合併後仍是「件」（2026-08-05）——⚠ 不寫「家族 48 個」，避免同頁兩種單位 |
  | 分母／母體 | `reports/population.py`（**唯一定義處**） | 每張報表的 rows 已帶 `patent_count`，加總即母體，零額外查詢 |
  | 三層漏斗 | `build_ppt._funnel_*`（Q3，2026-08-05） | 原始 → 同族合併 → 技術主題，封面併 1 格 |
  | 雙通道 | `clustering/sources.SOURCE_SEGMENT_SLUGS` | 技術（獨立項）／功效（效果摘要）各自母體 |
  | 重複計數標示 | `population.OVER_COUNTING_REPORTS` | 申請人報表走展開 VIEW，總和大於件數是刻意的，必須加註 |

  ### ⚠ 本次查出的三個缺口（2.x 要修）

  1. **`publication_trend` 母體必然小於總數但無登記原因**——未授權公告的專利
     沒有公告年。讀者看到「母體 40/55 件」卻沒有解釋，只會認為資料錯誤
     （這正是 A3 母體對帳器當初要解決的問題本身）。要補
     `POPULATION_REASONS` 條目。
  2. **`opportunity_quadrant` 的單位是「主題」不是「件」**——⚠ 沿用件數句型
     會產出「母體 7/55 件」這種**語意錯誤**的註記。需要單位分流或不印。
  3. **`country_distribution` 未登記**——缺 `country_code` 時母體會少，同樣無解釋。

  ### 連帶：1.2 刪除的影響

  ⚠ `lifecycle` 目前登記在 `OVER_COUNTING_REPORTS`（2026-08-07 起走展開口徑）。
  刪除該報表時必須一併清掉這筆登記，否則會留下指向不存在報表的死條目。

## 2. TDD 實作

- [ ] 2.1 Red：為 registry、dataset schema、單位/分母、subclass 門檻、命名發現與 label truncation 新增失敗測試並記錄原因
- [ ] 2.2 Green：最小修改 report definition、transform、renderer 與 narrative input，使資料契約測試通過
- [ ] 2.3 Red：新增 HTML/PPT artifact persistence、零上傳失敗、reader guide／Key Player 可重用內容元件、evidence identity 與雙通道輸出測試；不斷言固定頁碼或固定三頁
- [ ] 2.4 Green：完成必要輸出與持久化，使 job succeeded 對應可讀回 artifact，並讓內容元件可由 goal-driven SlidePlan 消費
- [ ] 2.5 Refactor：測試全綠後移除被取代圖表與重複 narrative/renderer 邏輯

## 3. 驗證與輸出

- [ ] 3.1 執行 report/transform/renderer/narrative 目標測試、相關模組回歸與 `scripts/verify_module.py`
- [ ] 3.2 產生 HTML、goal-driven PPTX、manifest/metadata 與 narratives/evidence artifact，核對檔案存在、選圖／章節、dataset id 與 checksum；不以固定頁數判定成功
- [ ] 3.3 以桌面與行動視窗檢查 HTML，渲染 PPTX 全頁縮圖並檢查截字、重疊、空白圖、圖例與中文字型
- [ ] 3.4 保存前後對照、已知限制與未測項目，由使用者逐項確認內容與視覺品質後才 archive

## 執行紀錄（2026-08-06，Claude；切片 1/3——報表口徑與 registry）

依 Migration Plan 的第一片「口徑／registry」完成三個可獨立驗收的功能切片：

- **S1 報表組合先刪後改（RPT-011）**：刪 `owner_ranking`／`owner_year_matrix`／
  `family_quality_detail`（15→12 張），registry／前端／引擎／population／PPT／
  測試同步；家族完整性依定案降級為國家佈局頁註記（chart_runner 直查 view）。
  留痕＋反向鎖：`tests/test_report_catalog_removals.py`（含 EXCLUDED_FROM_PPT
  必須保留 family_quality_detail 鍵的向後相容守門）。
- **S2 IPC/CPC 出頁門檻（design #5）**：4 階 subclass distinct <3 → 判定寫進
  `report_data.classification_thresholds`（含 reason），PPT 端 `_report_key_has_data`
  單一接縫排除＋manifest `below_threshold_skipped` 現形；**網頁報表照產**。
  舊版本無該鍵 → 行為不變。`tests/test_classification_threshold.py`。
- **S3 趨勢年度四欄（問題 9）**：`application_trend` 加 `family_count`
  （SQL：COUNT(DISTINCT COALESCE(同族ID, patent_id))，無 ID 各算一族）；
  `topic_count`／`new_topic_count` 由 cluster_data 技術通道算（無分群→缺鍵不補 0）。
  圖不改。`tests/test_annual_trend_four_columns.py`（14 條）。

守門：verify_module 新增行 lint 0／覆蓋 98%／CC 唯一超標為 `build_ppt`（F60，
既有債，本次僅改一行，記錄不順手改）。大回歸 969 passed；5 failed 全屬
既有本機 postgres 依賴（stash 比對證實非本輪造成）。

⚠ 未完成（後續切片）：通道資料改版（技術頁概覽欄位／功效頁精簡與 tech_means）、
具名發現敘述（RPT-012）、內容元件（EXP-008/009）、容量誠實驗證（EXP-010）。
⚠ 兩個待使用者決策：`lifecycle` 處置（改版候選未定案）；
`applicant_ranking` 加 legal_status／專利種類維度的視覺設計（通道已被兩段色＋斜紋用滿）。
