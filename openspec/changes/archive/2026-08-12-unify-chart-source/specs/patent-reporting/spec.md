# patent-reporting（delta）

## ADDED Requirements

### Requirement: 圖表單一來源輸出

系統 SHALL 為每張圖表只輸出**一個 SVG 檔**（既有原檔名、WEB 呈現尺寸）；
HTML 顯示與簡報轉換 SHALL 共用此同一來源，簡報端的字級適配由消費端執行，
引擎不得為特定輸出媒介預先產第二份尺寸版本。

#### Scenario: 新版本每張圖恰一檔

- **WHEN** 系統完成一次報表產製
- **THEN** 版本目錄內每張圖 SHALL 恰有一個 SVG（無 `.web` 中綴副本）
- **AND** SHALL 不產生 `profile_manifest.json`

#### Scenario: 舊版本相容顯示

- **GIVEN** 本需求生效前產製的版本（原檔名為簡報尺寸、另有 `.web.svg`）
- **WHEN** 網頁報表顯示該版本
- **THEN** SHALL 優先採用 `.web.svg`，缺檔時退回原檔——新舊版本皆正確顯示，
  不要求重產
