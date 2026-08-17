# Tasks: deepen-deck-evidence-layer

⚠ **全批禁寫條件規則**（沿用 `add-deck-delivery-line` tasks 3b 的約束）：
任何形式鎖進閘門前，先過 design §1.2 的**三問**：

1. 不看語意能不能判定？（不能 → 建議形）
2. 🔴 滿足它的**唯一**途徑是不是把事情做對？（恆等式 → 安全）
3. 若有自由度：偏差是**多出來的**（可見，目視兜得住）還是**缺席的**（兜不住）？

⚠ 只問第 1 題會放行「每頁最多一句」這種鎖——那是 v5 的同型錯誤（design §4）。

⚠ 本 change 未經使用者確認前不得 apply。

---

## 2b. 外觀保護策略與技術交叉（2026-08-16）

- [x] 2b.1 Red：新增 `S1` 外觀判定與外觀策略/技術交叉內容函式測試。
- [x] 2b.2 Green：`document_kind IN ('S','S1')` 走唯一外觀判定函式。
- [x] 2b.3 Green：新增 `design_protection_strategy` 與 `design_tech_intersections`，輸出可審核表格資料。
- [x] 2b.4 Green：新增 `design_protection_detail` report key 與 `design_protection` section，產出策略分布圖與兩張表格資料。
- [x] 2b.5 Green：前端 `REPORT_TYPES` 可選外觀保護策略，population/catalog 治理已登記。
- [x] 2b.6 Guard：外觀策略資料不得輸出 WIPS/PDF 連結；代表圖只以本地主附圖或 patent_id 解析。

## 0. 前置查證（未做完不得進 1.x）

- [x] 0.1 鎖定並記錄 `family_count` 三種語境，不得要求 implementer 自行定位家族演算法：
      `annual_trend.family_count`＝同一年 `COUNT(DISTINCT canonical_family_id)`；
      `family_country_layout`＝`derived_layer.report_family_country` 的現有保護國家佈局口徑；
      `applicant_strength_profile.family_count`＝同申請人去重 patent 後的 canonical family set size。
- [x] 0.2 確認設計專利 7/55 或 11/55 類推論只可作為本 change 的待驗收資料，
      不作為 family algorithm 決策；`priority_number`／`priority_date`／`priority_country`
      不作為 v1 家族合併依據。
- [ ] 0.3 追「台灣 9 件中 7 件已授權」的 7 是怎麼來的——兩個版本同錯，
      表示是**共同上游**（引擎或 prompt），不是 CLI 各寫各的

---

## 1. 外觀設計判別（唯一定義處）

- [x] 1.1 Red：測試斷言 `is_design_patent()` 對 id 452（`P/S1`／US／有 58 字主權項）
      回 True，對無主權項但 `kind` 非 S 的資料回 False
      ⚠ 這一條就是「無主權項」判準會踩的坑（design §3.1）
- [x] 1.2 Green：`document_kind IN ('S','S1')`，**單一函式**，引擎與報表共用
- [ ] 1.3 一致性閘門：全庫掃描，若某處仍用「無主權項」判設計專利即紅

## 2. 外觀設計軸（報表引擎）

⚠ 2026-08-13 使用者裁決「可以動報表引擎」——HTML 報表會一起出現這一軸。

- [ ] 2.1 母體對帳行：`總數 = 技術 + 外觀`，數字由引擎產（design §1.3 ①層）
      ⚠ 割草機是 `226 = 216 + 10`，不是 `217 + 9`
- [x] ~~2.2 主圖：年度 × 申請人身分別~~ 🔴 **2026-08-16 定案：採 Codex 版策略分布圖**
      Codex 已實作 `design_protection_detail`（`b6b5c2a`）：策略分布 bar
      （純外觀／技術+外觀件數）＋逐申請人明細表＋技術×外觀交叉 evidence 表。
      **不另畫年度×身分別圖**，理由：
      ① 審閱意見的核心是「11 件被靜默排除、漏 25% 申請人」——策略圖＋交叉表
         **正面回答**（每家策略、誰同時做兩邊），比時序圖直接；
      ② 滑雪機僅 11 件外觀分散 9 個年份，年度圖每格 1–2 件，**圖比表難讀**；
      ③ 同一批 11 件畫兩張圖＝把統計拉厚，與「把統計壓薄」相反。
      ⚠ 原規劃唯一多出來的「時間連續性」改由**判讀帶一句話**承接（見 3b.5 範式，
      建議形不進閘門）：外觀策略頁要講「2015–2025 每年都有」這類節奏。
