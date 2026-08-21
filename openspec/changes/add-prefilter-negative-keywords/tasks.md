# Tasks: 初階篩選（負面關鍵字）

⚠ 切片依**可獨立驗收的功能**拆，不按 DB／API／前端水平拆。每個切片走完
Red → Green → Refactor 才進下一個。

## 0. 基線

- [ ] 0.1 記錄測試基線：`pytest tests -q` 的 passed／failed 數，並記下既有紅的清單
      ⚠ 沒有基線就分不出「這次弄壞的」與「本來就壞的」
- [ ] 0.2 記錄實測數字作為驗收對照：`ion` 子字串 265／前綴詞界 0、`mow` 187／177、
      `blade` 64／64、`割草` 於 abstract 命中 0
- [ ] 0.3 確認 `workspace_excluded_patents` 現有列數與各狀態分佈（改動前的存量）

## 切片 A：負面關鍵字治理（純 DB，無 AI）

- [ ] A.1 Red：關鍵字寫入後只作用於所屬 workspace（PRE-001）
- [ ] A.2 Red：停用的關鍵字不參與比對（PRE-001）
- [ ] A.3 Green：migration 建立關鍵字表（workspace 外鍵、原詞、比對詞、確認狀態、
      啟用旗標、時間戳）
- [ ] A.4 Green：關鍵字 CRUD 端點與 workspace 範圍守門
- [ ] A.5 ⚠ 全庫 workspace 不得建立關鍵字（沿用 `CLU-007` 既有限制），補測試
- [ ] A.6 驗收：兩個 workspace 各建關鍵字，互不可見

## 切片 B：AI 轉英文比對詞（產草稿，不生效）

- [ ] B.1 Red：未確認的比對詞不得用於比對、不得產生任何 pending（PRE-002）
- [ ] B.2 Red：AI 輸出直接落庫時，確認狀態仍為未確認（護欄測試，非 code review）
- [ ] B.3 Green：新增 `ai:keyword_expand` job type
- [ ] B.4 Green：新增 runner。⚠ **自行** `functools.partial(_gw_build_cli_command,
      tools=NO_TOOLS)`，不從其他 runner import——兩支既有 runner 都因此靜默拿到
      RESEARCH_TOOLS（見 design 證據 5）
- [ ] B.5 Green：三處註冊同步——`job_repository.JOB_TYPES`、
      `ai_bridge._AI_JOB_RUNNERS`、`test_cli_gateway` 權限政策表
- [ ] B.6 Red→Green：補測試斷言三處集合相等（AIC-009）
- [ ] B.7 Green：轉換失敗時明確回報，且不阻斷使用者自行輸入（PRE-002）
- [ ] B.8 驗收：輸入「割草」，取得含 `mow` 詞族的建議；確認前執行篩選無任何 pending

## 切片 C：確定性比對與命中預覽

- [ ] C.1 Red：`ion` 命中 0（不是 265）、`mow` 命中 177（不是 11）、`blade` 命中 64
      ——**這三個數字是防止日後被改回 `ILIKE '%…%'` 的鎖**
- [ ] C.2 Red：三個比對欄位皆空的專利不命中，且數量可列出（PRE-003）
- [ ] C.3 Green：前綴詞界比對（`~* '\m詞'`），涵蓋 `title`／`abstract`／獨立項
- [ ] C.4 Green：逐關鍵字命中件數預覽；零命中須顯示 0 而非省略（PRE-004）
- [ ] C.5 Refactor：比對詞的正規化與跳脫收斂到單一函式（避免兩處各自處理特殊字元）
- [ ] C.6 驗收：以割草機 workspace 實跑，中文關鍵字經轉換後命中 > 0

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
