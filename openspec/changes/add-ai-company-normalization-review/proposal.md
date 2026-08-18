## Why

現行公司治理已有 `company_aliases`、`ai_suggested`、人工確認與 `confirmed` 顯示護欄，但 AI 能力只涵蓋既有代碼組的中文名草稿，且前端審核入口已移除。新匯入的公司變體仍需使用者逐筆判斷應加入哪個既有公司，或手動建立新的 TEMP 公司；中英文正規化名稱也容易因變體逐筆輸入而不一致。

本變更要讓 AI 對每個未歸戶或待補名稱的原始變體提出受控建議，使用者確認後才寫入正式公司 mapping。AI 永遠不得產生、猜測、修改或替換 WIPS 公司代碼。

## Intent

在現有「專利權人代碼與中文名」治理區加入預設收合的 AI 建議待審流程。AI 可建議變體加入 Backend 提供的既有公司、補齊該公司的中文名與英文正規化名稱，或建立無 WIPS 代碼的 TEMP 公司；所有正式寫入都必須經使用者逐筆或多選確認，且未確認內容不得影響專利列表、分析、分群或報表。

## What Changes

- 新增手動觸發的 `ai:company_normalization_suggestion` job，沿用既有 Companion、`ai_bridge`、`cli_gateway` 與 PostgreSQL job queue。
- Backend 將待處理變體、既有 confirmed 公司白名單、已知 WIPS 代碼及公司目前中英文正式名組成受控輸入；CLI 不直接讀資料庫。
- AI 可輸出 `map_existing`、`update_names` 或 `create_temp` 三種建議，並附信心、理由及 HTTPS 證據；輸出契約不提供可由 AI填寫的 WIPS 代碼欄位。
- 建議以 `company_aliases.review_status='ai_suggested'` 保存，正式顯示與統計仍只消費 `confirmed`；不新增 suggestion table。
- 前端以可多選變體的待審區呈現原始字面、目標公司、中英文正規化名稱、信心與來源；使用者可改選既有公司或修改名稱後確認。
- 確認只處理使用者選取的變體，並委派既有 confirmed 唯一寫入路徑；略過保留待審建議但不寫正式 mapping。
- 建議完成、確認與 derived refresh 透過 SSE 使目前畫面背景更新，不跳頁、不重置展開狀態。

## Scope

- 候選來源：目前 workspace 中尚未 confirmed 歸戶的公司原始字面、`review_required` 公司組，以及 confirmed 公司組中缺中文名或英文正規化名稱者。
- 已依權威 WIPS 代碼自動建組但缺中文名者，若查得可核對的市場慣用中文名或法人登記中文名稱，均可產生待審建議；法人名稱須標示名稱依據，不得偽裝成市場慣用名。
- 自然人原始字面可被建議為公司變體，但僅限證據可明確辨識同一人，並證明其為該公司的 owner／proprietor／董事；僅有 founder、CEO、經理、員工、發明人、聯絡人或同名字面不足以歸戶。
- 建議類型：加入受控既有公司、補／修既有公司中英文正式名、建立 TEMP 公司。
- 每個原始變體保留原字面；同一正式公司下的多個變體共用一組中文正式名與英文正規化名稱。
- CLI 最小權限為 Backend 提供的 payload，加上公司名稱查證所需的 `WebSearch`／`WebFetch`；不得取得 DB、shell、任意檔案讀寫或其他 MCP 權限。
- 所有經 Nginx 進入工具的內網使用者均可手動觸發與審核，沿用現行公司治理權限。

## Non-goals

- 不讓 AI 產生、推測或查回 WIPS 公司代碼，也不讓 AI 修改 Backend 提供的既有代碼。
- 不自動確認、不因匯入自動啟動 AI、不以相似度直接寫正式 mapping。
- 不改 raw/core 專利原始公司字面，不刪除原始變體。
- 不改公司集團正規化、分群、embedding 或分類邏輯。
- 不建立另一張公司 mapping 或 suggestion table，不建立第二套 confirmed 寫入 SQL。
- 不在本變更提供永久封鎖建議；「略過」沿用現有語意，保留待審內容供稍後處理。

## Confirmed Decisions

