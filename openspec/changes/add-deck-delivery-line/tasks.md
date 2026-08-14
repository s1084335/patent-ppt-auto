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
- [x] 1.3 中央份刪除；`.agents/context/README.md` 路由與引用更新
      **2026-08-14 完成**：排除 `__pycache__` 後確認中央份無獨有檔才刪；
      README 路由改指產品 repo，舊註記「不代表 PPT 回到交付線」標明已由
      本 change 推翻。力山 repo 變更未代提交（與使用者其他未提交變更並列）。
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
- [x] 2.1b **圖表原生繪製——前半完成，後半停用**（2026-08-14 使用者裁決）
      ✅ **前半（轉換器詞彙）已完成**：34 測試綠，14 張真實圖表元素對帳全過。
      ⏸ **後半（把圖表 SVG 內聯進頁面 SVG）不做**。圖表維持原本的
      it_render_charts → PNG → chart_stack 貼圖。
      理由：原生繪製的三個理由只剩一個成立——字級不受控被實測推翻
      （11/14 已達 14pt）、配色不成立、「可被程式檢查」目前無消費者
      （deepen-deck-evidence-layer 把相關檢查都判給目視層）。
      ⚠ 停在這裡沒有回退成本：轉換器是**新增的未啟用能力**，圖表路徑從未被改過。
      原條文（供日後要啟用時參考）：
