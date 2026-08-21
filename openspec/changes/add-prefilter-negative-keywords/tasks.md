# Tasks: 初階篩選（負面關鍵字）

⚠ 切片依**可獨立驗收的功能**拆，不按 DB／API／前端水平拆。每個切片走完
Red → Green → Refactor 才進下一個。

## 0. 基線（2026-08-21 完成）

- [x] 0.1 記錄測試基線：`pytest tests -q` 的 passed／failed 數，並記下既有紅的清單
      ⚠ 沒有基線就分不出「這次弄壞的」與「本來就壞的」
      → **99 failed / 2865 passed / 49 skipped / 40 errors**（8:01）。
      ⚠ 這不是本 change 造成的：D-4 修好後，原本靜默 skip 的 ~140 個 DB 測試
      開始真的執行並暴露既有紅，已立案為 **D-6** 交 Codex
      （見 `known-issues-optimization.md`）。
- [x] 0.2 記錄實測數字作為驗收對照：`ion` 子字串 265／前綴詞界 0、`mow` 187／177、
      `blade` 64／64、`割草` 於 abstract 命中 0
      → **四項全部吻合**（割草機 workspace 母體）。
      🔴 第一次量出 257／170／46 是我少量了一欄：三個比對欄位是
      `title`／`abstract`／**`獨立項[KR,JP,US,CN,EP,IN]`**，最後那個欄名是中文帶
      國別後綴、**不含 "claim" 字樣**，用關鍵字猜欄名會漏掉它。
- [x] 0.3 確認 `workspace_excluded_patents` 現有列數與各狀態分佈（改動前的存量）
      → **總列數 0**（尚無任何剔除紀錄）。

## 切片 A：負面關鍵字治理（純 DB，無 AI）——2026-08-21 完成，22 passed

- [x] A.1 Red：關鍵字寫入後只作用於所屬 workspace（PRE-001）
- [x] A.2 Red：停用的關鍵字不參與比對（PRE-001）
- [x] A.3 Green：migration 建立關鍵字表（workspace 外鍵、原詞、比對詞、確認狀態、
      啟用旗標、時間戳）
      → `0055_prefilter_negative_keywords`，表
      `derived_layer.workspace_negative_keywords`。
      ⚠ **規則與結果分表**：命中結果仍落 `workspace_excluded_patents`（schema 不改）。
      混在一起會讓「規則改了、舊結果還在」變成無法表達的狀態。
      ⚠ `terms_confirmed` 預設寫在 **schema** 而非只靠應用層——應用層漏一條路徑就破功。
- [x] A.4 Green：關鍵字 CRUD 端點與 workspace 範圍守門
      → `GET／POST /workspaces/{id}/negative-keywords`、
      `PATCH／DELETE /workspaces/{id}/negative-keywords/{keyword_id}`。
      🔴 PATCH／DELETE **先驗歸屬再操作**：路徑帶了 `workspace_id` 卻不用等於裝飾，
      知道 `keyword_id` 就能改別的 workspace。已補兩支跨庫測試。
      ⚠ 建立端點**不收** `match_terms`／`terms_confirmed`——開放等於留一條繞過確認的路。
- [x] A.5 ⚠ 全庫 workspace 不得建立關鍵字（沿用 `CLU-007` 既有限制），補測試
      → 委派既有 `clustering.exclusions.is_global_workspace`，並加測試斷言
      **沒有自己查 `is_global` 欄**（否則全庫判定就有第二份定義）。
- [x] A.6 驗收：兩個 workspace 各建關鍵字，互不可見（模組層與 API 層各一組）

### 🔴 A 過程中挖到的真實危害：測試會靜默連上正式庫

`connection._pool` 是模組層單例，**第一次使用就把連線字串快取住**。
測試改了 `PGDATABASE` 沒用——池還握著舊的。

實測：漏了 `_reset_pool()` 時，`TestClient` 打的 API **實際連到 Supabase 正式庫**
（`current_database()` 回 `postgres`、看得到正式的三個 workspace）。
✅ 已確認零殘留：正式庫沒有這張表（0055 未在 Supabase 跑過），寫入必然失敗。

⇒ 本檔加了兩道護欄：`_reset_pool()` ＋ `_assert_pool_targets_test_db()`
（**比對 `current_database()` 必須正好是本檔的拋棄式庫**）。
⚠ 只驗「不是正式庫」不夠——池也可能指到 `conftest` 釘的 `patent_ppt_test`，
症狀是「表在、資料不在」，看起來像程式邏輯錯誤，會一路修錯方向。

⚠ 本檔的建庫寫法也刻意與其他 DB 測試不同：**連得上才改 env**（其他檔是先改後連，
`SkipTest` 時 `tearDownClass` 不執行就永久污染——D-4 的根因，53 檔同型）。

