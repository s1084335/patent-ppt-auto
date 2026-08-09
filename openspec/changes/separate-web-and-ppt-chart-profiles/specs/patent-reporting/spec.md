## ADDED Requirements

### Requirement: RPT-015 同一圖表支援可驗證的 Web 與 PPT Profile

系統 SHALL 以相同 chart identity、dataset version、資料列、排序、色彩語意與版面邏輯產生 `web` 與 `ppt` rendering profile；兩者只得依目標媒介調整畫布尺寸、DPI、字級與必要邊距。

🔴 **2026-08-09 檔名契約回寫**：原設計把 identity 寫進檔名
（`{report_key}__{variant}.{profile}.svg`），實作時被兩件現實推翻——

1. `annual_trend.svg` **同時**是 `application_trend` 與 `publication_trend`
   兩個 report_key 的圖，「一檔一 identity」的命名模型表達不了；
2. 既有檔名與 report_key 本來就不同名（`country_distribution` 的圖叫
   `jurisdiction_distribution.svg`），改名會波及 artifact_manifest、
   build_ppt 的 ChartIndex 與所有既有報表版本。

⇒ 改為 **PPT profile 沿用既有檔名、web profile 加 `.web` 中綴**；
identity → path 的對應改由 `profile_manifest.json` 維護（一個檔可同時登記在
多個 identity 下）。identity 本身仍是 `report_key:variant`，與選圖契約一致。

#### Scenario: 同一圖表產生兩種 Profile

- **WHEN** 報表版本產生可供網頁選取與 PPT 使用的圖表
- **THEN** 兩個 artifact SHALL 具有相同 report、variant 與 chart identity
- **AND** manifest SHALL 記錄各自 profile、dataset version 與 checksum
- **AND** 資料、排序與色彩語意不得因 profile 不同而改變

#### Scenario: 其中一種 Profile 產生失敗

- **WHEN** web 或 PPT profile 缺少、損壞或 checksum 不符
- **THEN** 該 chart identity SHALL 被標示為不完整
- **AND** 系統不得以另一張任意舊圖或不同 identity 圖片代替

#### Scenario: 舊報表版本只有單一圖檔

- **WHEN** 使用者選擇尚未產生雙 profile 的舊報表版本
- **THEN** 匯出 PPT 時 SHALL 明確標示需要重產（`resolve_ppt_asset` fail loud）
- **AND** 不得把舊單 profile 靜默視為已驗證的 PPT profile
- **AND** **網頁瀏覽** SHALL 退回顯示既有單一圖檔，不得因缺 web profile 而空白

🔴 **2026-08-09 回寫理由**：原條文只寫「明確標示需要重產」，未區分兩端。
web profile 是 2026-08-09 才開始產的，在那之前的**每一個**報表版本都只有一份
圖——網頁端若照 PPT 端一樣 fail loud，等於所有既有版本的報表頁全空。

⚠ 兩端態度刻意不同：PPT 拿錯圖會讓簡報**悄悄用到別版資料**（比產不出來更糟），
故 fail loud；網頁最壞只是看到 PPT 尺寸的圖，內容完全正確，不值得讓整頁掛掉。

#### Scenario: web profile 的圖不得進入 PPT 素材索引

⚠ 動因：`artifact_manifest` 的 report_key 反查有一條前綴規則
（`opportunity_quadrant_*`），`.web.svg` 同樣命中——不擋就會讓網頁尺寸的圖
被組版端當成可用素材，且**沒有任何錯誤訊息**。

- **WHEN** 建立 artifact manifest 或組版端索引可用圖檔
- **THEN** `.web.svg` SHALL NOT 對應到任何 report_key
- **AND** profile manifest 仍 SHALL 能由 identity 反查到它（該處先還原成原檔名再查）