- [ ] ~~2.1b 原條文~~ 🆕 **圖表原生繪製**（design 4-0d，2026-08-13 使用者裁決）：
      窄轉換器詞彙擴充。

      🔴 **2026-08-14 完整掃描推翻「只多這四種」**——上次只掃元素標籤沒掃
      屬性值形式。重掃 14 張真實圖表 SVG（元素＋屬性＋**值的形式**）：

      **元素**（4 種新增）：
      | SVG | 次數 | → pptx |
      |---|---|---|
      | `<circle>` | 108 | `MSO_SHAPE.OVAL` |
      | `<line>` | 19 | ✅ **全部水平／垂直，0 條斜線** → 照既有原則走細 rect 退化形，**不需 `add_connector`** |
      | `<polyline>` | 2 | `build_freeform()` |
      | `<defs>`＋`<pattern>` | 1 | 🔴 見下方 hatch |

      **🔴 五個屬性值形式缺口**（每一個都會讓轉換器當場炸或靜默畫錯）：
      | # | 形式 | 次數 | 處置 |
      |---|---|---|---|
      | 1 | `width="100%" height="100%"` | 13/14 張 | 相對 viewBox 解析；`_px()` 只吃絕對 px，**第一張就炸** |
      | 2 | `fill="white"` | 14 | 顏色**關鍵字**；`_color()` 只收 `#RRGGBB`，會 raise |
      | 3 | `fill="url(#hatch)"` | 3 | 🔴 pattern 引用，**不可退化**，見下 |
      | 4 | `transform="rotate(...)"` | 2（`<text>`）＋1（`patternTransform`） | `shape.rotation` |
      | 5 | `stroke-dasharray="6 4"` | 2 | 虛線 |

      **其餘要一起補的屬性**：`text-anchor`（**198 次**：middle 152／end 46
      ——不做則所有置中與右對齊文字全部偏移）、`fill-opacity` 22、
      `stroke-opacity` 1、`font-style="italic"` 1、`rx` 66、`stroke-width` 79。

      🔴 **hatch 不可退化成純色**：實查 `chart_runner.py:826` ——
      「顏色分段＝申請結構（solo/joint），**斜紋疊加＝已轉讓**」，
      是**第二個視覺通道**承載獨立資訊。退化就讓「已轉讓」消失，
      而且**不會有任何東西報錯**（缺席型失敗）。改用 `fill.patterned()`
      取最接近的 45° MSO pattern。

      ✅ **`<style>` 只有字型宣告**（`text{font-family:...}`，8 張都是），
      沒有 fill／stroke／class 規則 → CSS 不影響幾何與顏色，**窄轉換器仍然窄**。
      ⚠ 但因此 `font-family` 是**繼承來的**（`<style>` 或 `<svg>` 根元素 6 次），
      不是每個 `<text>` 都有屬性——現行 `_add_text` 只讀 `el.get("font-family")`
      會拿不到，字型靜默變成 pptx 預設。

      ⚠ `<title>` 65 個是 tooltip（`<circle>`／`<rect>` 的子元素）、
      `data-*`（`data-value-band`／`data-cell`／`data-topic`／`data-on-fill`
      ／`pointer-events`）是 HTML 互動用 → 明確忽略並補測試，不得靜默略過。
      🔴 圖表文字改原生後字型走 `chart_sizing.FONT_FAMILY`，不得從 SVG 的
      `<style>` 另抓一份。
      ⚠ 未知屬性維持 fail loud，`data-*`（HTML 互動用）要明確決定轉換或忽略並補測試。
      🔴 **屬性值形式缺口**：14 張裡 **13 張**用 `width=\"100%\" height=\"100%\"`，
      而 `_emu()` 只吃絕對 px——**第一張就會炸**。轉換器要加百分比解析
      （相對於 viewBox）。⚠ 先前只掃元素標籤沒掃屬性值形式，漏了這條。
      🔴 **色彩照搬**：白底 rect 也畫成白色 shape，視覺與現行 PNG 完全相同
      （design 4-0d）。⚠ 不趁機改配色——視覺不變才能用現行 PNG 做像素比對。
      🔴 **驗證分三層**（design 4-0d）：A 元素對帳（SVG 有 N 個就要有 N 個 shape）、
      B 幾何對帳（座標尺寸逐一比，容忍 1 EMU）、C 數值正確**屬引擎不屬轉換器**。
      ⚠ 轉換器的錯是**靜默**的——長條少一根、短 5%、標籤配錯，看起來都合理，
      目視抓不到，必須機械對帳。
      ⚠ 未驗：`build_freeform`／`fill.patterned()` 在 python-pptx 1.0.2 的行為；
      viewBox 非 1:1 時的座標換算（1120×837 與 949×460 兩種尺寸並存）；
      原生化後 pptx 體積與開檔速度；`fit_render_charts` 的角色重新定義。
      ✅ `add_connector` 已消除（`<line>` 全為水平／垂直）。
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
      ⚠ 沒在 deck worktree 跑 `uv sync`：它沒有自己的 `.venv`，sync 會從頭建
      含 torch 數 GB；實際在用的是主工作樹的 venv，而 `pyproject` 改動還在本
      分支上——**合主線後主工作樹 `uv sync` 才會自然涵蓋**。
      驗證：`playwright.__file__` 指向專案 venv、回退不再啟用、
      `shoot_pages`／`fit_render_charts` 實跑正常（browsers 沿用既有，未重下載）。
      ⚠ 瀏覽器本體不進 `pyproject`（150–400 MB），另裝或用環境變數指。