## 切片 B：AI 轉英文比對詞（產草稿，不生效）——2026-08-21 完成，30 passed

- [x] B.1 Red：未確認的比對詞不得用於比對、不得產生任何 pending（PRE-002）
      → `active_match_terms` 同時過濾 `enabled` 與 `terms_confirmed`，兩者缺一不可。
- [x] B.2 Red：AI 輸出直接落庫時，確認狀態仍為未確認（護欄測試，非 code review）
      → 兩層斷言：`store_expansion` 原始碼含 `terms_confirmed=False`（寫死），
      且**簽章不接受**任何確認相關參數——有參數就有人會傳 True。
- [x] B.3 Green：新增 `ai:keyword_expand` job type
- [x] B.4 Green：新增 runner。⚠ **自行** `functools.partial(_gw_build_cli_command,
      tools=NO_TOOLS)`，不從其他 runner import——兩支既有 runner 都因此靜默拿到
      RESEARCH_TOOLS（見 design 證據 5）
      → 已照做，並加測試斷言「原始碼不得出現 `from backend.app.worker.ai_`」。
- [x] B.5 Green：三處註冊同步——`job_repository.JOB_TYPES`、
      `ai_bridge._AI_JOB_RUNNERS`、`test_cli_gateway` 權限政策表
      ⚠ 實際是**四處**：`test_cli_gateway` 另有「實際 argv 取樣器」
      （`_actual_argv`），它不信政策表的宣告，要真的組一次指令來驗。
      漏了它，既有守門 `test_every_registered_job_uses_reviewed_actual_argv_tier`
      會紅——它就是為此而寫的。
- [x] B.6 Red→Green：補測試斷言三處集合相等（AIC-009）
      ⚠ 這條在三處都沒註冊時**也會過**（三個空集相等），只在漏其中一處時才紅
      ——那正是它的用途。
- [x] B.7 Green：轉換失敗時明確回報，且不阻斷使用者自行輸入（PRE-002）
      → `KeywordExpandError`：JSON 解析失敗、缺 `terms`、或**濾完為空**都算失敗。
      ⚠ 不得靜默寫入空陣列：使用者會看到「轉換完成」卻一個詞都沒有，
      以為是 AI 判斷沒有對應詞，實際是解析失敗。
      → 另補 `test_manual_terms_path_works_without_ai`：自行輸入英文詞並確認後
      `active_match_terms` 取得到——這條路徑**完全不經過 AI job**，AI 掛掉不影響。
- [x] B.8 驗收：輸入「割草」，取得含 `mow` 詞族的建議；確認前執行篩選無任何 pending
      → **實跑真 CLI**（`exit_code=0`、`is_error=False`）：取得 **37 個英文比對詞**，
      含 `mow`／`mower deck`／`lawn mow`／`robotic mower` 等詞族，全為英文無中文殘留，
      指令未夾帶任何工具（NO_TOOLS）。⚠ 驗收腳本**不寫任何 DB**，零殘留。
      「確認前無 pending」由 B.1 的 `active_match_terms` 為空涵蓋（切片 C 才產 pending）。

### ⚠ B 的一個設計取捨：非英文詞濾掉而非報錯

模型偶爾夾帶原文是常態，整批打掉會讓可用結果一起消失 ⇒ 逐詞濾。
但**全部被濾光**要當失敗處理，否則同樣是「靜默寫空」。

## 切片 C：確定性比對與命中預覽——2026-08-21 完成，14 passed

- [x] C.1 Red：`ion` 命中 0（不是 265）、`mow` 命中 177（不是 11）、`blade` 命中 64
      ——**這三個數字是防止日後被改回 `ILIKE '%…%'` 的鎖**
      → 用**自造語料**釘死行為（`combustion`／`composition` 不得被 `ion` 命中；
      `mower`／`mowing`／`MOW` 必須被 `mow` 命中）。
      ⚠ 刻意不依賴正式庫的實測值：那些數字會隨資料變動，而要驗的是**比對規則**。
      正式庫實測留給 C.6。
- [x] C.2 Red：三個比對欄位皆空的專利不命中，且數量可列出（PRE-003）
- [x] C.3 Green：前綴詞界比對（`~* '\m詞'`），涵蓋 `title`／`abstract`／獨立項
      ⚠ 不能用 `LIKE 'term%'`：比對要落在**單字的開頭**不是**欄位的開頭**，
      `"Lawn mower blade"` 會漏。
- [x] C.4 Green：逐關鍵字命中件數預覽；零命中須顯示 0 而非省略（PRE-004）
- [x] C.5 Refactor：比對詞的正規化與跳脫收斂到單一函式（避免兩處各自處理特殊字元）
      → `normalize_term`，並加測試斷言 `re.escape` 只出現在它裡面。
      ⚠ 使用者輸入 `c++`／`(a)`／`a|b` 是常態，不跳脫會在**執行篩選時**才炸。
