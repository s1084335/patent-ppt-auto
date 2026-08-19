# Tasks: sync-report-contracts-and-palette

⚠ 閘門進場前先過 design §1 三問（不看語意能否判定／恆等式 vs 代理指標／
偏差是多出來還是缺席）。本 change 有三個閘門，§1 已逐一過過。

⚠ 全批採 TDD：先寫會紅的測試，再最小實作。閘門類任務**必須做反向驗證**
（把正確的東西改壞，閘門要紅）——空閘門比沒閘門更糟。

⚠ 本 change 未經使用者確認前不得 apply。

## 🔴 執行分段（2026-08-18 使用者裁決：分 4 段，逐段回報）

切點是**依賴**與**能不能獨立驗收**，不是平均分。

| 段 | 節 | 項數 | 段末可驗的實物 |
|---|---|---|---|
| **1. 母體** | §1 ＋ §2 ＋ §7e | 14 | 兩個 workspace 數字**不相等且各自正確**（滑雪機 55/48、割草機 226/163）；拿掉 `patent_ids` 閘門要紅 |
| **2. 版型庫** | §7a → §7b → §7c → §7d | 15 | 三處同步閘門綠；`conclusions` **真的產得出那一頁**；`roadmap` 不再出現 |
| **3. 收斂** | §6 ＋ §4 | 10 | 裸 hex 為 0；「造形」語意字串逐處人工過目未被改動 |
| **4. 契約與驗收** | §3 ＋ §5 ＋ §8 | 16 | `report_key` 對帳閘門綠；兩份報表全頁目視 |

### 為什麼這樣切（不要自行調換）

- ⚠ **§1 必須最先**：§7e 的法律狀態分解是**新的彙總**。閘門立起來之前做它，
  等於當場再種一個同型錯誤（該錯已出現三次）。§2 封面數字同理。
- ⚠ **§5 文件契約必須最後**（不直覺）：第 2 段會改 `narrative.md`、`SKILL.md`、
  `content-template.json`，第 3 段會改用詞。文件同步排前面就要**同步兩次**，
  第二次一定有人漏。
- ⚠ **§4 用詞單獨放第 3 段**：全批**唯一不可逆**的一節（「外觀」有兩種語意，
  誤改文獻備註救不回來）。不能趕，也不該跟別的事混在同一段的注意力裡。
- **第 2 段內部有順序**：7a 的反向驗證會當場抓出還有哪些版型漏登記；
  7c 要在 7d 前（7d 的結論頁要用通用表格版型）。

### 每段結束要交的東西

改了哪些檔／跑了哪些測試／**反向驗證結果**（把對的改壞，閘門有沒有紅）／
這段沒驗到什麼／下一段開工前需不需要使用者裁決。

⚠ §6 的色票數字（28→49 種）是觀測值不是現況，第 3 段開工時**重新實查**。

**2026-08-19 使用者再確認**：§4 用詞**照原規劃留在第 3 段**，與 §6 色票同段完成，
不抽出、不延後。⚠ 同段混了一個可逆（色票）與一個不可逆（用詞）的改動，所以
第 3 段的**兩件事要分開 commit**——用詞那批單獨一個 commit，出事才回退得乾淨。

---

## 1. 母體閘門（最優先——同型錯誤已出現三次）

