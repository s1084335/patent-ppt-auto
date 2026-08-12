# Tasks — add-deck-delivery-line

分支：`feat/add-deck-delivery-line`。前置：`unify-chart-source` 實作並驗收
（intake 吃版本目錄）。流程本體（九步演算法、版面、閘門門檻）零改動。

## 1. skill 遷入產品 repo

- [ ] 1.1 `.agents/skills/html-report-to-deck/` → `skills/html-report-to-deck/`；
      SKILL.md 依硬規範重寫兩區（Runbook 零開發機路徑；COM 目視、regression、
      pitfalls 收開發備註）；「非產品線」邊界註記依 2026-08-12 定案改寫留痕
- [ ] 1.2 開發機路徑參數化補完（`regression.py` 的 PPTX_TO_PNG）；
      跑 `check_docs.py`＋`regression.py` 確認遷移零破壞
- [ ] 1.3 中央份刪除；`.agents/context/README.md` 路由與引用更新

## 2. TDD：B 案組版輸出層（窄轉換器）

- [ ] 2.1 Red：轉換器契約——五頁型元素詞彙（矩形卡／逐行文字／圖片／線）
      SVG→DrawingML 映射、文字逐行定位＋關 wrap、超出詞彙 fail loud
- [ ] 2.2 Green：`deck_layout` 輸出層改組 SVG＋窄轉換器；Chromium BBox 量測
      取代 `text_h()` 估算；逐頁截圖產出（目視 PNG）
- [ ] 2.3 🔴 映射校驗（Windows 開發機、一次性）：五頁型
      「Chromium 截圖 vs COM 轉圖 vs 實機開檔」三方對照，證據入
      `output/_verify/`；regression 基準改比 SVG 截圖並重建
- [ ] 2.4 封面素材：runner 注入 workspace 名稱作封面技術名稱
      （version_meta→workspace 名；全庫退回報表標題）

## 3. TDD：runner 與回存

- [ ] 3.1 Red：runner 編排契約（機械步順序、任一步非零即 failed 短路、
      閘門紅回饋 CLI 重撰稿一次仍紅即 failed）、manifest 形狀
      （based_on_version／相對 key／SHA-256／閘門摘要）、失敗不落 ROOT
- [ ] 3.2 Green：`ai_report_deck_runner`（materialize→機械步→CLI 撰稿
      （帶唯讀 MCP 取證，同 narrative 通道）→閘門→pptx＋逐頁 PNG 落
      `DECK_ARTIFACT_ROOT`＋DB 紀錄）；`AI_JOB_TYPES` 收錄；ai_bridge 派工表
- [ ] 3.3 Red→Green：前端「產製簡報」按鈕＋deck 紀錄區（含逐頁預覽）＋
      `JOB_REFRESH_TARGETS['ai:report_deck']`（跨層對帳測試會先紅）
- [ ] 3.4 誠實進度：runner 各階段 heartbeat stage（沿 narrative keepalive 模式）

## 4. 組合驗收

- [ ] 4.1 OpenSpec strict、目標測試、範圍回歸（deck／runner／frontend 關鍵字）
- [ ] 4.2 E2E：前端按鈕 → 真版本走完整鏈 → pptx＋逐頁 PNG 落 ROOT、
      DB manifest hash 相符、SSE 自動出現紀錄＋前端逐頁預覽可看；
      失敗路徑（撰稿超時／閘門紅）各演一次
- [ ] 4.3 🔴 系統產 vs 手工產**逐頁對照**（同版本各一份），差異列出交使用者判；
      封面技術名稱＝workspace 名稱實物確認
- [ ] 4.4 揭露未覆蓋；使用者接受後 archive
