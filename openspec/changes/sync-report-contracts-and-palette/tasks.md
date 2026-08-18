# Tasks: sync-report-contracts-and-palette

⚠ 閘門進場前先過 design §1 三問（不看語意能否判定／恆等式 vs 代理指標／
偏差是多出來還是缺席）。本 change 有三個閘門，§1 已逐一過過。

⚠ 全批採 TDD：先寫會紅的測試，再最小實作。閘門類任務**必須做反向驗證**
（把正確的東西改壞，閘門要紅）——空閘門比沒閘門更糟。

⚠ 本 change 未經使用者確認前不得 apply。

---

## 1. 母體閘門（最優先——同型錯誤已出現三次）

- [ ] 1.1 掃出所有繞過 `run_report` 自行 `cur.execute` 的彙總，列成嫌疑清單
- [ ] 1.2 Red：閘門測試——直查 DB 的彙總若未帶母體條件且不在白名單即紅
- [ ] 1.3 白名單機制：模組層顯式宣告＋**必須寫理由**（design §3）
- [ ] 1.4 修 `fetch_patent_kind_summary`：接 `patent_ids`
      （現況全庫 281 件／設計 21，滑雪機實際 55／11）
- [ ] 1.5 修受理局頁家族註記：不得用 `family_country_layout`
      （現況全庫 187，滑雪機實際 48）
- [ ] 1.6 反向驗證：把 1.4 的 `patent_ids` 拿掉，閘門要紅

## 2. 封面數字由引擎供給

- [ ] 2.1 Red：`report_data.json` 應含封面四個數字（件／族／受理局／三分法）
- [ ] 2.2 家族數口徑：`count(DISTINCT "WIPS同族ID")` 於母體
      ⚠ 缺同族 ID 時各自算一族，不得併成一族「未知」
- [ ] 2.3 三分法走 `transforms/patent_kind.py` 唯一定義處，不在封面自行判定
- [ ] 2.4 `stats_note` 移除「存活家族」（避免第二個家族口徑）
- [ ] 2.5 兩個 workspace 各驗一次：滑雪機 55／48／4／17·27·11；
      割草機 226／163／2／151·65·10 —— **兩者不得相等**

## 3. 家族數落點收斂

- [ ] 3.1 Red：`annual_trend`／`publication_trend` 的顯示欄不含 `family_count`
- [ ] 3.2 登記隱藏欄；⚠ `chart_rows` 必須保留資料（CLI 取證要用）
- [ ] 3.3 確認 KP 表與 KP 象限泡泡不受影響（per-applicant 維度，不動）

## 4. 用詞統一為「設計」

⚠ **分類後才改，不得全域取代**（design §5）：「外觀」有兩種語意，
專利類型改名、產品造形保留。誤改會破壞文獻備註語意且不可逆。

- [ ] 4.1 逐處分類 46（後端）＋60（測試）＋7（prompt）＋5（deck）處，
      標記「類型」或「造形」
- [ ] 4.2 只改「類型」那組：`DESIGN_STRATEGY_AXIS`、欄名對照、
      `strategy_type` 值（`只走外觀`→`只走設計`、`技術+外觀`→`技術+設計`）、
      報表 `label_zh`（外觀保護策略→設計保護策略）
- [ ] 4.3 ⚠ OpenSpec `archive/` 底下**不改**（歷史紀錄）
- [ ] 4.4 一致性測試：三分法標籤與報表用詞一致；
      「造形」語意的字串未被改動（逐處人工過目，不只跑測試）

## 5. 文件契約同步

- [ ] 5.1 在 design §2 的權責邊界表基礎上，逐檔標出「這段屬於誰的職責」
- [ ] 5.2 Red：`report_key` 集合對帳閘門
      —— 文件提到但定義裡沒有＝紅；定義裡有但文件沒提＝黃（列出不擋）
- [ ] 5.3 反向驗證：文件塞一個假 report_key 要紅
- [ ] 5.4 同步 `prompts/report-narrative-flow.md`（family_country_layout、
      年度矩陣、country_distribution 狀態堆疊、topic_timeline 段落）
- [ ] 5.5 同步 `prompts/content_standard.md`（技術主題×2）
- [ ] 5.6 同步 `skills/html-report-to-deck/SKILL.md`＋`references/narrative.md`
- [ ] 5.7 同步 `add-deck-delivery-line/design.md` 的頁面盤點
      （現存有效×2、主題演進×3、年度矩陣×2、更多、技術主題）

## 6. 色票唯一定義處

- [ ] 6.1 盤點：chart 側 hex 與 deck 側 `RGBColor` **跨語法**比對
      （實查 chart 49 種、deck 12 個）
- [ ] 6.2 兩套深藍收斂（`#00094A` vs `#0B2545`）
- [ ] 6.3 定唯一定義處（比照字型定在 `chart_sizing`）
- [ ] 6.4 硬判準：`chart_runner` 裸 hex 字面數為 0（常數定義區除外）
- [ ] 6.5 軟揭露：色票表列出每個色的語意用途，新增時必須填
- [ ] 6.6 補回 `COLOR_TRANSFERRED`（2026-08-17 違反 deepen 5b.4 的新增）

## 7. 驗收

- [ ] 7.1 OpenSpec strict validation
- [ ] 7.2 三層範圍回歸（直接／整合／契約）
      ＋ **符號反查消費者**比對已跑清單（2026-08-18 實證：憑印象挑會漏 6 支）
- [ ] 7.3 兩個 workspace 各產一份報表，逐項對 design §7 判準
- [ ] 7.4 揭露未覆蓋範圍；⚠ 本 change **不含**版型與 deck 實物驗收（輪二）
- [ ] 7.5 使用者接受後 archive；同步 main specs 與 migration ledger