- [x] 1.1 掃出所有繞過 `run_report` 自行 `cur.execute` 的彙總，列成嫌疑清單
      （`scripts/audit_population_scope.py`，唯讀）

      **結果與原假設不同，記在這裡供後續不要重查：**

      第一類（繞過 `run_report` 自行查）：14 個函式，10 個未見母體條件。
      逐個人工判定後**只有 1 個是真 bug**：

      | 函式 | 判定 |
      |---|---|
      | `chart_runner.fetch_patent_kind_summary` | 🔴 真 bug（見 1.4） |
      | `mcp_server.get_data_status` | 全庫用途**正確**，但見 1.7 |
      | `list_zh_name_drafts`／`count_company_normalization_queue`／`list_company_groups`／`list_confirmed_group_candidates` | 公司治理跨 workspace，**本來就該全庫** |
      | `refresh_patent_search_terms`／`refresh_report_patent_base` | derived 重建，全庫 |
      | `api/jobs.ready` | 健康檢查，全庫 |
      | `workflow_outputs_repository._append` | 誤報（不是報表彙總） |

      ⚠ **掃描器有盲點**：它只抓「繞過 `run_report`」的，抓不到「走 `run_report`
      但定義 `supports_patent_ids=False`」那一類——第 2 例（受理局家族註記）就是
      那類。第二類要另外掃 `report_definitions.py`。

      第二類（`supports_patent_ids=False`）：4 個。

      | 報表 | 判定 |
      |---|---|
      | `family_country_layout` | 🔴 真 bug（見 1.5） |
      | `applicant_strength_profile`／`cluster_topic_table`／`opportunity_quadrant` | ✅ **排除嫌疑**——`report_type="cluster"`，範圍由 `workspace_id` 經 `load_cluster_workspace_data` 給，不是由 `patent_ids`。實測滑雪機 ws=3：成員 55 → 指派專利 **44**（44 ≤ 55，沒有洩到全庫） |

      ⚠ 那個 **44** 是「11 件外觀設計被靜默排除」（分不了群），屬 **deepen §3**
      而非本節——同樣是母體不對，但根因相反：§1 是**洩到全庫**，deepen §3 是
      **靜默縮小**。不要在本節「修」它。

- [x] 1.7 `get_data_status` 的全庫數字要**標明是全庫**
      ⚠ 它不是 bug，但是現成的誤讀陷阱：CLI 讀到 `patents: 281` 可能當成本報告母體，
      那正是封面 281 的形態。純加標籤，不改數字。
- [x] 1.2 閘門：`backend/app/db/population_scope.py`（掃描邏輯唯一定義處，
      測試與 script 共用一份）＋`tests/test_population_scope_gate.py`
- [x] 1.3 白名單：模組層 `POPULATION_SCOPE_EXEMPT` 宣告＋理由必填（空字串不算）；
      測試另存 `REVIEWED_EXEMPTIONS`，新增豁免要同時改兩處，讓它不可能悄悄長大
      ⚠ 三問：Q1 過、Q2 **不過**（塞進豁免表就通關＝代理指標）、Q3 過（diff 看得見）。
      保證「每個全庫彙總都被登記過」，**不保證理由是對的**
- [x] 1.4 `fetch_patent_kind_summary` 接 `patent_ids`
      （原況全庫 281 件／設計 21，滑雪機實際 55／11——2.5 已實測確認）
      ⚠ 參數做成 **keyword-only 且無預設**：忘記傳是 TypeError 當場炸，
      不是靜默退回全庫（那正是本 bug 的形狀）
- [x] 1.5 修受理局頁家族註記
      🔴 **2026-08-18 實測推翻原判定，原文保留在此以免下次重犯：**
      ~~不得用 `family_country_layout`（現況全庫 187，滑雪機實際 48）~~

      **實際不是母體問題。** 母體本來就有接——`ChartContext.report()` 一律傳
      `patent_ids`，而 `supports_patent_ids=False` 的語意不是「忽略母體」，
      是「**家族層報表：母體由 `build_family_scope_clause` 翻譯成家族集合**」
      （`report_engine.py` 353–360）。
      ⚠ `report_family_country` **沒有 `patent_id` 欄**，把該旗標改成 True 會產生
      `patent_id = ANY(...)` 直接讓 SQL 壞掉。已加測試守住不要被改。

      **真正的缺陷是加總錯誤**：報表依國家 group by，每列是「該國有幾個家族」，
      相加等於同一家族跨幾國算幾次。

      | workspace | 各國相加（註記原本顯示） | DISTINCT 家族 | 國家數 |
      |---|---|---|---|
      | 滑雪機 | **46** | **40** | 4 |
      | 割草機 | 159 | 144 | 2 |

      ⚠ 那個 **46** 已經以「存活 46」的形式傳進 deepen 的文件——錯數字會擴散。

      修法：註記改講自己算得準的東西（佈局點數＋涵蓋國數），並明說跨國會重複計入；
      家族總數的權威口徑交給封面（§2）。**187 那個數字應是 08-17 引擎母體修好之前
      量的，已過時。**

