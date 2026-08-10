## 1. 基準與 revision 契約

- [ ] 1.1 固定代表性 workspace/report version/PPTX，保存現有 artifact、preview、approval overrides 與全頁 PNG 基準
- [ ] 1.2 定義 original/draft/candidate/approved revision schema、`plan_id + slide_id`、ETag/optimistic lock、latest/pin 與 retention 關係
- [ ] 1.3 建立動態 slide／content block identity、單頁 AI scope、選圖/evidence 不變、非目標 slide 不變與 artifact 命名決策表；不再依固定頁碼或全域 slot list

## 2. TDD：歷史與編輯持久化

- [ ] 2.1 Red：新增 workspace/version 隔離、舊 artifact revision-0、歷史狀態與缺漏提示測試
- [ ] 2.2 Green：完成最小 revision persistence/API 與匯出歷史讀取，不改既有整份 PPT 主線
- [ ] 2.3 Red：新增草稿 save/readback、selected chart/evidence/AI 原文不可變、跨程序、conflict 與無效 slide/block identity 測試
- [ ] 2.4 Green：完成 draft/approved override 持久化及前端保存／重整讀回

## 3. TDD：HTML theme 與單頁候選

- [ ] 3.1 Red：新增 producer theme token 與 HTML/PPT consumer 一致性測試
- [ ] 3.2 Green：讓單頁 HTML 消費共用 theme/structure，移除其第三套硬編樣式
- [ ] 3.3 Red：新增單頁 payload scope、原選圖/evidence refs 全保留、未選圖拒絕、candidate 不改 latest、取消不變、核准 conflict、前一版追溯及非目標 slide diff 測試
- [ ] 3.4 Green：完成 candidate create/compare/cancel/approve 與完整 deterministic PPTX rebuild
- [ ] 3.5 Refactor：全綠後收斂 revision/manifest/theme 共用邏輯，不恢復 CSS 模擬或自由拖曳

## 4. 實物驗收

- [ ] 4.1 執行 report/export/AI runner/PPT builder/frontend 目標測試、回歸與 `scripts/verify_module.py`
- [ ] 4.2 以真實 goal-driven 報告做保存、重整、候選、取消、核准與歷史回復；產出原版/候選/核准 PPTX、HTML、plan/evidence manifest/hash
- [ ] 4.3 使用 `D:\vscode\ppt-tools\pptx_to_png.py` 轉出全部頁面並程式化／人工確認非目標頁不變、指定頁無截字重疊
- [ ] 4.4 揭露未驗 layout/browser/DB 項目並交使用者逐頁驗收；明確接受前不得 archive
