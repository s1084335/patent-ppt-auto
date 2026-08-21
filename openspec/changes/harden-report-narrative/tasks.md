# Tasks: 報表解讀的可信度與人工介入

⚠ 三支柱彼此獨立，可各自驗收。支柱二內部有順序：先擴檢查對象，再加自檢迴圈
（沒有正確的檢查對象，自檢只會反覆修錯的東西）。

🔴 **貫穿全案的兩條紅線**（每個切片都要自問）：
1. 這條規則是**檢查性質**還是**檢查格式**？後者一律不做（EXP-013）。
2. 這個檢查是 **runner 強制**還是 **CLI 可選**？後者一律不做（design 三）。

## 0. 基線

- [ ] 0.1 測試基線：`pytest tests -q` 的 passed／failed 數與既有紅清單
- [ ] 0.2 記錄 `#452` 的 `narratives.json` 與 job result 現況，作為「修好前」對照
- [ ] 0.3 記錄目前 CLI 的 `--allowedTools` 實際值——支柱二完工後要比對**未變**

---

## 支柱一 · 來源可信

### 切片 A：輸出契約與提示詞對齊

- [ ] A.1 Red：`build_prompt` 產生的形狀宣告含 `evidence`（EXP-008）
- [ ] A.2 ⚠ 逐字比對 `build_prompt` 與 `report-narrative-flow.md` 兩處宣告
      ——本次事故的第三層破口就是這兩處不一致
- [ ] A.3 Green：兩處同步補上 `evidence`
- [ ] A.4 Red：缺 `evidence`／`evidence` 為空／`query_count=0` 各產生契約警告，
      且 job 仍 `succeeded`（EXP-008 三個 scenario）
- [ ] A.5 Green：`validate_narrative_contract` 新增 evidence 檢查
- [ ] A.6 Red：不含 `evidence` 的既有 `narratives.json` 仍能正常組版（EXP-008）
- [ ] A.7 驗收：組版端未因新鍵改變行為

### 切片 B：稽核落庫

- [ ] B.1 Red：有查詢時 `job_result` 含 `query_audit` 與 `query_count`（EXP-009）
- [ ] B.2 Red：**零查詢時三欄仍存在**，值為 `[]`／`0`／`[]`（EXP-009）
      ⚠ 這條是本切片的核心：欄位缺席與「查了沒查到」在消費端無法區分
- [ ] B.3 Red：稽核讀取失敗時工作仍依解讀結果判定成敗，`query_count` 為 `0`（EXP-009）
- [ ] B.4 Green：`handle_ai_narrative` 結果白名單補三欄
- [ ] B.5 ⚠ 稽核只讀 `mcp_query_audit` 唯一來源，**不得在 runner 內另行定義落點**
- [ ] B.6 🔴 **真 Red 驗證**：人為移除白名單任一欄，確認測試**實際變紅**
      ——不接受「有測試」，要證明它守得住

### 切片 C：警告可見

- [ ] C.1 Red：`contract_warnings` 隨結果落庫（EXP-010）
- [ ] C.2 Green：前端 AI 任務卡顯示警告，與 `succeeded` 並存
- [ ] C.3 驗收：**實機檢視**警告確實顯示，不接受「應該會顯示」

---

## 支柱二 · 內容可信

### 切片 D：品質檢查覆蓋長文

- [ ] D.1 Red：只有長文的變體，長文逐項受既有九把鎖檢驗（EXP-011）
- [ ] D.2 Red：長文不含任何具體數值時產生警告（EXP-011）
- [ ] D.3 Red：長文與條列並存時**各自受檢**，警告可辨識到形式與變體（EXP-011）
- [ ] D.4 Green：`validate_narrative_contract` 的檢查對象擴及長文
- [ ] D.5 ⚠ **判準沿用既有九把鎖，不新增判準**——新判準要有依據，
      依據要從實跑分布來，現在沒有
- [ ] D.6 回歸：既有 `points` 檢查行為未變

### 切片 E：判準不依格式

- [ ] E.1 Red：兩段實質內容相同、小標與句型不同的文字，檢查結果**相同**（EXP-013）
- [ ] E.2 Red：依循常見句型但無具體數值、未指名對象的文字，仍判**不合格**（EXP-013）
- [ ] E.3 Green：確認判準實作只看性質
- [ ] E.4 🔴 **逐條檢視全部判準**：任一條以「必須出現某字串或某句型」為通過條件即不合格
      ——第一世代的模板化是規則自己規定出來的（`ai_narrative_runner.py:515`）
- [ ] E.5 Red：限制與涵蓋範圍說明重複產生時文字完全相同（EXP-014）
- [ ] E.6 Red：解讀寫回時不改動程式產生的限制說明（EXP-014）
- [ ] E.7 Red：解讀文字不覆述字數／格式／句型要求（EXP-025）
- [ ] E.8 Red：解讀文字不出現契約欄位名、工具名稱或取證機制（EXP-025）
- [ ] E.9 Red：資料不足時據實說明資料狀況，不以「依規定」「依指示」帶過（EXP-025）
- [ ] E.10 ⚠ **檢查對象是內容性質，不是字串比對**——洩漏的常見形式是用自己的話覆述，
      關鍵字黑名單擋不住，且違反 EXP-013

### 切片 E-2：整份報告為脈絡（EXP-027）

- [ ] E2.1 ⚠ 動工前先確認**不做什麼**：不得規定「每章開頭承接前章」之類的段落結構
      ——那是第一世代模板化的完全複製（見 design 四之三末段）
