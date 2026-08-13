# Tasks — add-deck-delivery-line

分支：`feat/add-deck-delivery-line`。前置：`unify-chart-source` 實作並驗收
（intake 吃版本目錄，**2026-08-12 已完成**）。流程本體（九步演算法、版面、
閘門門檻）零改動。

## 0. 前置（獨立於本 change，先做完再填 deck 面）

- [ ] 0.1 「匯出報告」頁**清空 PPT 線殘骸**（design §6）：`btn-export-ppt`
      （打已移除的 `ai:report_plan`，必定 422）、`ppt-goal-input`、
      `ppt-chart-picker`、`exportPreview` 的 PPT 狀態（`pptFiles`／
      `selectedPptFile`／`pptViewer`／`editMode`／`edits`，全庫 29 處）、
      localStorage 編輯稿孤兒（`EXPORT_EDIT_KEY_PREFIX`）、與報表種類頁重複的
      版本下拉與整份預覽、**`static/vendor/pptx-renderer/` 1.5 MB 資產**。
      ⚠ 頁面與導覽項**保留**（deck 要進駐），清空期間放一句「簡報產製規劃中」。
      驗收＝殘留引用歸零＋前端契約測試綠。

## 1. skill 遷入產品 repo

- [ ] 1.1 `.agents/skills/html-report-to-deck/` → `skills/html-report-to-deck/`；
      SKILL.md 依硬規範重寫兩區（Runbook 零開發機路徑；COM 目視、regression、
      pitfalls 收開發備註）；「非產品線」邊界註記依 2026-08-12 定案改寫留痕
- [ ] 1.2 開發機路徑參數化補完（`regression.py` 的 PPTX_TO_PNG）；
      跑 `check_docs.py`＋`regression.py` 確認遷移零破壞
- [ ] 1.3 中央份刪除；`.agents/context/README.md` 路由與引用更新
- [ ] 1.4 新 `assemble_from_version.py` intake（自 unify-chart-source 移入，
      2026-08-12：它本來就是 deck 第 1 步）——版本目錄／asset 端點 → 既有
      `report.json`＋`charts/` 中間格式；texts←narratives.json、
      tables/patent_ids←report_data rows、notes←encoding_notes＋reader_guide、
      report_meta←version_meta（含 workspace 名稱，見 design 4b）；
      `extract_report.py` 降 HTML fallback

## 2. TDD：B 案組版輸出層（窄轉換器）

- [ ] 2.1 Red：轉換器契約——五頁型元素詞彙（矩形卡／逐行文字／圖片／線）
      SVG→DrawingML 映射、文字逐行定位＋關 wrap、超出詞彙 fail loud
- [ ] 2.2 Green：`deck_layout` 輸出層改組 SVG＋窄轉換器；逐頁截圖產出（目視 PNG）。
      🔴 **Chromium BBox 只取代寬度估算**（`text_w`）——**高度不得改用量測**，
      理由見 design 4c-1：`getBBox` 的 height 跨 OS 差 21% 且無法用係數校正。
      縱向一律由 `row_h`／`LS_RENDER` 推導。
- [ ] 2.2a 🔴 **`overlaps()` 撞版判定改為「橫向量測、縱向推導」**（design 4c-1）：
      `fit_render_charts.overlaps()` 目前 w 與 h 都吃量測值——同一張圖會在
      Windows 判定撞版、Linux 判定沒撞（**兩邊都不報錯**）。改後補測試守住。
- [ ] 2.2b 🔴 **字型收斂＝Noto Sans TC**（design 4c，前置於映射校驗）：
      四處宣告（`deck_layout.FONT`、`chart_runner.SVG_FONT_STYLE` 與三處
      SVG 根元素、HTML 報表頁）收斂為**單一常數唯一落點**。
      ⚠ **字型檔本身**：兩端必須是同一個 `NotoSansTC-VF.ttf`——apt 的
      `fonts-noto-cjk` 叫 `Noto Sans CJK TC`，**名稱對不上會 fallback 到
      DejaVu Sans**（實測）。WSL2 已於 2026-08-13 從 Windows 複製一份到
      `~/.local/share/fonts/`；伺服器部署腳本要納入同一份。
      **重量**由字型推導的常數——`LS_RENDER`（現 1.40，量自正黑體 16pt）、
      `MIN_CHART_PT` 9.0／`MIN_CHART_PT_MULTI` 12.0。
      🔴 **重量必須在 Linux 執行**（產線環境）；拿 Windows 的數字回去用
      等於量測基準搞錯邊。⚠ 組版字級不變（標題 24／內文 16）。
- [ ] 2.2c 🔴 **圖表字達 14pt**（design 4d，使用者定案的目標字級）：
      依關係式 `投影片pt = SVG字級px × 316.8 ÷ SVG高度px` 反推——現況
      8.5–13.3pt **不達標**，需在本步定圖區幾何與 SVG 高度
      （fit 放 18px 時高度須 ≤407px，現 560）。
      同步收斂 `fit_render_charts.BASE_FONT`（現寫死 15.1）改**從
      `chart_sizing` 讀取**＋一致性測試——否則改後端字級會無聲算錯倍率。
      驗收判準：五頁型單圖頁與雙圖頁圖內字**實測 ≥14pt**（雙圖頁若不可行，
      列出並交使用者裁決，不得默默降標）
- [ ] 2.3 🔴 映射校驗（一次性）：五頁型三方對照——
      **WSL2 Chromium 截圖**（產線量測端）vs Windows COM 轉圖 vs 實機開檔，
      **以 Noto Sans TC 為準**，證據入 `output/_verify/`；
      regression 基準改比 SVG 截圖並重建（⚠ 舊基準算自正黑體，不得沿用）。
      ⚠ 截圖與量測**在 Linux 做**（design 4c-1／4c-2）：Windows 的角色只剩
      「提供 Linux 沒有的 PowerPoint COM」，量測端統一才不會把開發機的數字
      當成產線結論。環境已備妥（chromium-1234 兩端對齊、字型同檔）。
- [ ] 2.4 封面素材：runner 注入 workspace 名稱作封面技術名稱
      （version_meta→workspace 名；全庫退回報表標題）

## 3. TDD：runner 與回存

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
- [ ] 4.3 🔴 系統產 vs 手工產**逐頁對照**（同版本各一份），差異列出交使用者判；
      封面技術名稱＝workspace 名稱實物確認；§7 新頁實物驗（口徑頁定義正確
      且讀得懂、綜合結論頁三欄、改版後 roadmap 頁的判準）；頁數帳 22→23
- [ ] 4.4 揭露未覆蓋；使用者接受後 archive
