## Why

現有報表與 PPT 已可產出，但母體口徑、兩通道論證、Key Player 深度與敘述品質仍不足以穩定支撐專利情報判讀。既有 16 問題調查已完成，需要轉成可執行、可逐項驗收的 change。

## What Changes

- 統一全系統單位與三層漏斗，封面揭露母體、有效分析與主題數。
- 技術／功效通道分頭呈現，技術×功效矩陣維持否決，不得復活。
- 依「刪除、改版、新增」順序精簡報表，移除不再回答問題的權人報表。
- 強化年度、分類、主題、代表專利、Key Player 與研發方向的證據鏈，作為 goal-driven planner 可選用的可靠內容元件。
- 將敘述改為具名發現卡，讓方向建議建立在使用者選圖與可追溯證據，而非模板句。
- 不再建立一套全域固定頁序、固定 Key Player 三頁或固定 direction slots；動態規劃由 `enable-goal-driven-readonly-report-planning` 承接。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `patent-reporting`：修改報表組合、母體標示、通道內容與敘述契約。
- `report-export`：修改讀圖說明、Key Player 內容元件與 PPT 內容容量契約；不固定整份頁序。

## Scope

報表 registry、cluster analytics、narrative input/output、前端報表呈現、可重用 PPT 內容元件與重產驗收。最大目標、選圖集合與動態 SlidePlan 不在本 change 重複定義。

## Non-goals

- 不恢復市場資料線或痛點板。
- 不建立技術×功效交叉矩陣。
- 不藉此 change 重寫分群核心模型。

## Impact

影響報表數量、圖表資料、AI narrative prompt/version、可用章節元件、既有測試與交付內容；頁序改由 goal-driven SlidePlan 決定。

## Activation

需重跑必要 AI 任務、報表、narratives 與 goal-driven 整份 PPT；若來源資料或 derived 欄位改變，先完成對應 DB refresh。本 change 的報表口徑與內容元件先完成，再供 planner 消費。

## Acceptance Gate

每個功能切片先 TDD，再跑 `verify_module.py`；最終整份報告需程式化全頁掃描、COM 轉圖與實機逐頁檢視，由使用者決定是否 archive。
