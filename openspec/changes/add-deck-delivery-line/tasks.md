# Tasks — add-deck-delivery-line

分支：`feat/add-deck-delivery-line`。前置：`unify-chart-source` 實作並驗收
（intake 吃版本目錄，**2026-08-12 已完成**）。流程本體（九步演算法、版面、
閘門門檻）零改動。

## 0. 前置（獨立於本 change，先做完再填 deck 面）

- [x] 0.1 「匯出報告」頁**清空 PPT 線殘骸**（design §6）：`btn-export-ppt`
      （打已移除的 `ai:report_plan`，必定 422）、`ppt-goal-input`、
      `ppt-chart-picker`、`exportPreview` 的 PPT 狀態（`pptFiles`／
      `selectedPptFile`／`pptViewer`／`editMode`／`edits`）、
      localStorage 編輯稿孤兒（`EXPORT_EDIT_KEY_PREFIX`）、與報表種類頁重複的
      版本下拉與整份預覽、**`static/vendor/pptx-renderer/` 1.5 MB 資產**。
      ⚠ 頁面與導覽項**保留**（deck 要進駐），清空期間放一句「簡報產製規劃中」。
      驗收＝殘留引用歸零＋前端契約測試綠。
      **2026-08-13 完成**（commit `7919059`）：另清出兩項未在原清單的殘骸——
      前端自組單頁 HTML（`reviewExportOutput`／`buildExportHtml`，與引擎產出
      必然分岔）與不可達的封面／解讀編輯分支。驗證：目標＋範圍回歸 157 passed、
      `node --check` 驗 JS 語法、掃 inline 事件與一般呼叫確認無斷引用。
      退場契約反轉的 16 支舊測試，新契約在 `tests/test_export_page_cleanup.py`。

## 1. skill 遷入產品 repo

- [x] 1.1 `.agents/skills/html-report-to-deck/` → `skills/html-report-to-deck/`；
      SKILL.md 依硬規範（AGENTS.md §Skill 撰寫硬規範）重寫兩區；
      「非產品線」邊界註記依 2026-08-12 定案改寫留痕。
      🔴 **2026-08-13 兩處修正**（原條文在 B 案前提下寫的）：
      ① **COM 目視留在 Runbook，不收進開發備註**——原文把它當開發量尺，
         但目視迴圈已定案進產線（design 4-0），它是 Runbook 的一步。
         收進開發備註的只剩 `regression.py` 與 `pitfalls.md`。
      ② **Runbook 區的讀者＝撰稿 CLI，不是「照九步手動跑的人」**。
         產品側九步由 runner 以 subprocess 驅動（design §1），CLI 只實際做兩件事：
         **第 5 步撰稿**（吃 plan.json／report.json → 出 content.json，含輸出契約
         與唯讀 MCP 取證四條硬規則）與**目視迴圈**（看逐頁 PNG、檢查清單、
         發現問題只能改 content.json）。九步全流程屬 runner 職責，連同開發側
         手動操作一併收進開發備註。
      ⚠ 硬規範要求 Runbook 區「不得出現開發機路徑」——現行 SKILL.md 的
      `uv run --no-project --with ...` 指令與 `D:\vscode\...` 路徑都要清掉
      （執行方式改走專案環境，見 2.2d）。
      **2026-08-13 完成**：原樣複製後重寫兩區。Runbook＝撰稿＋目視迴圈
      （含輸入輸出契約、頁型容量、schema、取證四規則、閘門讀法、優先順序梯子、
      授權界線、回報格式）；開發備註＝九步手動流程、驗證分層、配色與幾何、
      regression／check_docs、產線環境需求（只交叉引用 design 4-0b 不另抄）。
      邊界註記已改寫留痕：舊版寫「不是產品交付線」，deck 接線後該句失效。
      驗證：`check_docs.py` **0 問題**——8 條數字措辭全保住，且 CLI 步驟 1–9
      仍被辨識（⚠ 該檢查是 `if blocks:`，格式一變會**靜默跳過**而非變紅，
      故以「輸出有印出步驟清單」為準，不只看問題數）。
