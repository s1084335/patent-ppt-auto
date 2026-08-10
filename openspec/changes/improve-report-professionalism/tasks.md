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

- [x] 2.1 Red：為 registry、dataset schema、單位/分母、subclass 門檻、命名發現與 label truncation 新增失敗測試並記錄原因
- [x] 2.2 Green：最小修改 report definition、transform、renderer 與 narrative input，使資料契約測試通過

  ### 2.1／2.2 實際做了什麼

  | 項目 | 狀態 |
  |---|---|
  | registry | ✅ catalog 13→12（刪 `lifecycle`），新增引用完整性守門 |
  | 單位／分母 | ✅ 補 `publication_trend`／`country_distribution` 母體原因；`opportunity_quadrant` 標為非件數單位；新增 `SAME_AS_TOTAL_REPORTS` 明列「檢查過、確認相等」 |
  | subclass 門檻 | ✅ 已實作（2026-08-05 定案），本次補契約測試 |
  | label truncation | ✅ 已實作（F-12），本次補契約測試並釘住「不得指向已移除的附錄 2」 |
  | 命名發現 | ✅ **已實作**——`ai_narrative_runner` 鎖七·具名（Q14／RPT-012）：整頁沒點到任何具名對象即發警告。⚠ 我一度誤判它「只寫在 prompt 裡」，查完才確認有程式驗證 |

  ⚠ **本輪發現「測試綠不代表能跑」**：刪掉 `lifecycle` 後全部報表測試依然全綠，
  實際產圖卻炸 `ValueError: Unknown report`——`chart_runner` 的 SECTION_SPECS、
  圖檔對應表與讀圖說明各留死引用。新增的三支引用完整性測試把這類問題守在
  單元測試層。
- [ ] 2.3 Red：新增 HTML/PPT artifact persistence、零上傳失敗、reader guide／Key Player 可重用內容元件、evidence identity 與雙通道輸出測試；不斷言固定頁碼或固定三頁

  ### 2.3 Key Player：查證後範圍縮小（2026-08-10）

  ⚠ **兩軸不需重新設計**：2026-08-07 已定案「橫軸＝跨國布局深度（國數）、縱軸＝
  技術廣度（主題數）、泡泡＝家族件數、顏色＝定位分類」，形狀照範例（滑雪機 V2 p7）
  的泡泡象限圖，且定案明載「**不得做成屬性表**」。現行實作符合。

  | # | 工作 | 依據 | 狀態 |
  |---|---|---|---|
  | 1 | 定位分類的推導規則寫進讀圖須知 | 使用者裁決：顏色標籤沒有依據就是視覺噪音 | ✅ `cd4f70b` |
  | 2 | 修正名實不符（「申請人四面向」→「Key Players 競爭定位」） | 定案原文：四面向是資料維度，圖是競爭定位圖 | ✅ `cd4f70b` |
  | 3 | **每家的代表專利，且要帶技術內容摘要** | 使用者裁決 ＋ 定案⑥ | ✅ `c377f85`＋`526aa7d` |

  第 3 項的三個待決問題**由使用者裁決為「讓 CLI 自行查」**（2026-08-10，原話：
  「要發揮讓系統 CLI 自行查資料的優勢，不然我允許權限讓 CLI 找證據的用意就白費了」）：
  資料層只交 `patent_ids`（該家全部專利 id），摘要由 CLI 自己查 `文獻備註` 產生
  ——不預先決定取幾件、也不由引擎產摘要。取證入口規則與正反例寫在
  `content_standard.md` 第 6 節。

  ### 第 3 項的具體要求與待決問題

  **要求**（2026-08-10 使用者補充）：代表專利**不得只給標題**，要有簡短的技術
  內容摘要，讓人看得出這件專利在做什麼。

  ⚠ 這是 2026-08-07 定案⑥「解讀深度下沉到專利內容層」的同一條要求：
  ❌「2024 電機化」／✅「2024 電機自鎖：法蘭擋板＋電磁鐵解鎖，5 件同架構」。

  **待決**（動工前要問使用者）：

  1. **摘要從哪來**——① 確定性取既有欄位（獨立項／效果摘要前 N 字，不花 AI 額度
     但可能語句不完整）；② AI 產生短摘要（品質好但要走 CLI 通道、要 guard）。
     ⚠ 依既有原則「摘要屬於 AI 的工作」，但也依「確定性結果先顯示、不等 AI」，
     可能要兩者並存（先給確定性版本，AI 完成後覆蓋）。
  2. **每家取幾件**——一件夠不夠代表？多件會不會擠爆版面？
  3. **落點**——象限圖旁的卡片、獨立的 Key Player 深入頁、還是報表頁的展開區？

  **實作範圍**：`content_blocks.key_player_profiles` 目前**沒有**代表專利欄位，
  要一路補：`strength_rows` 查詢帶專利號與內容欄 → profile 聚合 →
  `applicant_strength_rows` 攤平 → 顯示端。