- [x] C.6 驗收：以割草機 workspace 實跑，中文關鍵字經轉換後命中 > 0
      → 唯讀實跑，母體 226 件：`mow` **177**（與規格鎖定數字一致）、`blade` 62、
      `lawn mow` 98、`grass cut` 9、**`ion` 0**、不存在的詞 0；
      聯集 179/226（未涵蓋全部母體）；三欄皆空 0 件。

### 🔴 依使用者提醒改的一處：成員清單不寫死

使用者 2026-08-21：「初篩以後還會有其他種 workspace 的專利喔，最好實作機制不要
寫死在某些專利上」。

原實作自己讀 `patent_ids_json` ⇒ 改走既有唯一來源
`clustering.exclusions.display_member_patent_ids`（契約＝**永遠回全部成員**），
並加測試斷言「不得自己查 `patent_ids_json`」與「換一個 workspace 照樣算得出來」。

⚠ **不能用 `analysis_member_patent_ids`**：那條會扣掉已剔除者，
而剔除正是本功能要產生的——扣掉等於「已剔除的永遠不會再被檢視」。

## 切片 D：裁決與封存——2026-08-21 完成，28 passed

- [x] D.1 Red：待裁決狀態不影響分群母體（PRE-005）
- [x] D.2 Red：封存後分群母體 = 成員數 − 已封存數（PRE-006）
- [x] D.3 Red：封存後不出現在瀏覽清單、但在剔除名單可見（PRE-006、WSP-003）
- [x] D.4 Red：同一專利被兩線命中不產生重複待裁決；已裁決保留者不重新列入（CLU-017）
- [x] D.5 Green：命中寫入既有 `workspace_excluded_patents` 的 pending 態，
      `reason` 記錄命中的關鍵字與比對詞（供 PRE-005 追溯）
      ⚠ 記到**比對詞**層級而不只是關鍵字：使用者看到「割草」不知道是被 `mow`
      還是 `lawn mow` 抓到的，而那決定了要不要刪掉某個過度寬鬆的詞。
- [x] D.6 Green：逐筆與批次裁決；沿用既有 `restore_patents` 還原
- [x] D.7 Green：瀏覽清單排除已封存者（改 `display_member_patent_ids` 的消費端，
      ⚠ 不改該函式語意——它的契約是「回全部成員」，排除由呼叫端疊）
      → `decisions.browsable_patent_ids`，並加測試斷言該函式語意未變。
- [ ] D.8 驗收（實資料）：割草機 workspace 剔除數筆後重跑分群，母體件數與預期相符
      ⚠ **未做**：這會寫入正式庫並影響分群，留給使用者決定何時做。

### 🔴 D 推翻了 0036 的「保留＝刪列」（使用者裁決）

`CLU-017` 要求「已保留者不再列入待裁決」，但既有 `keep_patents` 是**直接刪列**
——記不住誰被保留過。使用者 2026-08-21：「那就把既有契約修一下」。

⇒ migration `0056`：`status` 加 `kept`、`source` 加 `prefilter`。

⚠ 0036 反對第三種狀態的理由是「每個查排除清單的地方都要多一個過濾條件」。
**動工前窮舉全庫 11 個查詢，每一個都明確指定 status**，故該擔憂不成立。
該性質由 `test_kept_never_leaks_into_any_public_list` 逐一列舉對外函式守住。

🔴 第一版我用正規式掃原始碼驗這件事，結果抓到 `runner.py` 的 docstring
——**結構猜測不如行為驗證**，已改為列舉對外函式逐一確認。

### 🔴 兩條線的「保留」語意刻意不同（使用者裁決）

| | AI 線 | 初階篩選 |
|---|---|---|
| 判斷依據 | 主題結構（分群結果） | 關鍵字比對 |
| 重跑後依據會變嗎 | **會** | **不會**（PRE-001 明訂重跑可重現） |
| 已保留者重跑時 | **覆蓋回 pending**（重判有意義） | **跳過**（重問等於騷擾） |

⇒ 儲存統一（`keep_patents` 一律寫 `kept`），**寫入端各自決定要不要尊重它**。
「誰決定要不要重問」寫在寫入端，因為理由屬於「這條線的判讀依據會不會變」。

### 既有測試改動（改斷言不放寬）

`test_keep_removes_row_entirely` ×2：斷言由「該列應完全移除」改為「應標記為
kept」，並在 docstring 註明契約變更日期與理由。仍驗「保留後不在任何對外清單」。