- [x] 2.2g 🆕 **斷行要保護不可分割詞組**（2026-08-13 逐頁目視 slide05 抓到）：
      標籤欄頁第二行結尾是孤立的「CN」，號碼「223248696）。」被推到第三行
      ——**專利號從中間被拆開**。
      ⚠ 那張是 pptx 路徑（PowerPoint 斷的），所以**不是 B 案引入的**；但 B 案把
      斷行收回引擎後，`wrap_lines` 只看字寬，會照樣拆。避頭尾只處理標點禁則，
      沒處理詞組。
      🔴 這正是「只有目視看得到」的一類：程式化檢查全綠、SVG 合法、字寬也沒超，
      但讀者會看到孤立的「CN」。**它也證明了逐頁目視不可抽樣**——我原本打算
      只看兩頁就過。
      判準：空格兩側都非 CJK、且**至少一側含數字**時視為不可分割
      （`CN 223248696`／`A63B 069/18` 保護；`11 件`／`2020 年` 不保護，
      因為 CJK 側本來就可斷；`the quick` 不保護）。
      ⚠ 詞組本身超過行寬時仍須斷（fail open），否則無限迴圈。
      驗收：補測試掃多種寬度；`regression` 重跑並**八頁全看**。
      **2026-08-13 完成**：判準＝空格兩側都是 ASCII 英數且至少一側含數字。
      實作踩三個坑（都記進註解）：
      ①詞組邊界不能用「空白」為界——中英文之間沒空格，會把整串中文吃進來而
        誤判成含 CJK、於是不保護
      ②只記「受保護的空格索引」會漏算左側詞，斷點退到空格之前，詞組照樣被拆成
        「US」＋「 12345678」→ 改回傳完整詞組範圍
      ③🔴 `_apply_kinsoku` 為了把行首的「）」推回上一行，從行尾借一個字，
        正好切進號碼裡。⚠ 兩條規則本身不衝突，衝突的是「只借一個字」這個手段
        → 改推**整個詞組**，兩者同時滿足
      順帶收掉兩個第二落點：`_apply_kinsoku` 內另有一段重切邏輯（沒有詞組保護）
      抽成 `_greedy_wrap` 共用；`est_lines` 從獨立數學式改為委派
      `len(wrap_lines)`——加入保護後兩者會分岔（保護讓某些行提早斷，實際多一行），
      那就是估高一套、排版另一套。
      ⚠ 因此 `test_line_count_matches_est_lines` 變成結構保證而非數值驗證
      （自己跟自己比，pitfalls #39），已在 docstring 註明並新增
      `test_est_lines_delegates` 用原始碼守住。
      驗證：69 支綠、regression 8 頁逐像素相同、用 slide05 真實文字確認
      兩條規則同時滿足（第 2 行結束在頓號、`CN 223248696` 完整移到第 3 行）。
- [x] 2.2e 🆕 **CLI 執行身分實測**（design 4-0b 唯一剩下的 🔴 條件）：
      `_CLI_SPECS` 的 `"binary": "claude"` 靠 PATH 解析，實測開發機的 CLI 在
      使用者 profile 底下（`C:\Users\user\.local\bin\claude.exe`，2.1.217），
      服務身分的 PATH 通常不含該目錄、認證也綁 profile。
      以 Companion 實際的執行身分跑一次真 CLI，確認起得來且認證過；
      解不到則改為絕對路徑設定或指定服務執行身分，結果寫回 4-0b。
      🔴 **驗收＝實跑 CLI 成功**，不是「確認二進位存在」。
      **2026-08-13 完成——結論是這個條件不成立，我原本的前提就錯了。**
      Companion 根本不以服務身分執行：`scripts/companion_startup_install.ps1`
      用**啟動資料夾捷徑**，檔頭明寫「純使用者層級機制，不需任何提權，且同樣以
      登入使用者身分執行——**這正是 Companion 需要的前提（要拿得到使用者自己的
      Claude CLI 登入 token）**」。排程器那條路**已試過並否決**：「以 LogonType
      Interactive 啟動時實測 LastTaskResult=1（啟動即失敗），改 S4U 需管理員權限」。
      ⚠ 也就是說「CLI 拿得到 token」是該啟動方式的**設計目的**，不是巧合——
      我沒讀那份腳本就先寫下擔憂，是憑推論寫規格。
      實跑驗證：`build_cli_command` ＋ `run_cli` → exit=0、11.8 秒、回覆正確、
      空白名單（`--allowedTools ''`）生效。
      ⚠ **部署時仍要重驗**：架站方若改用服務或容器啟動 Companion，整段前提翻掉，
      而症狀不會是明顯錯誤（可能只是認證失敗或空回應）。已寫回 design 4-0b。
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
- [x] ~~2.2c 圖表字達 14pt（依 SVG 高度反推圖區幾何）~~
      🔴 **2026-08-13 改寫：原生繪製後這題的前提消失**（design 4-0d）。
      原條文要靠「定圖區幾何與 SVG 高度」把縮放後的字級推到 14pt，那建立在
      「圖被當成點陣圖等比縮放」上。圖表改原生繪製後，圖表文字**就是投影片的
      原生文字**，字級直接指定——關係式
      `投影片pt = SVG字級px × 圖區高in × 72 ÷ SVG高px` 整條消失。

      ✅ **2026-08-14 實測：本題不存在。** 產出 SVG 字級 20.8–30.5px，
      投影片 **11/14 達 14pt、最低 11.54pt**，`MIN_CHART_PT = 9.0` 從未觸發。
      ⚠ 舊記錄「達 14pt 1/14」係誤用 `BASE_FONT = 15.1`（fit 的**輸入**）
      當產出字級，已刪除。

      **保留的部分**：`fit_render_charts.BASE_FONT`（現寫死 15.1）仍應改為從
      `chart_sizing` 讀取＋一致性測試——原生繪製後 `fit` 的角色雖然縮到只剩
      目視用 PNG，那個寫死值仍是第二落點。