- [ ] 2.4 Green：完成必要輸出與持久化，使 job succeeded 對應可讀回 artifact，並讓內容元件可由 goal-driven SlidePlan 消費

  ### 2.4 查證與進度（2026-08-10）

  | 項 | 狀態 |
  |---|---|
  | artifact 持久化 | ✅ 已實作——`handlers.py:416` 產完即 `upload_run_dir`；例外**不吞**，上傳失敗即 job 失敗（結構上滿足「succeeded 對應讀得回」） |
  | Key Player 內容元件可被消費 | ✅ `chart_runner.applicant_strength_rows` 呼叫 `content_blocks.key_player_profiles`（唯一定義處） |
  | reader guide 內容元件可被消費 | ✅ `3833796`——⚠ 查證發現 `reader_guide_blocks()` **全庫只有測試在呼叫**，沒有生產端消費者；已沿 `encoding_notes` 同一條通道加進 `table_display` |
  | 判讀說明頁的內容規則 | ✅ `17c4ca7`——從滑雪機 V2 p11 反解四個必填區與四個偏差角度，寫進 `content_standard.md` 5-1 |
  | 「零上傳失敗」實跑驗證 | ✅ job 251 `report_generate` succeeded，`artifacts_uploaded: 31`、`has_cluster_analytics: True`；磁碟 31 = DB 31 **雙向零差異**；`table_display.reader_guide` 4 條實際落地 |

  ⚠ **落點誤判已修**：一度把口徑定義（計數單位／同族合併／共同申請／分類覆蓋）
  當成範例頁上的「可觀測性偏差」。前者答「數字怎麼算」、後者答「不能怎麼推論」，
  兩者不得互相頂替——界線已寫進 `content_standard.md` 與 `SKILL.md`，並加測試守住。
- [ ] 2.5 Refactor：測試全綠後移除被取代圖表與重複 narrative/renderer 邏輯

## 3. 驗證與輸出

