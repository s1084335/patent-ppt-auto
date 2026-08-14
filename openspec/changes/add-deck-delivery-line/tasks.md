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
      ③ ✅ **目視截圖解析度＝2×，沿用既有經驗值**（2026-08-14 使用者裁決）。
      原條文要「造樣本由低到高試，取 CLI 能穩定指出的最小值」。實查發現
      **2× 不是待填的暫定值**：SKILL.md 的目視迴圈已明訂「放大到 2× 以上看
      行首有沒有中文標點」，依據是 pitfalls #41「中文標點掉到行首——逐頁目視
      十幾輪都沒看出來」的實際教訓。
      ⚠ 未做實測的代價：**2× 可能不是最小值**（偏大就是白花每輪讀圖成本）。
      要收斂時再測；判準與樣本設計（第二行植入、多點、陰性對照、陽性 3/3
      且陰性 0/3）已備妥。
      ⚠ 值在 deck_layout.VISUAL_SCALE（唯一定義處，見 2.2f）；
      SKILL.md 的「2× 以上」是同一份知識的文件面，改一處要同步另一處。
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