- [x] 1.5b 家族口徑三個數字**已由 §2.2 收斂**
      滑雪機：`report_family_country` DISTINCT = **40**；各國相加 = **46**
      （deepen 文件記載的「存活 46」就是它）；`report_patent_base` 的
      `COUNT(DISTINCT 家族ID)` 於母體 = **48**。
      → **封面採 48**（§2.2）；46 已在 1.5 修掉來源，並換掉 `narrative.md` 的錯範例；
      40 是 `report_family_country` 自己的語意（有保護國家列的家族），不上封面。
- [x] 1.6 反向驗證：**5 則變異全數轉紅**——拿掉 WHERE／加回預設值／呼叫端傳 None／
      傳空清單／卸錯豁免；還原後全綠
      ⚠ 過程中抓到**我自己測試的假性通過**：原本只斷言 `patent_ids=` 出現，
      改成 `patent_ids=None` 照樣綠。已改為指名 `ctx.patent_ids`

## 2. 封面數字由引擎供給

- [x] 2.1 `report_data.cover_stats`（件／族／受理局／三分法）；`patent_ids` 必填無預設
- [x] 2.2 家族數口徑＝`FAMILY_ID_EXPRESSION` 於母體（沿用唯一定義處，不另寫）。滑雪機 48
      ⚠ 缺同族 ID 時各自算一族，不得併成一族「未知」
- [x] 2.3 三分法委派 `fetch_patent_kind_summary`；測試斷言封面不得出現 `document_kind`
- [x] 2.4 範本 `stats_note` 移除「存活家族」；第四格改專利類型；`narrative.md` 的「存活家族 46」錯範例一併換掉
- [x] 2.5 兩個 workspace 各驗一次（實測 9/9 全中）：滑雪機 55／48／4／17·27·11；
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
- [ ] 5.8 `test_topic_table_single_render` 兩條改成**驗行為**（2026-08-19 使用者裁決）

      現況：`test_section_carries_rows` 與 `test_variant_has_no_chart_file` 都用
      `re.search` 掃 `chart_runner` 原始碼找 `ctx.sections.append({"title": "分群…`。
      ⚠ 那個 anchor 在 **chart_runner 全歷史都不存在**（`git log -S '"title": "分群'`
      查無）——它從來沒對上過，兩條紅不是回歸，是一開始就沒在守。

      改法：實際跑出分群 section，斷言它帶 `rows`、且主題統計表的 variant 沒有
      chart file。⚠ source-grep 型測試的 anchor 會隨重構漂走而**不會有人發現**，
      因為漂走的症狀是「一直紅」而不是「突然紅」——本專案同型陷阱第 6 次。
      併進 §5 是因為它與 5.2 同屬「文件／測試與程式對不上」，收成同一套契約檢查。
- [ ] 5.4 同步 `prompts/report-narrative-flow.md`（family_country_layout、
      年度矩陣、country_distribution 狀態堆疊、topic_timeline 段落）
- [ ] 5.5 同步 `prompts/content_standard.md`（技術主題×2）
- [ ] 5.6 同步 `skills/html-report-to-deck/SKILL.md`＋`references/narrative.md`
- [ ] 5.7 同步 `add-deck-delivery-line/design.md` 的頁面盤點
      （現存有效×2、主題演進×3、年度矩陣×2、更多、技術主題）

## 6. 色票唯一定義處

### 🔴 6.0 實查結果（2026-08-19，唯讀，取代先前所有觀察值）

腳本 `scripts/audit_palette.py`（見 6.7）。⚠ 先前的 **28 種**（deepen 5b.1 原記）
與 **49 種**（08-18 觀察）都不是現況。

| 量測 | 值 |
|---|---|
| chart 側不同顏色 | **48 種**（出現 82 次：具名常數 33／散落 49） |
| chart 側**完全沒有具名常數**的顏色 | **24 種**（一半） |
| deck 側不同顏色 | **14 種**（具名 12／散落 12） |
| 兩側都出現＝同一份知識兩個落點 | **4 種**（`#00094A`／`#9CA3AF`／`#E5E7EB`／`#FFFFFF`） |

**⚠ 49 處散落不是同一種東西，分屬三個媒介**——這決定 6.3 的唯一定義處要不要分層：