- AI 建議必須由使用者確認後才可成為正式 mapping。
- 每個變體可各自被建議，但同一公司身分只維護一組共用的中文正式名與英文正規化名稱。
- AI 可建議加入既有公司、補／修中英文名稱，以及建立新 TEMP 公司。
- 缺中文名公司可接受「市場慣用中文名」或「法人登記中文名稱」建議，待審畫面必須標示依據與來源。
- 個人可以作為公司變體，但必須通過自然人同一性與企業所有人／董事關係的高證據門檻，並由使用者明確確認其專利將歸入該公司統計。
- AI 永遠不得產生 WIPS 公司代碼；既有代碼只能來自 Backend 白名單或既有資料。
- 沒有 WIPS 代碼的新公司由 Backend 以既有確定性規則產生 `TEMP:*`，不得由 AI 提供字串。
- 沿用 `derived_layer.company_aliases` 的 `ai_suggested`／`confirmed` 狀態與唯一正式寫入路徑，不新增 table。
- 手動啟動、待審區預設收合、無建議時隱藏；完成後以 SSE 背景刷新。

## Open Questions

無阻塞問題。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `company-governance`：把既有「AI 中文名先草稿後確認」擴充為逐變體的中英文公司正規化建議、受控既有公司歸戶、TEMP 公司建立與組合驗收契約。
- `platform-runtime`：新增公司正規化 AI job 對公司治理資源的 SSE invalidation，不建立第二條 queue 或 Companion 通道。
- `workspace-and-browse`：在既有公司治理區提供手動觸發、多選待審、修改後確認與背景刷新。

## Impact

- DB：原則上無 migration；沿用 `derived_layer.company_aliases` 既有欄位與 `wips_metadata_json` 保存 suggestion kind、受控 target reference、信心、證據、model/prompt version。若實作盤點證明既有 constraint 無法承載契約，必須先回寫本 change 再提出 migration，不得臨時新增表。
- Backend/API：新增候選快照、待審列表、手動 enqueue 與確認契約；正式寫入仍委派既有 `apply_confirmed_display_names`。
- Worker/Companion：新增單一 job type 與 runner，統一掛進既有 `ai_bridge`；不得建立獨立 queue、獨立 Companion 或直接 DB 連線。
- Frontend：擴充現有公司治理區；沒有建議時不佔版面，待審時可多選變體與編輯名稱。
- Derived/reporting：確認後 enqueue 既有 `refresh_derived`；確認前所有 projection、分析、分群與報表保持不變。

## Activation

- 部署 backend、worker/Companion job contract 與 frontend 後生效；若 DB constraint 盤點符合預期則不需 migration。
- 不需重匯 WIPS、不需重跑 embeddings 或分群；只在人工確認正式 mapping 後執行既有 derived refresh。
- 舊 `ai:company_zh_name` job 保持相容，不再新增第二個可見前端入口；新待審區以廣義公司正規化 job 為唯一入口。

## Acceptance Gate

- OpenSpec strict validation 通過，proposal、delta spec、design、tasks 無矛盾且 Open Questions 為零。
- TDD 證明 CLI 輸入只含 Backend 受控候選／公司白名單，輸出無 WIPS code 欄位；任何未知 target、候選、alias 或額外 code 都在 persistence 前拒絕。
- DB/API 驗證證明 `ai_suggested` 不進 confirmed-only projection；確認前後 raw/core 原文完全不變。
- 每個變體均可獨立或多選確認；加入既有公司時只採該公司既有 WIPS code，建立新公司時 code 由 Backend 產生 `TEMP:*`。
- 有代碼但缺中文名的公司，AI 可分別提出 `market_common_name` 或 `registered_legal_name` 依據；來源不足時不得產生可確認建議。
- 自然人變體須顯示人物同一性、公司所有人／董事關係、證據日期與「確認後其專利將納入公司統計」警示；只有其他職稱或發明人紀錄時必須拒絕。
- 使用者修改目標公司或中英文名稱後，正式 mapping 以使用者送出的受控值為準；衝突時原子失敗且不覆蓋既有 confirmed mapping。
- 前端實物驗收手動啟動、預設收合、無建議隱藏、可讀證據、多選、修改、略過、確認、錯誤保留與畫面不跳轉。
- SSE 實測涵蓋 job 完成、建議持久化、人工確認、derived 完成與斷線輪詢保底；不得依賴 F5 才看得到結果。
- 組合驗收包含目標測試、受影響回歸、`scripts/verify_module.py`、隔離 PostgreSQL、真 Companion/CLI job、Playwright 桌面與行動版；未執行的 Supabase/Lightning 項目須逐項揭露。
