# Tasks — remove-ppt-delivery-line

分支：`chore/remove-ppt-delivery-line`（自 `feat/improve-report-catalog` 3db58b5 疊出，
該分支含 HTML 線需要的修復，尚未合主線，故不從 master 開）。

## 1. 解讀契約檔遷移（先搬再刪，解讀線不斷）

- [ ] 1.1 `report-narrative-flow.md`／`data_access.md` → `backend/app/worker/prompts/`；
      `content_standard.md` 節錄解讀相關章節同行搬遷
- [ ] 1.2 `ai_narrative_runner` 讀檔路徑改指新位置；容量改走全域上限
      （移除 `load_narrative_capacity` 對 build_ppt 的動態載入）
- [ ] 1.3 守門測試 `test_no_workspace_content_in_rules` 掃描路徑改指新位置

## 2. 後端移除

- [ ] 2.1 worker：`ai_report_ppt_runner`／`report_planning_runner`／`regeneration_runner`；
      ai_bridge 拔 `ai:report_plan`／`ai:report_ppt`
- [ ] 2.2 reports：`planning_contracts.py`／`chart_bundle.py`
- [ ] 2.3 main.py：PPT 端點與 `ppt_eligible` 標記
- [ ] 2.4 `skills/patent-report-ppt/` 整包刪除
- [ ] 2.5 對應測試刪除；`verify_module` preset 更新；import 完整性掃描零殘留

## 3. 前端（2026-08-10 使用者修正範圍）

- [ ] 3.1 匯出報告區塊**先留**（含按鈕）；只移除**編輯模式**。
      ⚠ 後端 PPT 端點已拔，按「產生 PPT」會得到明確錯誤訊息（不得靜默）——驗收要確認。
- [ ] 3.2 「報表種類」頁加：HTML 匯出入口（產製＋版本＋開啟 HTML）

## 3b. 分群卡收斂（2026-08-11 使用者裁決，`c4d2dd7`）

- [x] 3b.1 主題演進只做技術通道（主題×年泡泡矩陣）；功效雙條移除、無 fallback
- [x] 3b.2 統計表隱藏「技術狀態」欄（rows 保留）；priority 清 status 與死鍵 tech_means
- [x] 3b.3 分群卡定型五變體：統計表（技術/功效）＋機會矩陣（技術/功效）＋主題演進（技術）
      ——PPT 呈現未定，先落 HTML

## 4. openspec 收尾

- [x] 4.1 兩個 PPT change 標註作廢後封存（archive）
- [ ] 4.2 strict validation

## 5. 組合驗收

- [ ] 5.1 範圍回歸
- [ ] 5.2 E2E：前端產製 → 解讀 → 開 HTML 逐卡實物驗收
- [ ] 5.3 使用者接受後 archive 本 change