| 媒介 | 處數 | 說明 |
|---|---|---|
| SVG 圖元（函式內區域 dict／字面） | 30 | `_TIER_COLORS`、`qcolors` 等函式內字典，與 `STATUS_COLORS` 同性質卻沒上升成模組常數 |
| HTML 報表 CSS 變數（`:root{--paper…}`） | 15 | 內嵌 f-string 模板，**已經有自己一套命名**（`--paper`／`--ink`／`--brand`），註解還寫著對應舊名（`--text`／`--border`／`--accent`）＝第三套命名 |
| HTML 表格內嵌樣式（source field 檢視頁） | 4 | 除錯用頁面 |

### 🔴 6.0a 實查新發現：顏色被當成語意選擇器（規格沒記，比色票分岔嚴重）

`skills/html-report-to-deck/scripts/rebuild_chip_chart.py` L57／L67：

```python
d["note"]   = next((t for a, _, t in head    if "#9CA3AF" in a), "")
d["footer"] = next((t for a, _, t in reversed(tail) if "#9CA3AF" in a), "")
```

它靠 **`#9CA3AF` 這個色值**辨認「哪一段文字是註記」。chart 側只要換掉那個灰，
這裡不會報錯，只會**靜默回空字串**——註記與頁尾直接消失。
⚠ 這比「兩套深藍」危險：深藍分岔只是看起來不一致，這個是功能會壞而且無聲。

`rebuild_chip_chart.py` 在 deck 側（`skills/`）卻產 SVG、用 chart 側的色，
12 處全部散落、0 個具名——它是兩側耦合最深的一個檔。

### 🔴 6.0b 「不得新增第 29 種」這條判準已經失效

deepen 5b.4 寫「不得新增第 29 種」，但實查是 48 種——在 `COLOR_TRANSFERRED`
（`#7C3AED`，chart_runner L371）之前就已經多出約 20 種。單獨點名它是任意的。

⚠ **數量鎖本來就守不住**（v5／v7／v9 同型）：它沒說「該從哪裡取色」，只說
「不准超過 N」，於是每個人都覺得自己那一個是必要的例外。
判準應改為**結構性**的 6.4：「散落裸 hex 為 0」——那是恆等式（做對＝唯一途徑），
不是代理指標。6.6 因此改成「登記 `COLOR_TRANSFERRED` 進色票表並補語意用途」，
不是撤回它。

- [x] 6.1 盤點：chart 側 hex 與 deck 側 `RGBColor` **跨語法**比對（結果見 6.0）
- [ ] 6.2 兩套深藍收斂（`#00094A` vs `#0B2545`）
      實查落點：`#00094A`＝`chart_runner.COLOR_TEXT` ＋ `rebuild_chip_chart` 5 處
      散落；`#0B2545`＝`deck_layout.TEXT`（L44）。⚠ 要**先問使用者留哪一個**——
      兩者肉眼近乎一樣，但改的是全份簡報與報表的主文字色，不是實作細節
- [ ] 6.3 定唯一定義處（比照字型定在 `chart_sizing`）
      ⚠ 依 6.0 的三媒介，唯一定義處要能同時服務 hex 字串（SVG／CSS）與
      `RGBColor`（PPTX）。⚠ 跨部署單元不能 import（`skills/` 是可攜的），
      `rebuild_chip_chart` 那條走「一方產生、一方消費」，不是 import
- [ ] 6.3a 🔴 先解 6.0a 的**顏色當語意選擇器**（`rebuild_chip_chart` L57／L67）
      ——它會讓 6.2 的收斂變成靜默破壞。⚠ **排在 6.2 之前做**
- [ ] 6.4 硬判準：`chart_runner` 散落裸 hex 為 0（模組層具名常數除外）
      基線 **49 處**（SVG 30／HTML CSS 15／表格樣式 4）。
      ⚠ 掃描器必須用 `ast` 取常數行範圍——正則＋括號計數會被內嵌 CSS 的
      `{{ }}` 帶歪（實測誤判成 68、61，真值 49）
- [ ] 6.5 軟揭露：色票表列出每個色的語意用途，新增時必須填
      ⚠ 24 種色目前**完全沒有具名常數**，補表時等於要為它們命名並寫用途；
      這是本節工作量的主體，不是 6.2