- [x] 2.3 🔴 映射校驗（一次性，開發機）＋解析度定值：
      **①②③ 全數完成（2026-08-14）**：
      ① 14 頁真實素材全數程式化網格掃＋逐類人工檢視，證據
      `output/_verify/mapping/RECORD.md`。無結構缺陷；差異兩類＝光柵化粗細
      （非缺陷）＋**長句換行點差一字**（SVG＝引擎斷行、pptx＝PowerPoint 斷行；
      內容逐字同、方向安全——引擎估行保守，pptx 不會溢出）。
      🔴 誠實揭露：pptx 交付物現由 python-pptx **原生組版**而非窄轉換器——
      「PPTX 零重排」只在轉換器路徑成立；接上（make_deck 改走 svg_to_pptx）
      屬後續 change。⚠ 首輪 COM 檔名字典序配對錯誤（投影片1、10、11…）已修
      數字排序——regression 以 mtime 防過同一坑，第二次踩。
      ② 基準改比 SVG 截圖並重建（1c37ac4）：8 頁全數目視，
      抓到**英文單字被從中間拆開**的真缺陷並修（_greedy_wrap＋_apply_kinsoku）。
      原條文：①~~**五頁型三方對照**~~——Chromium 截 SVG vs PowerPoint COM 轉圖 vs 實機開檔，
      **以 Noto Sans TC 為準**，證據入 `output/_verify/`。這是 B 案的地基：
      截的是 SVG、交付的是 pptx，中間隔著窄轉換器，不校驗就可能
      「SVG 看起來對、pptx 開起來不對」。成立後固定，日後只有改轉換器才重跑。
      ②`regression.py` 基準改比 **SVG 截圖**並重建（⚠ 舊基準算自正黑體，不得沿用）。
      ③ ✅ **目視截圖解析度＝2×，沿用既有經驗值**（2026-08-14 使用者裁決）。
      實查 2× 不是待填暫定值：SKILL.md 目視迴圈已明訂「放大到 2× 以上看行首
      標點」，依據 pitfalls #41 的實際教訓。⚠ 未實測的代價＝2× 可能偏大
      （白花每輪讀圖成本）；要收斂再測，樣本設計（第二行植入、多點、陰性對照、
      陽性 3/3 陰性 0/3）已備妥。值在 `deck_layout.VISUAL_SCALE`（唯一定義處），
      SKILL.md「2× 以上」是同一知識的文件面，改一處同步另一處。
- [x] 2.4 封面素材：runner 注入 workspace 名稱作封面技術名稱
      （version_meta→workspace 名；全庫退回報表標題）
      **2026-08-14 完成**：資料鏈＝intake 已把 version_meta 的 workspace_name
      解進 report.json 的 report_meta（唯一定義處）；runner 只消費——
      _cover_tech_name 讀 report_meta 注入撰稿 prompt（workspace 名缺時
      退 h1＝報表標題）。單元測試＋半真跑（真素材出「滑雪機」）各驗一次。

## 3. TDD：runner 與回存