- [x] 1.2 開發機路徑參數化補完（`regression.py` 的 `PPTX_TO_PNG`，原寫死
      `D:\vscode\ppt-tools\pptx_to_png.py`）；⚠ 目視轉圖進產線後，該路徑
      **不只是開發便利**——runner 也要解得到，需與 2.2d 的環境收斂一起定；
      跑 `check_docs.py`＋`regression.py` 確認遷移零破壞
      **2026-08-13 完成**：改為環境變數 `PPTX_TO_PNG` 優先、預設回開發機路徑
      （沿 `fit_render_charts.PLAYWRIGHT_HOME` 同一套慣例）；加前置存在性檢查——
      轉圖排在最後一步，缺了會讓前面四步白跑約一分鐘，且 subprocess 只會噴
      「找不到檔案」，看不出是環境沒設好還是腳本壞了。SKILL.md 兩處同步。
      驗證：`check_docs.py` 0 問題；故意指到不存在路徑→提早擋下並給出設定指引；
      預設路徑跑完整回歸 **五步全過、8 頁逐像素與基準相同**（順帶證實 COM 轉圖
      在互動式 session 下可用；⚠ 產線的非互動 session 條件仍待 2.2e 實測）。
- [ ] 1.3 中央份刪除；`.agents/context/README.md` 路由與引用更新
      ⚠ **排在合主線之後**：中央份 `.agents/skills/html-report-to-deck/` 是開發側
      現在唯一拿得到的一份，repo 內那份還在 worktree 分支上。提前刪會讓開發側
      手動流程在合併前無處可跑。
- [x] 1.4 新 `assemble_from_version.py` intake（自 unify-chart-source 移入，
      2026-08-12：它本來就是 deck 第 1 步）——版本目錄／asset 端點 → 既有
      `report.json`＋`charts/` 中間格式；texts←narratives.json、
      tables/patent_ids←report_data rows、notes←encoding_notes＋reader_guide、
      report_meta←version_meta（含 workspace 名稱，見 design 4b）；
      `extract_report.py` 降 HTML fallback
      **2026-08-13 完成**：TDD 13 支綠。自真實產物
      （`report_trial_20260812_133901`）反解出**五個陷阱**並逐一守住：
      ①`variant.file` 可能是空字串（主題統計表＝解讀落點，不是圖）
      ②`more_variants`（第 11–20 名）須剔除
      ③rows 散在 `reports[key]`／`section`／`variant` **三處**，只讀一處會靜默少表
      ④notes 併三來源（`section.note`＋`encoding_notes`＋`reader_guide`）
      ⑤`narratives.json` 可能不存在，texts 留空而非炸掉
      ⚠ 實作期再發現第六個：帶 `rows` 的 variant（Key Players、機會矩陣、主題演進）
      **沒有 `narrative_key`**，缺鍵時以 `report_key:variant_key` 推導，
      否則那些章節的解讀會靜默消失。
      實跑真實版本目錄：9 章節、13 張圖（14 張 SVG 剔除 more）、註記 5／2／2…，
      判讀 0（該版本未跑解讀，符合預期）。

## 2. TDD：B 案組版輸出層（窄轉換器）

🔴 **2026-08-13 第 3 次裁決：恢復 B 案**（沿革與三項實測依據見 design 4-0）。
本節曾於同日上午整組撤銷，下午恢復；`~~刪除線~~` 的撤銷註記已清除，避免
下次讀到自相矛盾的條文。

