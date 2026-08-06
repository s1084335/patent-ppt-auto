## Purpose

定義匯入、瀏覽專利與分類區三個主要使用區域的隔離式端到端驗收，要求真 API、真測試資料與瀏覽器畫面共同產生可保存、可重跑且不污染正式資料的證據。

## ADDED Requirements

### Requirement: SAT-001 E2E fixture 與正式資料隔離

驗收 SHALL 使用可識別的測試 DB/workspace/object prefix 與固定小樣本；建立、使用、對帳及待清理資料 MUST 有 manifest。

#### Scenario: 測試目標指向正式資料庫
- **WHEN** E2E preflight 判定 DB/storage 為正式環境或無隔離標記
- **THEN** suite SHALL 拒絕執行任何寫入

### Requirement: SAT-002 匯入區完整流程

驗收 SHALL 從瀏覽器上傳允許格式，觀察上傳與 job 進度、成功／失敗結果、統計及 workspace 歸屬，並與 API/DB 對帳。

#### Scenario: 成功匯入固定樣本
- **WHEN** 測試樣本完成 patent_import
- **THEN** inserted/matched/updated/patent_ids、workspace 成員與 DB 實際資料 SHALL 一致

### Requirement: SAT-003 瀏覽區內容與版面

驗收 SHALL 在桌面與行動 viewport 檢查全庫/workspace 切換、26 欄契約、原文與正規化值、連結、代表圖 lazy load、分頁與水平捲動。

#### Scenario: 最長欄值與小螢幕
- **WHEN** 清單含長標題／長公司名並在行動 viewport 顯示
- **THEN** 文字與控制項不得重疊或被不可達地裁切，水平捲動仍可操作

### Requirement: SAT-004 分類區與治理流程

驗收 SHALL 覆蓋技術／功效來源、主題列表與專利、排除／復原、AI pending review 及完成事件後的資料刷新。

#### Scenario: 確認排除一筆候選
- **WHEN** 使用者在分類區確認排除 pending 專利
- **THEN** 該筆 SHALL 從目前分析集合消失、核心專利仍存在且分群 artifact 不被重跑

### Requirement: SAT-005 證據與清理需人工閘門

suite SHALL 保存 viewport 截圖、console/network、API/DB 對帳、版本與未測項目；測試資料在使用者看過 manifest 並明確同意前不得清除。

#### Scenario: E2E 完成
- **WHEN** 全流程已執行且產生測試資料
- **THEN** 系統 SHALL 先輸出結果與清理候選，等待使用者明確核准後才執行 cleanup