⚠ 另有 5 紅**非本切片造成**：`test_exclusion_review_status_migration` ×2、
`test_migration_contract` ×3。移開 0056 後同樣 5 紅，錯誤是
`company_aliases_..._key already exists`，屬 migration downgrade 鏈的既有債
（D-6，Codex 正在修）。

## 切片 D：裁決與封存

- [ ] D.1 Red：待裁決狀態不影響分群母體（PRE-005）
- [ ] D.2 Red：封存後分群母體 = 成員數 − 已封存數（PRE-006）
- [ ] D.3 Red：封存後不出現在瀏覽清單、但在剔除名單可見（PRE-006、WSP-003）
- [ ] D.4 Red：同一專利被兩線命中不產生重複待裁決；已裁決保留者不重新列入（CLU-017）
- [ ] D.5 Green：命中寫入既有 `workspace_excluded_patents` 的 pending 態，
      `reason` 記錄命中的關鍵字與比對詞（供 PRE-005 追溯）
- [ ] D.6 Green：逐筆與批次裁決；沿用既有 `restore_patents` 還原
- [ ] D.7 Green：瀏覽清單排除已封存者（改 `display_member_patent_ids` 的消費端，
      ⚠ 不改該函式語意——它的契約是「回全部成員」，排除由呼叫端疊）
- [ ] D.8 驗收（實資料）：割草機 workspace 剔除數筆後重跑分群，母體件數與預期相符

## 切片 E：初階篩選頁與入口

- [ ] E.1 Red：瀏覽頁有入口且顯示待辦數（WSP-013）
- [ ] E.2 Red：瀏覽頁不承載關鍵字編輯／確認／裁決操作（WSP-013）
- [ ] E.3 Green：初階篩選頁四段（關鍵字／確認比對詞／待裁決／剔除名單）
- [ ] E.4 Green：`renderMain()` 加分派、**不加左導覽項**（沿案件比對前例）
- [ ] E.5 Green：瀏覽頁入口，待辦數取自權威 API 不由前端自數
- [ ] E.6 ⚠ 確認畫面不得顯示 SQL——補測試斷言頁面不含 SQL 片語
- [ ] E.7 驗收：實機開頁走完一輪（輸入 → 轉換 → 確認 → 套用 → 裁決 → 還原）

## 切片 F：保留期硬刪（🔴 不可逆，最後做）

⚠ **F 於 A–E 全數驗收通過、且實際使用過一段時間後才啟用。**
⚠ 預設停用；未啟用前，驗收報告須如實揭露「本次未驗硬刪」。

- [ ] F.1 Red：未滿一年者不列入刪除對象（PRE-007）
- [ ] F.2 Red：dry-run 不變更任何資料（PRE-007、PRT-007）
- [ ] F.3 Red：刪除後 `patent_ids_json` 不含該 id、剔除名單無孤兒列（PRE-007）
- [ ] F.4 Red：受影響報表版本被標記「來源已不完整」（PRE-007）
- [ ] F.5 Green：清理作業（批次上限、失敗隔離、逐筆回報、執行紀錄）
- [ ] F.6 Green：引用清理——⚠ 11 個 FK 全 CASCADE 會自動連帶刪，但
      `patent_ids_json`／`workspace_excluded_patents` 無 FK，必須主動處理
- [ ] F.7 Green：報表標記與前端顯示
- [ ] F.8 驗收（實資料，先 dry-run）：確認刪除清單正確後才真跑

## 1. 範圍回歸

- [ ] 1.1 直接測試：關鍵字、比對、裁決、封存相關新測試
- [ ] 1.2 整合測試：消費它們的——分群母體、報表母體、瀏覽清單
- [ ] 1.3 契約測試：`test_api_frontend.py`（前端靜態斷言）、`test_cli_gateway.py`
      （AI job 權限）、`test_frontend_js_syntax.py`
- [ ] 1.4 ⚠ **不得放寬既有斷言**。既有測試若因本 change 而紅，先分辨是「契約真的變了」
      還是「實作弄壞了」；前者改斷言並註明理由，後者修實作
- [ ] 1.5 完整套件，與 0.1 的基線逐項比對，新增的紅必須歸屬到本 change

## 2. 交付前驗收

- [ ] 2.1 逐項對照 proposal 的 Acceptance Gate 11 條
- [ ] 2.2 ⚠ 判準 5、6、8 以**實資料**驗收，不接受只跑單元測試
- [ ] 2.3 OpenSpec strict 通過
- [ ] 2.4 未執行與不適用的項目**分開揭露**，不以單一綠燈代替

## 3. 收尾

- [ ] 3.1 回寫本 tasks：實測與規劃不符處註明原因與日期
- [ ] 3.2 更新 `.agents/context/` 相關唯一來源與 `decisions.md`
- [ ] 3.3 使用者明確接受組合驗收後才 archive
