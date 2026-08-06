## 1. 基準與版面契約

- [ ] 1.1 固定代表性 workspace、report run、資料版本與目前 HTML/PPT 輸出，建立可重現基準
- [ ] 1.2 依「刪除優先、改造其次、新增最後」逐張確認報表 catalog 的保留、改造與淘汰；不建立固定全報表頁序或要求每次全部出頁
- [ ] 1.3 確認單位、分母、時間粒度、申請/公開/核准漏斗、技術/功效雙通道、family ID 與標籤長度契約

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