- [x] 2.1 Red→Green：轉換器契約——元素詞彙、SVG→DrawingML 映射、
      文字逐行定位＋關 wrap、超出詞彙 fail loud
      **2026-08-13 完成**（`skills/html-report-to-deck/scripts/svg_to_pptx.py`，14 支綠）
      - 詞彙自 `deck_layout.py` 反解＝**三種**原生元素：`<rect>`→`add_shape`
        （圓角看 `rx`）、`<text>`→`add_textbox`、`<image>`→`add_picture`。
        ⚠ 「線」**不另立詞彙**——它是矩形的退化形（`RULE_W = 0.014in` 細 rect），
        另立就會有兩個表示法。
      - 座標系 **96 dpi**（1280×720 ＝ 13.333×7.5in）。依據是
        `deck_layout.py:73` 的既有註記「0.008in 在 96dpi 下只有 0.77px」。
      - 🔴 端到端煙霧測試（SVG→pptx→COM 轉圖，一頁）：標題／副標／圓角卡／
        **1.344px 細線**／三行均勻間距／圖片位置尺寸／右邊界豎線／頁尾**全部吻合**。
      - 🔴 **同一份 SVG 用 Chromium 截圖對照，抓到兩個真問題**：
        ①圖片破圖——`set_content` 沒有 base URL，`file://` 被跨來源擋掉
        （COM 轉圖卻正常）。⚠ 若不處理，目視會看到假警報，更糟的是日後
        真的出錯時分不出是「真錯」還是「又是載入問題」。
        → 目視截圖**必須 SVG 存檔後 `goto`**，圖用相對路徑（見 2.2）。
        ②轉換器原本以 cwd 解析相對圖檔路徑——已修為**相對於 SVG 檔所在目錄**，
        並補測試；不修的話 runner 從別的目錄呼叫會靜默找不到圖。
- [x] 2.2 Green：`deck_layout` 輸出層改組 SVG＋窄轉換器；逐頁截圖產出（目視 PNG）。
      **2026-08-13 完成，三塊到齊**：①引擎自行斷行（`wrap_lines`＋避頭尾，
      8 支綠／438 subtests）②SVG 輸出層（`svg_canvas` ＋ `_compose` 抽出頁型
      呼叫，pptx 與 SVG 兩端共用；6 支綠）③逐頁截圖（`shoot_pages`，4 支綠）。
      🔴 斷行與 `est_lines` **鎖在同一套係數**（都走 `_per_line`），並以
      `test_line_count_matches_est_lines` 直接斷言 `len(wrap_lines) == est_lines`
      ——兩者各算各的就是「估高一套、排版另一套」，不一致不會報錯，
      只會讓版面偶爾溢出或留白。
      避頭尾採**回推**（把前一字移到下一行）而非懸掛：本 skill 絕對定位，
      標點突出右邊界會撞到右側元素（標籤欄頁右欄緊貼邊界）。
      ⚠ 改 `deck_layout` 後跑 `regression.py`：**8 頁逐像素與基準相同**，
      既有組版零破壞。
      **Chromium BBox 取代估算，寬高皆可用**（design 4c-1a，Windows-only）。
      ⚠ `fit_render_charts` 的 Chromium `getBBox` **本來就在用**（找不撞版的最大
      字級），不是 B 案才引入的——那部分不動。
      🆕 **避頭尾改由引擎決定**（design 4-0 第 3 次裁決依據 2）：斷行寫死後，
      行首中文標點不再依賴 PowerPoint 的禁則處理（現行 `deck_layout.py:183`
      補的 `eaLnBrk`／`hangingPunct` 是「請 PowerPoint 照做」，B 案是自己斷）。
      補測試鎖住：任一行不得以中文標點起首。
      🔴 **逐頁截圖：SVG 存檔後用 `page.goto(file://…)`，不得用 `set_content`**
      （2026-08-13 實測，見 2.1 發現①）：`set_content` 沒有 base URL，
      SVG 內的圖片會**破圖**而 pptx 卻是好的——目視因此看到假警報。
      圖檔在 SVG 同目錄、用相對路徑引用。
- [x] 2.2f 🆕 **目視截圖解析度由 `deck_layout` 導出**（design 4-0c）：
      ⚠ **不得在 runner、skill、規格各寫一個數字**——那是上一世代
      「同一份知識多個落點」的錯法。`deck_layout` 已是版面幾何與字級的唯一定義處，
      截圖倍率從那裡推導，runner 只消費。
      ⚠ 值本身**由 2.3 實測決定**，本步只建立推導管道與消費點，不預先填數字。
- [x] ~~2.2a `overlaps()` 撞版判定改為「橫向量測、縱向推導」~~
      **2026-08-13 撤銷**（design 4c-1a）：Windows-only 後 w／h 都吃量測值不會
      造成跨 OS 靜默不一致，現行寫法即正確，不需改動。
