# 移除 PPT 交付線，交付物改為解讀完成的 HTML 報表

## 背景與動機（2026-08-10 使用者定案）

使用者原話：「先把匯出報告區塊，相關的 skill 和組版程式移除，前端的編輯模式也不用留，
其他前端留著，然後你先能交付給我的是 HTML 的報表，我要解讀完成的」。

r5／r6 兩輪實機驗收顯示 PPT 線的問題集中在**規劃與組版**（頁面錯位、圖檔反查錯、
附錄掉圖、容量丟點），而**報表引擎與解讀線是穩的**（285→287 實跑：35 檔、16/16 解讀、
HTML 嵌入解讀成功）。改以 HTML 報表為交付物，把可靠的那段變成產品主線。

## 已確認決策（2026-08-10 使用者逐題裁決）

1. **規劃線一併移除**：`ai:report_plan`＋四道閘門＋quality report＋scope lock
   （含 Codex B1 成果）——沒有 PPT 就沒有消費者，留著是會漂移的死碼；git 歷史可取回。
2. **skills/patent-report-ppt 整包刪**；解讀線要用的契約檔搬離 skills/
   → `backend/app/worker/prompts/`（flow／content_standard 解讀節錄／data_access）。
3. **HTML 入口併進「報表種類」頁**：產製按鈕＋版本清單＋開啟 HTML
   （順承 2026-08-10「匯出報告與報表種類整合為切換頁」的定案方向）。
4. **兩個 PPT 導向的 openspec change 標註作廢後封存**：
   `enable-goal-driven-readonly-report-planning`、`improve-report-professionalism`。

## 範圍

**移除**：前端匯出報告區塊（預覽工作台、編輯模式、選圖、產生 PPT）；
`skills/patent-report-ppt/` 整包；`build_ppt.py`；worker 的
`ai_report_ppt_runner`／`report_planning_runner`／`regeneration_runner`；
`reports/planning_contracts.py`／`chart_bundle.py`；ai_bridge 的
`ai:report_plan`／`ai:report_ppt` 派工；PPT 端點（`/reports/ppt-layout`、
`/reports/versions/{v}/ppt-files`、`/report-latest/ppt/*`）；`ppt_eligible` 標記；
對應測試與 verify_module preset 接線。

**保留（HTML 主線）**：`report_generate`（chart_runner 出圖＋index.html）→
`ai:narrative`（解讀，容量改走全域上限、不再依 PPT 版面）→ `refresh_index`
（解讀嵌回 HTML）→ 版本 API＋`/report-latest/asset/*`（HTML 與圖檔下載）。

**明確不動**：分群模組、其他 AI 任務（topic_label／patent_note／company_zh_name／
irrelevant_filter／candidate_explanation）、DB schema（舊 job 與 artifacts 留作歷史）。

## 原本要回答的問題現在由誰回答（刪除留痕）

| PPT 線原本回答 | 現在 |
|---|---|
| 簡報頁序與敘事 | HTML 報表 9 卡依固定順序＋各卡解讀 |
| 判讀說明頁 | 各卡母體註記＋`reader_guide` 口徑（仍在 `table_display`） |
| 附錄回查表 | HTML 內主題統計表 variant |

## Acceptance Gate

1. 前端「報表種類」頁可：產製報表 → 看版本清單 → 開啟解讀完成的 HTML。
2. `ai:narrative` 在無 PPT 組版程式的情況下正常跑完（容量走全域上限）。
3. 範圍回歸綠（移除件的測試一併刪除；留下線的測試不受影響）。
4. 全庫無殘留引用（import 完整性掃描：被刪模組零引用）。
5. HTML 實物驗收：逐卡有圖有解讀、無內部欄名、無佔位文字。