- [ ] 6.6 登記 `COLOR_TRANSFERRED` 進色票表並補語意用途
      （原文「補回…違反 5b.4 的新增」；5b.4 的數量鎖已失效，理由見 6.0b）
- [ ] 6.7 實查腳本落地成 `scripts/audit_palette.py`（唯讀），讓 6.4 的閘門與
      人工盤點讀同一份掃描邏輯，不各寫一份

## 7. 版型庫收斂（2026-08-18 新增）

⚠ 全部零自由度——只驗「有沒有、幾個」，不驗好壞。適用情境那類有自由度的，
一律留在輪二。

### 7a. 三份清單各說各話（根因）

實查：能畫的（`deck_layout`）、會擋的（`check_content`）、CLI 照抄的
（`content-template.json`）互不一致。`conclusions` 有畫法、有閘門，
**範本裡沒有**——CLI 不宣告就 `if not cc: return []` 靜默放行，那頁根本不產出。

- [x] 7a.1 Red：版型清單唯一定義處 `deck_layout.LAYOUTS`（比照 `ACTION_VERBS`）
- [x] 7a.2 Red：**三處同步閘門**——`check_content` 認得的、範本示範的、
      `narrative.md` 寫到的，三者集合必須等於 `LAYOUTS`；任一缺漏即紅
- [x] 7a.3 反向驗證：`LAYOUTS` 加一個不在範本裡的版型 → 閘門要紅
      ⚠ 這條會**當場抓出還有哪些版型漏登記**，不要跳過
- [x] 7a.4 `conclusions` 補進 `content-template.json`

### 7b. 結論頁閘門的三個破口

現況 `_check_conclusions` 的三行：

```python
if not cc: return []      # ① 沒宣告就放行
if not rows: ...          # ② 只驗非空——10 主題寫 1 列全綠
if topic in facts: ...    # ③ 主題名不在 facts 就整個跳過逐字比對
```

- [x] 7b.1 Red：`conclusions` 缺席時要紅（頁是必要的，不是可選的）
- [x] 7b.2 Red：主題名不在 `topic_facts` 時要紅（不得靜默略過比對）
- [x] 7b.3 Red：**涵蓋率對帳**——`conclusions` 要帶 `covered N/M` 與
      未涵蓋主題的逐條原因；缺對帳即紅
      ⚠ **不是最小列數**。規定列數是形式鎖（v5／v7／v9 三次的同型錯），
      會逼出硬湊；涵蓋率只要求「沒寫的要現形」，偏差從缺席變成可見清單
- [x] 7b.4 反向驗證：把某主題從結論刪掉但不更新對帳 → 要紅

### 7c. 表格通用版型

表格繪製**已經會了**（`slide_conclusions` 的四欄表、`CONCL_COLS` 欄寬），
只是綁死在那一頁。

- [x] 7c.1 抽成 `layout: "table"`：**參數化欄數與欄寬**（2026-08-18 使用者裁決）
      欄寬由版型依欄數計算，總和不得超過可用寬（沿用 `CONCL_COLS` 的內距紀律）
- [x] 7c.2 Red：欄數與資料列的欄位數不符即紅；欄寬總和超寬即紅
- [x] 7c.3 `slide_conclusions` 改用通用表格版型，不留第二份畫法

### 7d. 路線圖頁併入結論頁

2026-08-18 使用者裁決：**期程欄整個拿掉**；路線圖頁與結論頁合併成一頁。

⚠ 期程（`短期 0–3 個月` 等）是全份唯一沒有資料支撐的欄位——系統不知道人力、
預算與產品排程，那個數字必然是編的。

- [x] 7d.1 移除 `content-template.json` 的 `roadmap*` 區塊與三期程
- [x] 7d.2 結論頁改為**依 `ACTION_VERBS` 分組**呈現（同一批 rows，換分組）
- [x] 7d.3 排序依**外部訊號**：該主題的他人審查中件數（多者在前）
      ⚠ 這是對手給的時間壓力，有資料可查證；不是我們假設的月份
- [x] 7d.4 Red：`roadmap` 仍出現在 content 即紅（避免舊範本殘留）

### 7f. 專利行動改為可多選（2026-08-19 使用者裁決，規格回寫）