- [x] 2.2d 🆕 **執行方式改走專案環境**（接線前置，design 4-0b）：skill 現行以
      `uv run --no-project --python 3.12 --with python-pptx --with pillow` 臨時拉
      套件，等於在使用者機器上要求「有 uv ＋能連網拉套件」。
      backend 環境**已有** `python-pptx 1.0.2`（`pyproject.toml` 列著），
      改由 runner 用專案 interpreter 跑腳本，依賴交給 `pyproject` 管。
      ⚠ `comtypes` **不必**列入 `pyproject`（第 3 次裁決後 COM 只在開發機用於
      映射校驗，不進產線；原條文要求列入是 COM 進產線時的需求）。
      **2026-08-13 完成**，兩件事都做：
      ① **收斂路徑解析**：`fit_render_charts` 與 `shoot_pages` 原本各寫一份相同
         的三行，改一處不同步、症狀是「一支找不到瀏覽器、另一支正常」。
         收進 `browser_env.ensure_playwright()`。
      ② **`playwright>=1.62.0` 列入 `pyproject`**。
         ⚠ 我一度只做 ①、跳過 ②，理由是「vendored ＋ 環境變數本來就可攜」
         ——**那是把前提當結論**：只在「機器上已有那個目錄」時成立。
         實測：專案 venv `import playwright` 直接 `ModuleNotFoundError`；
         把 `PLAYWRIGHT_HOME` 指到不存在的路徑也一樣掛。
         影響是四項實質問題：`uv sync` 後跑不起來、產線多一個無人檢查的手動
         步驟、版本釘不住（chromium 版本是 `getBBox` 的變因）、CI／容器不能跑。
         兩者**不互斥**，應該都做。
      ✅ **已安裝並移除過渡回退**：`playwright==1.62.0` 裝進現用的 venv
      ⚠ 實查：**1.62.0 就是目前最新**（`uv --upgrade` 只想動 greenlet 與
      typing-extensions 兩個相依套件）。我原本寫「釘版而非最新，裝最新會對不上
      chromium-1234」——那是**憑推論寫進紀錄**，已更正。`pyproject` 寫
      `>=1.62.0` 是釘下限，日後真的升版時要重驗 browsers 相容性。
      ⚠ 沒在 `deck-work` 跑 `uv sync`：該 worktree 無 `.venv`，sync 會從頭建
      含 torch 數 GB；實際在用的是主工作樹的 venv，而 `pyproject` 改動還在本
      分支上——**合主線後主工作樹 `uv sync` 才會自然涵蓋**。
      驗證：`playwright.__file__` 指向專案 venv、回退不再啟用、
      `shoot_pages`／`fit_render_charts` 實跑正常（browsers 沿用既有，未重下載）。
      ⚠ 瀏覽器本體不進 `pyproject`（150–400 MB），另裝或用環境變數指。
- [ ] 2.2e 🆕 **CLI 執行身分實測**（design 4-0b 唯一剩下的 🔴 條件）：
      `_CLI_SPECS` 的 `"binary": "claude"` 靠 PATH 解析，實測開發機的 CLI 在
      使用者 profile 底下（`C:\Users\user\.local\bin\claude.exe`，2.1.217），
      服務身分的 PATH 通常不含該目錄、認證也綁 profile。
      以 Companion 實際的執行身分跑一次真 CLI，確認起得來且認證過；
      解不到則改為絕對路徑設定或指定服務執行身分，結果寫回 4-0b。
      🔴 **驗收＝實跑 CLI 成功**，不是「確認二進位存在」。
      ⚠ 原條文還要求驗 COM 的非互動 session——**第 3 次裁決後不需要**
      （目視改走 Chromium，產線無 PowerPoint）。