🔴 **CLI 測試分兩層**（2026-08-13 使用者定案「到時候伺服器是接 CLI，所以測試時
也要用這台的 CLI」，見 design 4-0b）：單元測試注入 fake `cli_runner`
（沿現有七支 runner 的既有模式，不燒 token）；**組合驗收與 E2E 用真 CLI 實跑**。
⚠ fake 驗得了編排與契約，驗不了「CLI 起不起得來、認證過不過、輸出形狀對不對」
——那三件正是部署會踩的。單元測試全綠不代表產線跑得動。

- [x] 3.1 Red：runner 編排契約（機械步順序、任一步非零即 failed 短路、
      **目視迴圈**：CLI 逐頁檢視→修 content.json→重組版重截圖，
      上限＝DECK_VISUAL_LOOP_MAX_ROUNDS（產線參數，預設 4；每輪落紀錄），
      閘門紅走同一迴圈）、manifest 形狀（based_on_version／相對 key／
      SHA-256／閘門摘要）、**content.json 隨產物保存**（deck/content.json＋
      manifest 記 hash）、失敗不落 ROOT
      **2026-08-14 完成**：	est_report_deck_runner.py 14 支——步序、chip 條件步、
      短路、目視迴圈（check 紅／make 紅走同一迴圈、audit 硬失敗、停滯即失敗、
      達上限附最後發現）、回存與 manifest、封面注入、進度單調。
      ⚠ 補強：CLI 說有問題卻沒改 content.json＝**停滯，立即失敗**——
      不許空轉燒到上限（原條文未寫，實作時補）。
- [x] 3.2 Green：i_report_deck_runner（materialize→機械步→CLI 撰稿
      （帶唯讀 MCP 取證，同 narrative 通道）→閘門→pptx＋逐頁 PNG 落
      DECK_ARTIFACT_ROOT＋DB 紀錄）；AI_JOB_TYPES 收錄；ai_bridge 派工表
      **2026-08-14 完成**：ackend/app/worker/ai_report_deck_runner.py。
      resolve_run_dir 沿 narrative 同函式（本機優先、DB 落地補位）；
      機械步走專案 interpreter 子行程（2.2d）；撰稿與目視同 RESEARCH_TOOLS
      權限面；make_deck.py 加可選第 4 參數輸出頁面 SVG（build_svg 共用
      _compose，同一份版面）。
      🔴 **半真跑抓到 fake 測不到的洞**：runner 假設 make_deck 吃第 4 參數，
      當時腳本只吃 3 個——SVG 從沒產出，單元測試因 fake 假裝有而全綠。
      	est_deck_runner_semireal.py 以真素材（滑雪機 14 頁）真 subprocess
      全鏈 18 秒通過，作為 regression 常駐（素材不在時 skip）。
      ⚠ 半真跑驗不了 CLI 撰稿品質與目視判斷——4.2 用真 CLI。