- [x] 3.1 執行 report/transform/renderer/narrative 目標測試、相關模組回歸與 `scripts/verify_module.py`
- [ ] 3.2 產生 HTML、goal-driven PPTX、manifest/metadata 與 narratives/evidence artifact，核對檔案存在、選圖／章節、dataset id 與 checksum；不以固定頁數判定成功

  ### 端到端實跑（2026-08-10）：走完整正式路徑，查出三個缺陷

  資料集 workspace 3（滑雪機 55 件）。**每一段都走 job 佇列與 AI bridge，不手動繞路**
  ——這是三個缺陷得以現形的唯一原因，前面所有單元測試都是綠的。

  | 段 | job | 結果 |
  |---|---|---|
  | 報表產製 | 251 `report_generate` | ✅ 31 檔，磁碟＝DB |
  | 逐報表解讀 | 252 `ai:narrative` | ✅ 15 個 variants 全數具名產出 |
  | 目標規劃 | 253 → 254 → 256 `ai:report_plan` | 253 failed（缺陷一）、254 succeeded、256 驗回存修正 |
  | PPT 組版 | 255 → 257 `ai:report_ppt` | 255 走成固定頁序（缺陷二）、257 驗修正 |

  #### 🔴 缺陷一：無圖 variant 被選中會崩潰（阻塞正式**預設**路徑）

  `cluster_topic_table` 的 `topic_table_tech`／`_effect` 兩個 variant 刻意沒有圖檔
  （主題統計表是表格，variant 只是解讀掛點），但存在性檢查踩空：
  `run_dir / ""` 等於 run_dir 本身，`.exists()` 回 True → `shutil.copy2` 對目錄操作
  → `PermissionError`，訊息看不出真因。

  ⚠ 前端 `loadPptChartPicker` **預設全選**，使用者按下 PPT 按鈕必然送出這兩個
  ——也就是正式預設路徑本來就是壞的。修正 `c9e6a6a`（前端過濾＋後端明確報錯）。

  #### 🔴 缺陷二：SlidePlan 沒回存 DB，goal-driven 整條路徑是斷的

  `ai:report_plan` 把 plan 寫進本機 `report_data.json` 後沒有 `upload_run_dir`；
  下游 `ai:report_ppt` 從 DB materialize，拿到沒有 plan 的版本，`resolve_layout`
  **靜默退回固定頁序**。

  | | 規劃 | 實際產出 |
  |---|---|---|
  | 頁數 | 11 | 14 |
  | 版型 | cover → exec_summary → … → **kp_quadrant** → reading_guide | cover → chart_hero ×6 → … → direction → 附錄 ×3 |

  且 manifest 的 `missing_reports` 含 `applicant_strength_profile`——**Key Player
  象限圖整個沒進 PPT**。⚠ 全程沒有任何錯誤訊息：「找不到 plan 就用固定頁序」是
  設計上的保底行為，斷鏈與「使用者根本沒規劃」在下游長得一模一樣。
  修正 `8d20c39`。跨容器成因同 2026-07-23 定案，這是該坑的第二次出現。

  #### 🔴 缺陷四：section 的 report_key 是檔名，選的圖從簡報上消失

  SlidePlan 修好之後重跑，PPT 確實照規劃出 11 頁，但**兩頁降級成 `stat_callout`、
  `charts=[]`**——使用者選的圖直接不見：

      p3 stat_callout ← degraded_from=chart_with_points  report_keys=['annual_trend']

  組版端取 chart_identity 前段當 report_key 去 `artifact_manifest` 反查，
  但 manifest 用 **registry 鍵**。引擎端兩個錯法：趨勢 section **寫死檔名**
  `annual_trend`（registry 是 `application_trend`）、IPC／CPC section **漏設**而
  fallback 成 `ipc_main_distribution_L4`。

  ⚠ **2026-07-27 就診斷過同一個問題**：`test_chart_sections` 的 docstring 明寫
  「SVG 檔名（annual_trend…）與報表鍵（application_trend…）不同名，查找必然落空」，
  當時也照做了「section 一律顯式帶 report_key」——**但值填成檔名**，測試再把錯值
  釘住。錯值能存活至今是因為通則測試只要求「查找鍵能取到 rows」，而 `chart_rows`
  裡剛好也有 `annual_trend` 這個鍵：兩支測試都綠，問題原地不動了兩週。

  修正 `2595d57`。**缺陷三一併解掉**——section 的 report_key 就是 identity 前段，
  改對之後與 `profile_manifest` 那套自然一致，不需要任何映射表，
  `ipc_main_distribution_L4:L5` 這種自相矛盾組合也消失。

  #### 🔴 缺陷五：CLI 可以不查資料庫就直接寫（使用者定案要擋）

  `run_report_planning` 把 `query_audit` 放進結果並註解「空清單有意義——代表這次
  規劃完全沒有查證」，⚠ 但**沒有任何地方會因為它是空的而失敗**，等於允許 CLI
  只讀聚合數字就編出整份敘述。`content_standard.md` 第三節那條規則原本只寫在
  給 AI 看的提示裡。

  使用者定案：「我給他的數據報表都是一定要產的，這個就是給模組控制，CLI 是根據
  這些內容去判斷要找啥證據來寫，所以**不能讓它可以不去查資料庫就直接寫**」。

  修正 `31f8e81`：`validate_research_effort` 判準只有一條「至少一次成功查詢」
  ——查幾次、查什麼由 CLI 依內容判斷，規則不越俎代庖；prompt 同步寫明要求
  （只擋不說會讓 CLI 一直撞牆）。

  #### 缺陷三：選圖 identity 兩套命名 → 已隨缺陷四一併解決

  | 來源 | 同一張圖 |
  |---|---|
  | `profile_manifest.json` | `application_trend:default`（registry report key） |
  | `report_data.sections` → `chart_bundle` → 前端 | `annual_trend:default`（section key） |

  前端與 `chart_bundle` 一致，故後者是對的。⚠ 但兩份檔案**都叫 identity、都是
  `xxx:yyy` 形狀、值卻不同**，是本專案第五次「同一份知識兩個落點」。
  另 `cpc_main_distribution_L4:L5`（section key 已含 `_L4` 再接 variant `L5`）
  這種自相矛盾組合也在可用清單裡。

  ⚠ **本段的方法論結論**：這三個缺陷沒有一個能被單元測試抓到——它們都在
  「兩個元件之間」而非元件內部。跨容器傳遞、預設選項、靜默降級三者共同的特徵是
  **失敗時看起來像正常行為**。往後凡是「A 寫檔、B 讀檔」的接縫都要有一支
  端到端測試或實跑驗證，不能只驗兩端各自的單元行為。
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