- [x] 2.2b 🔴 **字型收斂＝Noto Sans TC**（design 4c，前置於映射校驗）：
      收斂為**單一常數唯一落點**。
      ⚠ **2026-08-13 實掃：不是四處，是九處**（原條文低估）：

      | # | 位置 | 現值 |
      |---|---|---|
      | 1 | `deck_layout.py:42` `FONT` | `Microsoft JhengHei` |
      | 2 | `rebuild_chip_chart.py:112` SVG 根元素 | `Segoe UI, sans-serif` |
      | 3 | `chart_runner.py:295` `SVG_FONT_STYLE` | `'Microsoft JhengHei','Segoe UI'` |
      | 4–7 | `chart_runner.py` SVG 根元素 ×4（`1561`／`1763`／`1873`／`4307`） | `Segoe UI, sans-serif` |
      | 8 | `chart_runner.py:3194` HTML 報表頁 | ✅ 已 Noto 優先（線一改的） |
      | 9 | `chart_runner.py:3990` 另一處 HTML | `Microsoft JhengHei／Segoe UI／Arial` |

      ⚠ **3 與 4–7 互相矛盾**：`SVG_FONT_STYLE` 宣告正黑體，但四個 SVG 根元素
      宣告 Segoe UI（中文靠 fallback）——同一張圖兩種宣告，已經是漂移的證據。
      🔴 **這不只是整潔**：`fit_render_charts` 用 `getBBox` 量 SVG 文字來決定
      圖內字級，字型不一致 → 量測錯 → **字級跟著錯**，而且不會報錯。
      🔴 **待裁決：唯一定義處放哪**（涉及 skill 的自足性）。
      判準是「改圖表字型時簡報原生文字也要跟著改」＝同一份知識。
      選項 A：放 `chart_sizing.py`，skill `import` 它（需先做 2.2d 的環境收斂；
      代價是 skill 不再能獨立於 backend 執行）。
      選項 B：環境變數為唯一事實來源，兩邊都讀（skill 保持自足，但 fallback
      預設值仍會有兩處）。
      **2026-08-13 完成**：唯一定義處＝`chart_sizing.FONT_FAMILY`／`FONT_STACK`
      （後者由前者導出，不重打字型名）；九處全部改為引用；deck skill 走
      `parents[3]` 插 sys.path 後 import（`backend/app/reports/__init__.py` 是空的，
      無副作用）。新增 `test_font_single_source.py` 6 支守住。
      ⚠ 系統已確認裝有 `NotoSansTC-VF.ttf`（11.39 MB，註冊表有登記）——
      **先確認再重建基準**，否則基準會建立在 fallback 字型上。
      regression 8 頁如預期全紅（字型換了），目視確認版面正常後 `--update`
      重建並複驗逐像素相同。
- [x] 2.2b-1 🔴 **`LS_RENDER` 的模型形狀是錯的**（2026-08-13 重量時發現，
      比換字型本身更要緊）：

      | 字級 | 行數 | 實測高(pt) | 倍率 |
      |---|---|---|---|
      | 16 | 1 | 21.39 | 1.337 |
      | 16 | 2 | 44.82 | 1.401 |
      | 16 | 3 | 68.24 | 1.422 |
      | 24 | 1 | 32.08 | 1.337 |
      | 24 | 3 | 102.35 | 1.422 |
      | 24 | 4 | 137.49 | 1.432 |

      倍率隨行數上升 → 「行數 × 字級 × 固定倍率」的模型不成立。拆開算：
      **首行 1.337、後續每行 1.464**（16pt 與 24pt 完全一致，誤差 <0.1%）。
      現行 `LS_RENDER = 1.40` 在 2 行剛好吻合、**3 行以上一路低估**
      （3 行差 0.065 em、4 行差 0.129 em）。
      ⚠ `deck_layout.py` 檔頭早就警告「段數一多就會把最後一行切掉」——
      **根因就在這裡**：不是數值不夠大，是模型形狀不對。
      改法：`text_h()` 改兩段式 `首行 × 1.337 + (n-1) × 1.464`。
      ⚠ 連帶：`budget()` 的所有上限、各頁型裕度、regression 基準都會變，
      要一起重驗。這是獨立批次，不併進 2.2b。
- [x] 2.2b-2 重跑 `svg_canvas.BASELINE_RATIO`（現值 0.65 量自正黑體的 ascent
      比例；Noto 的 ascent 不同，換字型後要重掃）。
      ⚠ **字型檔本身**：`NotoSansTC-VF.ttf` 必須隨 Installer 佈到使用者機器
      （不能假設 Windows 內建——實測宣告 `Noto Sans TC` 而機器上沒有時會
      fallback，量測與產出一起錯）。
      **重量**由字型推導的常數——`LS_RENDER`（現 1.40，量自正黑體 16pt）、
      `MIN_CHART_PT` 9.0／`MIN_CHART_PT_MULTI` 12.0。
      🔴 **2026-08-13 改：重量在 Windows 執行**（design 4c-1a）——原條文寫
      「必須在 Linux」，那是產線為 Linux 容器時的要求；Windows-only 後
      開發機即產線環境。⚠ 組版字級不變（標題 24／內文 16）。