- [x] 2.3 表：只走外觀設計的申請人清單（滑雪機實測 6 家）
      ✅ Codex 版明細表已涵蓋（`content_blocks.design_protection_strategy`
      的 `strategy_type` 分「只走外觀／技術+外觀」）。
- [x] 2.4 註：法律狀態
      ✅ Codex 版明細表已含 `statuses` 欄（`_dedupe(legal_status)`）。
- [x] 2.5 洛迦諾**異常值檢查**（不占版面）：分類與產品類別對不上時標記
      ⚠ 不當分析主軸——滑雪機 91% 集中在 21-02，畫圖沒訊息（design §3.2）
- [x] 2.6 ❌ 不做「圖式未取得」的未分析聲明——`主附圖` 11/11 有 bytea

## 3. 依據層級

- [x] 3.1 `plan.json` 帶獨立項證據：`claim_lookup.players[].patent_ids` 已有（現成）
- [x] 3.2 content schema 新增建議句的 `依據` 欄位
- [x] 3.3 閘門：建議句必須帶 `依據：`——**純字串比對，不驗內容**
- [x] 3.4 接不上依據的建議句**直接擋下**，不貼「待驗證」標籤放行（design §2.3）
- [x] 3.4a skill/prompt 明定 `依據：` 後方必須是可追錨點；閘門只擋有限空泛例句與內部 key 外洩，不做廣泛語意判斷
- [ ] 3.5 版型：獨立項構型比對升為固定頁型，位置提到結論頁之後
- [x] 3.6 ❌ 不做層級對應期程的檢查（design §2.2）

## 4. 口徑與指令句

- [x] 4.1 content schema **移除** `read_me`／`chart_rule` 兩個封面欄位
- [x] 4.2 閘門：黑名單字串（`本簡報怎麼讀`／`圖表原則`／`待驗證`／`降級`）
      ⚠ **有限清單**，不是模式比對
- [ ] 4.3 版型：口徑集中到附錄
- [ ] 4.4 改寫 `add-deck-delivery-line` design 7.5（資料口徑頁改附錄）
- [x] 4.5 ❌ 不做「每頁最多一句口徑說明」數量鎖（design §4）

## 5. 推定數字覆蓋率

⚠ 依賴 0.1／0.2。

- [ ] 5.1 引擎輸出 `來源欄位覆蓋率 N/M`，**跟著數字走**不放頁尾
- [ ] 5.2 圖的軸 metadata（讓目視有依據可比）
- [ ] 5.3 `plan_deck`：矩陣／象限樣本數 < 8 時改輸出排序表——**排頁時決定**

## 5b. 色票唯一定義處

⚠ 只有**要調色**才必須先做這批；`add-deck-delivery-line` 的原生繪製是照搬配色，
不依賴這批。

- [ ] 5b.1 盤點：圖表 SVG 側 **28 種**硬編碼色票、`deck_layout` 側 **12 個**
      `RGBColor` 常數
- [ ] 5b.2 兩套深藍收斂：圖表 `#00094A` vs `deck_layout.TEXT` `#0B2545`
      ⚠ 肉眼近乎一樣但值不同——改一邊另一邊不動，**不會有任何東西報錯**
- [ ] 5b.3 定唯一定義處（比照字型定在 `chart_sizing`）＋一致性測試
- [ ] 5b.4 外觀設計軸的新圖必須從唯一定義處取色，不得新增第 29 種

## 6. 護欄回寫

- [ ] 6.1 把 design §1.2 的**三問**補進 `add-deck-delivery-line` design 7.0 三分法
      ⚠ 原本四層（機械／判斷／選項／建議形）沒有把**恆等式**與**代理指標**分開，
      三次踩坑（v5／v7／v9）都是把後者當前者
- [ ] 6.2 pitfalls 新增兩條：
      (a) 形式鎖的三次同型失敗＋三問判準
      (b) **缺席比錯誤難發現**——閘門逼出的偏差若是「少掉的」，目視兜不住，
      因為你不知道本來該有什麼

## 7. 驗收

- [x] 7.1 目標測試 + 範圍回歸
- [ ] 7.2 HTML 報表實物驗收（外觀設計軸，**全部頁面目視**不抽樣）
- [ ] 7.3 deck 實物驗收（同上）
- [x] 7.4 OpenSpec strict validation
- [x] 7.5 `openspec validate deepen-deck-evidence-layer --strict` 必須通過
- [x] 7.6 靜態檢查：本 change artifacts 不得再保留未決問題章節或未決問題條目
