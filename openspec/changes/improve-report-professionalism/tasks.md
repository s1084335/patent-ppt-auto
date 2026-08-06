## 1. 基準與版面契約

- [ ] 1.1 固定代表性 workspace、report run、資料版本與目前 HTML/PPT 輸出，建立可重現基準
- [ ] 1.2 依「刪除優先、改造其次、新增最後」逐張確認報表 catalog 的保留、改造與淘汰；不建立固定全報表頁序或要求每次全部出頁
- [ ] 1.3 確認單位、分母、時間粒度、申請/公開/核准漏斗、技術/功效雙通道、family ID 與標籤長度契約

## 2. TDD 實作

- [ ] 2.1 Red：為 registry、dataset schema、單位/分母、subclass 門檻、命名發現與 label truncation 新增失敗測試並記錄原因
- [ ] 2.2 Green：最小修改 report definition、transform、renderer 與 narrative input，使資料契約測試通過
- [ ] 2.3 Red：新增 HTML/PPT artifact persistence、零上傳失敗、reader guide／Key Player 可重用內容元件、evidence identity 與雙通道輸出測試；不斷言固定頁碼或固定三頁
- [ ] 2.4 Green：完成必要輸出與持久化，使 job succeeded 對應可讀回 artifact，並讓內容元件可由 goal-driven SlidePlan 消費
- [ ] 2.5 Refactor：測試全綠後移除被取代圖表與重複 narrative/renderer 邏輯

## 3. 驗證與輸出

- [ ] 3.1 執行 report/transform/renderer/narrative 目標測試、相關模組回歸與 `scripts/verify_module.py`
- [ ] 3.2 產生 HTML、goal-driven PPTX、manifest/metadata 與 narratives/evidence artifact，核對檔案存在、選圖／章節、dataset id 與 checksum；不以固定頁數判定成功
- [ ] 3.3 以桌面與行動視窗檢查 HTML，渲染 PPTX 全頁縮圖並檢查截字、重疊、空白圖、圖例與中文字型
- [ ] 3.4 保存前後對照、已知限制與未測項目，由使用者逐項確認內容與視覺品質後才 archive