- [ ] 2.2c 🔴 **圖表字達 14pt**（design 4d，使用者定案的目標字級）：
      依關係式 `投影片pt = SVG字級px × 316.8 ÷ SVG高度px` 反推——現況
      8.5–13.3pt **不達標**，需在本步定圖區幾何與 SVG 高度
      （fit 放 18px 時高度須 ≤407px，現 560）。
      同步收斂 `fit_render_charts.BASE_FONT`（現寫死 15.1）改**從
      `chart_sizing` 讀取**＋一致性測試——否則改後端字級會無聲算錯倍率。
      驗收判準：五頁型單圖頁與雙圖頁圖內字**實測 ≥14pt**（雙圖頁若不可行，
      列出並交使用者裁決，不得默默降標）
- [ ] 2.3 🔴 映射校驗（一次性，開發機）＋解析度定值：
      ①**五頁型三方對照**——Chromium 截 SVG vs PowerPoint COM 轉圖 vs 實機開檔，
      **以 Noto Sans TC 為準**，證據入 `output/_verify/`。這是 B 案的地基：
      截的是 SVG、交付的是 pptx，中間隔著窄轉換器，不校驗就可能
      「SVG 看起來對、pptx 開起來不對」。成立後固定，日後只有改轉換器才重跑。
      ②`regression.py` 基準改比 **SVG 截圖**並重建（⚠ 舊基準算自正黑體，不得沿用）。
      ③🆕 **定出目視截圖解析度**（design 4-0c）：造一頁**刻意植入行首標點**的樣本，
      由低到高試，取「CLI 能穩定指出該問題」的**最小值**。
      ⚠ 判準是**抓得到**，不是「看起來比較清楚」——後者無法收斂。
      ⚠ 不是越大越好：目視迴圈每輪都要讀一整份，CLI 讀圖有 token 成本。
      定出的值寫進 `deck_layout`（唯一定義處，見 2.2f），不寫進 runner 或規格。
- [ ] 2.4 封面素材：runner 注入 workspace 名稱作封面技術名稱
      （version_meta→workspace 名；全庫退回報表標題）

## 3. TDD：runner 與回存

🔴 **CLI 測試分兩層**（2026-08-13 使用者定案「到時候伺服器是接 CLI，所以測試時
也要用這台的 CLI」，見 design 4-0b）：單元測試注入 fake `cli_runner`
（沿現有七支 runner 的既有模式，不燒 token）；**組合驗收與 E2E 用真 CLI 實跑**。
⚠ fake 驗得了編排與契約，驗不了「CLI 起不起得來、認證過不過、輸出形狀對不對」
——那三件正是部署會踩的。單元測試全綠不代表產線跑得動。

- [ ] 3.1 Red：runner 編排契約（機械步順序、任一步非零即 failed 短路、
      **目視迴圈**：CLI 逐頁檢視→修 content.json→重組版重截圖，
      上限＝`DECK_VISUAL_LOOP_MAX_ROUNDS`（產線參數，預設 4；每輪落紀錄），
      閘門紅走同一迴圈）、manifest 形狀（based_on_version／相對 key／
      SHA-256／閘門摘要）、**content.json 隨產物保存**（`deck/content.json`＋
      manifest 記 hash）、失敗不落 ROOT
- [ ] 3.2 Green：`ai_report_deck_runner`（materialize→機械步→CLI 撰稿
      （帶唯讀 MCP 取證，同 narrative 通道）→閘門→pptx＋逐頁 PNG 落
      `DECK_ARTIFACT_ROOT`＋DB 紀錄）；`AI_JOB_TYPES` 收錄；ai_bridge 派工表
- [ ] 3.3 Red→Green：**「匯出報告」頁**（design §6，非報表種類頁）填入
      「產製簡報」按鈕＋deck 紀錄區（時間／版本／狀態／閘門摘要）＋逐頁預覽＋
      下載 pptx；`JOB_REFRESH_TARGETS['ai:report_deck'] = ['export']`
      （跨層對帳測試會先紅）
