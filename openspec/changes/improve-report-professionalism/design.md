## Context

現有管線已能產 15 種報表與 PPT，但內容仍有母體、單位、通道、敘述與論證鏈落差。既有調查已確認資料多半存在，重點是收斂而非無限制新增。

## Goals / Non-Goals

**Goals:** 讓每頁回答明確問題、所有數字有母體、技術／功效不混用、敘述可核對，並維持可讀版面。

**Non-Goals:** 不恢復市場線／痛點板、不做技術×功效矩陣、不以更多頁數取代內容決策。

## Decisions

1. **刪 > 改版 > 新增。** 現有報表先重評，owner ranking/year matrix 移除後由 Key Player 與申請／受讓證據承接。
2. **單位只留件、群。** 封面三層漏斗合成一格；設計案 `document_kind='S'` 不進分群但要有標籤與備註。
3. **同族口徑使用 `WIPS同族ID`。** 只用於技術多樣性等明確定義，不無條件改所有件數。
4. **技術 label 用機構／手段層；功效 label 用功效主類。** label 單一、summary 可多重；功效模板前綴先剝除。
5. **IPC/CPC 出頁門檻依 4 階 subclass 種類數。** 門檻與缺頁原因進 metadata。
6. **競爭強度收斂三維。** 不重新引入已否決的權利範圍維度。
7. **具名發現卡。** 每則含對象、數據、意義、限制；沒有證據就少產，並可由 goal-driven planner 依最大目標選入對應 slide。
8. **讀圖須知與 Key Player 改為可重用內容元件。** 不再固定讀圖須知位置或 Key Player 三頁；只有使用者選圖與最大目標需要時才由 SlidePlan 安排，容量仍必須測。

## 程式與測試落點

- Registry/SQL/chart：`backend/app/reports/`
- AI narratives：`backend/app/worker/ai_narrative_runner.py`
- UI：`backend/app/static/index.html`
- PPT：`skills/patent-report-ppt/scripts/build_ppt.py`
- Tests：report definitions/parity、population、cluster analytics、narrative contract/capacity、PPT layout/dynamic pages/reader-facing output。

## 輸出契約

`report_data.json` 保存母體、單位、門檻、漏產與主題版本；narratives 依 report/variant key 並可轉為 evidence reference；PPT metadata 由 goal-driven SlidePlan 揭露每頁目的、圖表、來源與缺漏。

## Risks / Trade-offs

- [內容元件與動態 SlidePlan 不相容] → 以穩定 chart/report identity 與 content-shape contract 測試，不再維護固定頁碼錨點。
- [敘述變長造成溢出] → 先濃縮、硬容量守門仍保留。
- [prompt 改善但最終建議仍空泛] → planner 的建議必須引用選圖或具名 evidence，不另用固定 direction slots／模板燃料。
- [舊報表刪除後被再加回] → 在 migration ledger 與 archive design 記錄承接頁。

## Migration Plan

依「口徑／registry → 通道資料 → evidence-ready narrative/content component」順序切片，每片 TDD 且可單獨重產；再交由 `enable-goal-driven-readonly-report-planning` 組成整份報告。使用者驗收後才 archive。
