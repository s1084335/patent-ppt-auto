## 1. 開工閘門與基線

- [x] 1.1 在獨立功能分支讀取本 change 全部 artifacts，對照 `feat/p1-lifecycle-status-analysis` 的現有 diff 與測試，列出符合、缺漏、衝突；任何衝突先回寫 OpenSpec，不直接吸收未規格化行為。
- [x] 1.2 記錄目標 checkout、branch、HEAD、dirty files、Alembic current/head 與 DB identity；確認不在 `master` 直接動工。
- [x] 1.3 執行專案規定的最小非 DB 測試基線及相關 report/frontend/clustering 測試，保存既有失敗；DB 測試依使用者閘門決定是否本輪執行，不得以未驗冒充通過。

## 2. Slice A：狀態值域與資料模型（DAT-007）

- [x] 2.1 Red：建立九項合法值、四類專利狀態分析 mapping、非法值，以及「前端不得維護第二份值域」的契約測試；實際執行並記錄未實作失敗。
- [x] 2.2 Green：在 `backend/app/mappings/legal_status.py` 建立詳細值域與分析分類唯一來源，讓 Red 最小通過；不得把分類接入 clustering。
- [x] 2.3 Red：建立 migration upgrade、既有資料保存、JSONB `[]` default、NOT NULL、不得新增 table、downgrade 保留 `legal_status` 的契約測試並確認失敗。
- [x] 2.4 Green：新增 Alembic migration，只在 `core_layer.patents` 增加 `legal_status_history`；不得回填虛構歷史或建立新 table。
- [x] 2.5 Refactor/Regression：重跑 mapping 與 migration 契約，核對 schema comments／現有 schema 測試；未取得 DB 閘門時明列尚未執行的 DB 項目。

## 3. Slice B：待登錄查詢與原子首次寫入（WSP-012、ING-011）

- [x] 3.1 Red：建立 paginated pending query 測試，涵蓋只列 TW、NULL/空字串/空白、排除非 TW/已有值，以及由後端回傳九項 `allowed_statuses`。
- [x] 3.2 Red：建立單筆寫入測試，涵蓋合法成功、非法值、找不到、非 TW、已登錄 conflict、同值重送與兩個併發請求只成功一次；斷言目前值與單一 history 同交易收斂。
- [x] 3.3 Green：在專利 query/repository ownership boundary 實作條件式原子更新與 history append，router 只做 request/response 與錯誤映射，不直接寫 SQL。
- [x] 3.4 Red/Green：建立並實作單筆 `report_patent_base.legal_status` targeted projection sync；斷言全量 refresh 後結果相同且未更新無關 derived tables。
- [x] 3.5 Red/Green：補 importer regression，證明 TW incoming 空狀態不覆蓋人工值、不新增 history、不改 raw source。
- [x] 3.6 Refactor/Regression：收斂 transaction 與錯誤型別，重跑 patent API、importer、derived projection 受影響測試。

## 4. Slice C：集中式收合管理區（WSP-012）

- [x] 4.1 Red：建立 frontend contract 測試，涵蓋預設收合、只顯示 pending TW、後端 options 產生下拉、單筆儲存、無批次/查看全部/修改既有入口。
- [x] 4.2 Green：在專利瀏覽畫面加入「TW 專利狀態管理」區塊；每列顯示專利識別資訊、選單及儲存操作，穩定尺寸且不影響既有表格布局。
- [x] 4.3 Red/Green：成功後只移除該列且畫面不跳轉；儲存失敗保留列與選擇並顯示可讀錯誤；空清單顯示完成狀態。
- [x] 4.4 Refactor/Regression：確認前端沒有第二份九項清單，重跑專利瀏覽與 frontend 全區 contract tests。

## 5. Slice D：背景刷新與失敗重試（RPT-009）

- [x] 5.1 Red：建立成功儲存後只 enqueue `report_generate`＋目前 `workspace_id`＋單一狀態分析 report key 的測試；斷言沒有其他 report key、`refresh_derived` 或 clustering/embedding/topic job。
- [x] 5.2 Green：提交資料後 enqueue 單一狀態分析，回傳 `saved`、`refresh_status` 與可用的 `refresh_job_id`；資料交易不得依賴 enqueue 成功。
- [x] 5.3 Red/Green：enqueue 或 worker 失敗時保留狀態/history，前端顯示非阻塞失敗與重試；重試只建立 report job，不再寫狀態或 history。
- [x] 5.4 Red/Green：狀態分析依唯一 mapping 產生 pending/alive/dead/unknown，並驗證 TW 登錄後目前 workspace 的資料與圖表更新。
- [x] 5.5 Refactor/Regression：重跑 reports API、job repository、worker handler、chart artifact 與 frontend progress tests。

## 6. Slice E：分群隔離（CLU-012）

- [x] 6.1 Red：建立狀態登錄前後 clustering source text、freshness hash、existing assignment 完全一致的 regression tests。
- [x] 6.2 Green：移除任何被實作誤接的 legal-status clustering dependency；若 Red 已通過，記錄其證明力且不得為了製造 Green 修改無關程式。
- [x] 6.3 Regression：以 job spy／DB 查詢證明狀態保存、enqueue 失敗與刷新重試皆未建立 clustering、embedding 或 topic jobs，所有 TW 專利仍在既有分析範圍。

## 7. 組合驗收與交付閘門

- [x] 7.1 依專案規則執行目標測試、受影響回歸與 `scripts/verify_module.py`；逐項記錄 Red 失敗原因、最小 Green、Refactor 與未驗項目。
- [x] 7.2 在測試 PostgreSQL 執行 upgrade → schema/data assertions → API/併發流程 → downgrade → re-upgrade；先展示測試資料與結果，未經使用者同意不得清除。
- [x] 7.3 以 Playwright 實機檢查桌面與行動 viewport：區塊預設收合、展開清單、九項選單、成功移除、失敗保留、背景進度、刷新失敗重試、畫面不跳轉且無重疊。
- [x] 7.4 產出一輪真實狀態分析 artifact，核對資料列、分類合計、SVG/HTML 畫面與目前 workspace；證明其他報表 artifact 未被刷新。
- [x] 7.5 比較狀態更新前後 cluster assignments、相關 workflow job types 與 clustering input identity，保存「未受影響」證據。
- [ ] 7.6 執行 `openspec validate add-tw-legal-status-curation --strict`、檢查 change task 完成證據、確認工作樹只含本 change 影響檔，再推遠端分支並經 required checks／人工驗收後才允許合併主線與 archive。