- [x] 3.3 Red→Green：**「匯出報告」頁**（design §6，非報表種類頁）填入
      「產製簡報」按鈕＋deck 紀錄區（時間／版本／狀態／閘門摘要）＋逐頁預覽＋
      下載 pptx；JOB_REFRESH_TARGETS[\'ai:report_deck\'] = [\'export\']
      （跨層對帳測試會先紅）
      **2026-08-14 完成**：後端 pi/deck_exports.py 三端點（紀錄清單／
      逐頁 PNG／pptx 下載）＋前端 renderExport 重寫。
      ⚠ 兩處與原條文的差異：①刷新目標值＝[\'deckExports\']（資源名），
      navs 才是 [\'export\']——原條文把頁名寫進了資源位；
      ②pptx 下載前驗 SHA-256 與 manifest 相符，不符回 409 拒供
      （靜默供出被動過的檔比 404 危險）。供檔路徑一律 manifest key＋
      escape 檢查（沿 artifacts.py 前例）。
- [x] 3.4 誠實進度：runner 各階段 heartbeat stage（沿 narrative keepalive 模式）
      **2026-08-14 完成**：素材 5 →機械步 10–25 → CLI 撰稿 30–55 →
      目視第 n 輪 60+（輪次進文字）→回存 92–97 → 100；階段文字繁中如實，
      百分比單調遞增（測試鎖住）。
## 3b. TDD：內容架構吸收批次（design §7）

⚠ 全批禁寫條件規則（「什麼情況必須用什麼」）；每項先問落在
機械／判斷／選項／建議形哪一層（§7.0 表）。

- [x] 3b.1 機械層 Red→Green：來源行（每頁角落 version／report_key）；
      **2026-08-14 完成**（db89404）：make_deck 自 report.json 蓋章、每頁角落渲染；CLI 不參與。
      **口徑事實包**（引擎自 `REPORT_DEFINITIONS`＋RPT-003／004＋DB 統計產
      定義原文與數值）；**集中度指標**欄（主題內申請人集中度，邊界值逐點）；
      **專利行動有限動詞表**（佈局／追蹤／迴避設計／細讀比對／暫不投入）
- [x] 3b.2 閘門 Red→Green：口徑頁**定義逐字＋數值**與事實包相符
      **2026-08-14 完成**（db89404）：intake 產 caliber_facts.json（population＋reader_guide 權威原文），check_content 逐字閘門；改寫紅、錯數字紅、未引用不報錯。
      （CLI 改寫定義須紅）；動詞表外之詞須紅；圖形文法 type 在庫內、
      節點數與文字長度在容量內、不撞版
- [x] 3b.2b IPC/CPC 收頁分工**維持現行**（design 7.9，只補契約測試不改行為）：
      **2026-08-14 完成**（db89404）：鎖 CLASSIFICATION_MIN_DISTINCT_L4=3 與 plan_deck 收頁判斷含踩坑紀錄，不改行為。
      plan_deck 對 `_L\d+$` 同指標多階層列候選＋寫 structure_checklist；
      ⚠ 測試要鎖「**不得**改回機械門檻」（曾用表格列數當代理值、實測全錯）
- [x] 3b.3 版型：三欄分析帶（選項）；**綜合結論頁**（一主題一列：
      **2026-08-14 完成**（b3d291a）：conclusions 取代建議頁；發現欄機械（topic_facts 逐字）、行動五動詞白名單、集中度分辨（單一申請人=集中持有、各一件=分散待驗）。
      發現機械填｜研發意涵 CLI｜專利行動 CLI，主題層級）；
      roadmap 頁**改版**為「優先序＋判準」（候選池由引擎排序：機會矩陣
      高象限／高相似度／家族缺口；取捨與判準 CLI 判斷，案件層級）
- [x] 3b.4 圖形文法六型（流程／循環／對比／階層／並列／時間線）確定性渲染；
      **2026-08-14 完成**（4ddeef8）：六型渲染＋容量閘門；文法外 fail loud；箭頭用字符維持窄詞彙。
      **不開自由畫 SVG 後門**
- [x] 3b.4b 🔴 **申請人年度矩陣改跨度圖**（design 7.8b，報表引擎改動）：
      **核心由 master 9fd86eb 完成**（跨度圖＋併張＋實有年圓點＋條末總計）；殘項宣告式 highlight/marker 於 8cfbe6b 補完（apply_chart_marks＋runner 每輪套用、接不上資料走修稿輪）。
      `chart_runner` 新渲染函式（每列一條起訖跨度條，條末標 `total` 件數）＋
      `report_definitions` 圖型改宣告；**Top 10 與第 11–20 名併成一張**；
      `highlight` 與 `marker`（世代分界）由 content.json 宣告、引擎渲染
      （CLI 不改圖）。⚠ 單一來源：HTML 報表同步變，需一併驗兩端。
      ⚠ **主題演進維持泡泡不動**——測試要鎖住（跨度平均佔全軸 56%，
      改跨度條會糊成等長）。加強留待「引擎算 early/recent＋CLI 宣告標註」，
      不在本批
- [x] 3b.5 撰稿 prompt：narrative 寫法範式（建議形，不進閘門）；
      **2026-08-14 完成**（1c37ac4）：narrative.md 增補四個新元件的寫法建議，全部建議形不進閘門；runner prompt 已指向該檔。
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