- [ ] 3.4 誠實進度：runner 各階段 heartbeat stage（沿 narrative keepalive 模式）

## 3b. TDD：內容架構吸收批次（design §7）

⚠ 全批禁寫條件規則（「什麼情況必須用什麼」）；每項先問落在
機械／判斷／選項／建議形哪一層（§7.0 表）。

- [ ] 3b.1 機械層 Red→Green：來源行（每頁角落 version／report_key）；
      **口徑事實包**（引擎自 `REPORT_DEFINITIONS`＋RPT-003／004＋DB 統計產
      定義原文與數值）；**集中度指標**欄（主題內申請人集中度，邊界值逐點）；
      **專利行動有限動詞表**（佈局／追蹤／迴避設計／細讀比對／暫不投入）
- [ ] 3b.2 閘門 Red→Green：口徑頁**定義逐字＋數值**與事實包相符
      （CLI 改寫定義須紅）；動詞表外之詞須紅；圖形文法 type 在庫內、
      節點數與文字長度在容量內、不撞版
- [ ] 3b.2b IPC/CPC 收頁分工**維持現行**（design 7.9，只補契約測試不改行為）：
      plan_deck 對 `_L\d+$` 同指標多階層列候選＋寫 structure_checklist；
      ⚠ 測試要鎖「**不得**改回機械門檻」（曾用表格列數當代理值、實測全錯）
- [ ] 3b.3 版型：三欄分析帶（選項）；**綜合結論頁**（一主題一列：
      發現機械填｜研發意涵 CLI｜專利行動 CLI，主題層級）；
      roadmap 頁**改版**為「優先序＋判準」（候選池由引擎排序：機會矩陣
      高象限／高相似度／家族缺口；取捨與判準 CLI 判斷，案件層級）
- [ ] 3b.4 圖形文法六型（流程／循環／對比／階層／並列／時間線）確定性渲染；
      **不開自由畫 SVG 後門**
- [ ] 3b.4b 🔴 **申請人年度矩陣改跨度圖**（design 7.8b，報表引擎改動）：
      `chart_runner` 新渲染函式（每列一條起訖跨度條，條末標 `total` 件數）＋
      `report_definitions` 圖型改宣告；**Top 10 與第 11–20 名併成一張**；
      `highlight` 與 `marker`（世代分界）由 content.json 宣告、引擎渲染
      （CLI 不改圖）。⚠ 單一來源：HTML 報表同步變，需一併驗兩端。
      ⚠ **主題演進維持泡泡不動**——測試要鎖住（跨度平均佔全軸 56%，
      改跨度條會糊成等長）。加強留待「引擎算 early/recent＋CLI 宣告標註」，
      不在本批
- [ ] 3b.5 撰稿 prompt：narrative 寫法範式（建議形，不進閘門）；
      口徑頁編排契約（可選、可排、可加註解，不得改定義）

## 4. 組合驗收

- [ ] 4.1 OpenSpec strict、目標測試、範圍回歸（deck／runner／frontend 關鍵字）
- [ ] 4.2 E2E：前端按鈕 → 真版本走完整鏈 → pptx＋逐頁 PNG 落 ROOT、
      DB manifest hash 相符、SSE 自動出現紀錄＋前端逐頁預覽可看；
      失敗路徑（撰稿超時／閘門紅）各演一次
      🔴 **本步一律用這台機器的真 CLI**（design 4-0b；不得以 fake `cli_runner`
      代替）。要驗到的是 fake 驗不到的三件：CLI 起不起得來、認證過不過、
      輸出形狀對不對。目視迴圈也要真的跑一輪以上，證明「CLI 看得懂逐頁 PNG
      並改得動 content.json」——那是簡報品質的依靠，不能只驗編排。
- [ ] 4.3 🔴 系統產 vs 手工產**逐頁對照**（同版本各一份），差異列出交使用者判；
      封面技術名稱＝workspace 名稱實物確認；§7 新頁實物驗（口徑頁定義正確
      且讀得懂、綜合結論頁三欄、改版後 roadmap 頁的判準）；頁數帳 22→23
- [ ] 4.4 揭露未覆蓋；使用者接受後 archive