使用者：「一種策略也許適用多個主題，也可能只適用一個主題……但也不是說行動就
只能選擇一種而已。」前半（一動詞對多主題）7d.2 的分組本來就成立；後半不成立
——`action` 是單一字串，等於強迫一主題一行動。「先迴避設計、同時追蹤對手審查
中的案」寫不出來就得二選一，而被丟掉的那個**不留任何痕跡**（缺席型偏差）。

- [x] 7f.1 `deck_layout.row_actions()`＝解析唯一定義處（字串／list 都收）；
      `check_content._bad_actions()` 讀同一份——多值最容易漏「只驗第一個」
- [x] 7f.2 該列在每個宣告動詞的分組下各出現一次；行動格印本分組動詞，
      其餘掛「同時：X」灰小字，避免讀者以為分組錯了
- [x] 7f.3 ⚠ **不加**「至多／至少 N 種」數量鎖（v5／v7／v9 形式鎖同型）。
      約束留在既有那條：每個動詞都要能從同列「判讀」推得出來
- [x] 7f.4 半真機械鏈實跑到多動詞列（`test_deck_runner_semireal` 代打 CLI）

### 7g. 兩個只有真產 pptx 才會炸的錯（半真鏈抓到，2026-08-19）

⚠ 兩個都是「同一份知識兩個落點」，而且**deck 側單元測試全綠**——
SVG 路徑與 PPTX 路徑對同一份輸入不同意，沒有人對帳。

- [x] 7g.1 `{"size": 11}` 裸 int：SVG 讀 pt 數值正常，PPTX 當成 11 EMU
      → 0 centipoints 直接 ValueError。修在 `_set_font`（唯一落點）。
      ⚠ 判斷用 `Length` 不是 `int`——`Pt(16)` 是 int 的子類別，第一版修法
      把既有 `B_SIZE` 二次換算，同一個 ValueError 換個數字
- [x] 7g.2 字級白名單兩份（`deck_layout` 定 24/16、`audit_deck` 寫死
      `"24,16"`）→ 收斂成 `ALLOWED_SIZES`，audit 預設讀它；
      新增 `S_SIZE = Pt(11)` 註記小字層級（不是「塞不下就縮字」的後門）

### 7e. 引擎供給外部訊號

- [x] 7e.1 `cluster_topic_table` 加法律狀態分解（`_status_breakdown`）
      ⚠ 走 `mappings/legal_status` 唯一定義處，不在此重判
- [x] 7e.2 不新增查詢——法律狀態併進 loader 既有的申請人查詢（loader 註解明訂 patents 是單一入口）；範圍沿用 cluster 的 workspace scope
- [x] 7e.3 ⚠ **無法用兩 workspace 比對**：只有滑雪機有分群資料（割草機 0 主題）。改以「合計 == 分群母體」與「每主題合計 == 該主題件數」兩條驗
- [x] 7e.4 🔴 **合計對上 cluster 母體**——實測合計 44、成員 55，未把排除的算進來
      實測滑雪機：workspace 成員 55、cluster 指派 **44**（11 件外觀設計分不了群）。
      分解件數合計要等於 **44**；寫成 55 就是把「刻意排除」偽裝成「全都算到了」，
      那才是真的新種一個同型錯。
- [x] 7e.5 主題分析 section 的 note 帶母體字串（分群 44／workspace 55，並說明設計案不進分群）
      ⚠ 這是揭露不是修正——「為什麼是 44」由 deepen §3 處理

## 8. 驗收

- [ ] 8.1 OpenSpec strict validation
- [ ] 8.2 三層範圍回歸（直接／整合／契約）
      ＋ **符號反查消費者**比對已跑清單（2026-08-18 實證：憑印象挑會漏 6 支）
- [ ] 8.3 兩個 workspace 各產一份報表，逐項對 design §7 判準
- [ ] 8.4 版型庫：`LAYOUTS` 三處同步閘門綠；`conclusions` 實際產得出來
      （⚠ 不是「閘門沒紅」——要真的看到那一頁）
- [ ] 8.5 揭露未覆蓋範圍；⚠ 本 change **不含**有自由度的閘門
      （依據層級、行動對象、表格適用情境）——全在輪二
- [ ] 8.6 使用者接受後 archive；同步 main specs 與 migration ledger
