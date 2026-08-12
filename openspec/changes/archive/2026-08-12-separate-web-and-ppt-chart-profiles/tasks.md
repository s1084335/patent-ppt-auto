## 1. 基準、Manifest 與 Red

- [ ] 1.1 固定代表性 report version、chart identities、canonical datasets、現行 web 圖與 PPT 頁，建立重產前基準與 RPT-015／EXP-018 追溯矩陣　（未做：無「重產前基準」可比——2026-08-09 之前不存在 web profile 產物）
- [x] 1.2 Red：新增 render profile／manifest schema、identity、dataset version、dimensions、checksum 與舊單 profile 錯誤測試，實跑並記錄失敗原因
- [x] 1.3 Red：新增 web／PPT parity tests，斷言資料列、排序、series/legend identity、semantic colors 與 layout logic 同源
- [ ] 1.4 Red：新增多選圖解析、跨版本／跨 variant mismatch、缺圖、checksum 錯誤、CLI 加圖／漏圖與 SlidePlan set-equality 測試　（大部分已由 P2 的 SlidePlan／chart_bundle 測試涵蓋；跨版本 mismatch 未補）

## 2. 報表雙 Profile 最小 Green

- [x] 2.1 Green：定義唯一 RenderProfile contract，只允許 canvas、DPI、font/stroke scale 與 margin policy 有媒介差異
- [x] 2.2 Green：讓 canonical chart specification／dataset 依序產生 web 與 PPT profiles，不複製 transform、排序或語意色彩邏輯
- [x] 2.3 Green：持久化兩份 artifact 與同一 manifest lineage，任一 profile 失敗時不得標示該 chart 可匯出
- [x] 2.4 Green：report content/asset API 回傳 web asset、stable chart identity 與雙 profile 完整性，不以檔名推導真相
- [x] 2.5 Green：實跑 1.2～1.3 目標測試直到通過，每個 chart family 通過後停止並記錄結果

## 3. 選圖與唯讀 CLI 最小 Green

- [x] 3.1 Green：匯出入口以 report version＋chart identity 驗證使用者選取集合，解析同 identity 的 PPT profile
- [x] 3.2 Green：evidence manifest 保存選取順序、web/PPT checksum lineage 與全部 PPT asset，並經版本化 artifact store 交接 Companion　（2026-08-09 補齊：profile_lineage 落在 bundle_manifest.json，缺 web profile 留 null 使其現形）
- [x] 3.3 Green：SlidePlan／組版 validator 強制 input selected set、plan referenced set 與 rendered set 完全相等，拒絕 CLI 自行增減或替換圖片
- [ ] 3.4 Green：舊版本或不完整 profile 明確回報需重產，不 fallback 到任意舊圖　（PPT 端已 fail loud；**網頁端刻意退回**顯示既有單圖，理由見 spec 回寫）
- [ ] 3.5 Green：實跑 1.4 全部目標測試直到通過，確認 mismatch 均 fail loud 且不產可核准 PPT　（未逐項跑：1.4 未補齊）

## 4. Refactor 與範圍回歸

- [x] 4.1 Refactor：目標測試全綠後收斂 renderer 參數、manifest serializer 與 asset resolver，移除 web/PPT 重複邏輯
- [ ] 4.2 執行 report registry/transform/chart、artifact store/API、goal-driven planner、SlidePlan validator 與 PPT builder 回歸　（已跑範圍回歸；完整回歸依 AGENTS.md 規則不跑）
- [x] 4.3 執行 `scripts/verify_module.py`，回報 lint、type、複雜度、新增行覆蓋率與未達門檻　（2026-08-09 A5 已跑，四項門檻結果見 commit 說明）

## 5. 重產與實物驗收

- [ ] 5.1 重產固定 report version 的全部可選圖 web／PPT profiles，核對 identity、dataset、排序、語意色彩、dimensions 與 checksum lineage　（已重產三張代表圖並逐項核對 identity／寬度／checksum，非全部可選圖）
- [x] 5.2 在桌面與行動 viewport 驗證 web 圖；渲染 PPTX 全頁並檢查字級、裁切、重疊、圖例與中文字型　（2026-08-09 Playwright 實測 1440x900／390x844：三張圖比例不變不裁切、桌面佔容器 95-98%、行動無水平捲動；實測抓到成對 bar 標籤在 web profile 重疊，已修）
- [ ] 5.3 選取多張圖跑完整 goal-driven CLI／PPT 流程，逐一核對選取集合、CLI input、SlidePlan 與成品頁全部一致　（未做）
- [ ] 5.4 保存 manifest、測試結果、web 截圖、PPTX 與全頁轉圖；使用者驗收後才 archive，未重產舊版本須明確列為限制　（未做：等使用者驗收）