- [ ] E2.2 Red：共同背景（涵蓋範圍、母體組成、排除項）隨派工提供給撰寫端（EXP-027）
      ⚠ 斷言的是**派工內容本身**，不是「report_data.json 裡有這個鍵」
      ——`#452` 已示範過「檔案裡有 ≠ 用得到」
- [ ] E2.3 Green：把既有頂層資料（`descriptive_stats`／`cover_stats`／`patent_kind`／
      `population`）納入派工的共同背景。**消費既有計算，不新增查詢**
- [ ] E2.4 Red：`narratives.json` 的 report_key 不含敘述統計（EXP-027）
- [ ] E2.5 驗收：逐章比對引用的年份區間與件數，確認落在共同背景之內
- [ ] E2.6 🔴 驗收：**通讀匯出檔全篇**，確認章節可銜接、後段不重複交代同一組背景。
      這條只能人工看——程式化只驗得到數字落在範圍內，驗不到「讀起來像一份報告」

### 切片 F：交件前自檢與修稿輪

- [ ] F.1 ⚠ 動工前先確認形式：**runner 強制執行**，不是開工具給 CLI 自呼叫
      ——`#452` 的 CLI 讀過取證地圖後選擇不用；可選的自檢等於沒有
- [ ] F.2 Red：首輪違規 → 收到**具體**違規變體與規則 → 修正後才寫入（EXP-012）
- [ ] F.3 Red：達輪數上限即停、保留最後一版、剩餘違規進 `contract_warnings`、
      job 仍 `succeeded`（EXP-012）
- [ ] F.4 Red：首輪即通過時不發修稿要求（EXP-012）
- [ ] F.5 Red：**自檢不擴權**——CLI 工具集與未啟用自檢時相同（EXP-012）
      ⚠ 與 0.3 記錄的值逐字比對
- [ ] F.6 Green：實作迴圈。無 `--resume` 時每輪重開 CLI 並帶完整上下文
- [ ] F.7 ⚠ 輪數上限取值要記錄理由：無續談時每輪重讀全文，`#452` 的 CLI 段約 17 分鐘
- [ ] F.8 （選配）實測 `--resume` 是否可用；可用才評估是否加入 `cli_gateway`。
      **不可用不影響本切片完成**

---

## 支柱三 · 人可介入

### 切片 G：人工稿的資料落點

- [ ] G.1 Red：人工稿與 AI 原稿分欄保存於 `narratives.json`，AI 原稿不被覆蓋（EXP-015）
- [ ] G.2 Green：新增人工稿保存端點
- [ ] G.3 Red：重跑報表 A 不影響報表 B 的 AI 原稿與人工稿（EXP-016）
- [ ] G.4 Red：編輯一張不影響其他張（EXP-016）
- [ ] G.5 ⚠ 沿用既有 `report_keys` 機制（API → handler → runner 三段已全通），
      **不另建第二套逐報表機制**

### 切片 H：編輯入口

- [ ] H.1 ⚠ 先確認新入口**不沿用** `export-edit-toggle`／`toggleExportEditMode`
      ——會誤觸 `test_export_edit_mode_removed`
- [ ] H.2 Green：`manual_text` 改讀 `narratives.json`（現在讀的 `view.edits` 是死路徑）
- [ ] H.3 ⚠ 顯示邏輯（人工稿優先、已修改標記）**已存在於 `index.html:4927`，不要重寫**
- [ ] H.4 Green：解讀區新增編輯與保存入口
- [ ] H.5 Red：未編輯的變體顯示 AI 原稿且不標示已修改（EXP-015）
- [ ] H.6 驗收：實機編輯、保存、重新載入仍在
- [ ] H.7 Red：匯出 HTML **含人工稿內容、不含編輯或保存操作**（EXP-015）
- [ ] H.8 回歸：`test_export_edit_mode_removed` 仍綠

---

## 1. 範圍回歸

- [ ] 1.1 直接測試：`test_narrative_requires_research.py`、`test_narrative_contract.py`、
      `test_narrative_selfcheck.py`、`test_narrative_report_keys.py`
- [ ] 1.2 整合測試：`test_chart_sections.py`、`test_api_ai_tasks.py`、`test_mcp_query_audit.py`、
      `test_population_notes.py`
- [ ] 1.3 契約測試：`test_api_frontend.py`、`test_frontend_js_syntax.py`
- [ ] 1.4 ⚠ 既有斷言**只能改不能放寬**；每處改動註明理由
- [ ] 1.5 完整套件，與 0.1 基線逐項比對

## 2. 交付前驗收

- [ ] 2.1 逐項對照 proposal 的 Acceptance Gate 全部判準
- [ ] 2.2 🔴 判準 8（未引入固定欄位名或句型）與 8b（無內部指示洩漏）**逐條逐則檢視**，
      不抽樣
- [ ] 2.3 🔴 判準 14（真 Red）**實際證明測試會紅**
- [ ] 2.4 實跑一次完整解讀，檢視 `query_audit` 分布與修稿輪實際發生次數
- [ ] 2.5 OpenSpec strict 通過
- [ ] 2.6 未執行與不適用的項目分開揭露

## 3. 收尾

- [ ] 3.1 回寫本 tasks 與 design：實測與規劃不符處註明原因與日期
      ⚠ 特別是輪數上限的實際取值與 `--resume` 的實測結果
- [ ] 3.2 更新 `.agents/context/` 相關唯一來源（`report-professionalism-spec.md` 的
      問題 13／14／16 標注已由本 change 處置）
- [ ] 3.3 使用者明確接受後才 archive
