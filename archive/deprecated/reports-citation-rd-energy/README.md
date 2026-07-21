# Deprecated Reports: Citation Ranking and R&D Energy

日期：2026-07-20

## 狀態

以下報表已從正式產品目標與 active report catalog 移出：

- `top_cited_patents`：高被引用專利排名
- `company_rd_energy`：企業研發能量

## 移出原因

第一版正式報告暫不做引用型排名與企業研發能量圖。這兩項依賴 WIPS 引用欄位與引用資料完整度，容易讓報表解讀偏向資料下載時點快照，而不是目前第一版要呈現的穩定分析主軸。

## 原 active 位置

- `backend/app/reports/report_definitions.py`
  - `top_cited_patents`
  - `company_rd_energy`
- `backend/app/reports/chart_runner.py`
  - `_build_top_cited_section`
  - `_build_rd_energy_section`
  - `render_bubble_chart`

## 現行處理

production 報表檔已直接移除上述 definition 與 chart builder，不再用 runtime `pop` 或 filter 停用。舊碼留存在本目錄：

- `legacy_report_definitions.py`：`top_cited_patents`、`company_rd_energy` 舊 definition。
- `legacy_chart_builders.py`：`top_cited_patents`、`company_rd_energy` 舊 chart section builder。

因此：

- `REPORT_DEFINITIONS` 不再包含這兩個報表。
- `DEFAULT_REPORT_NAMES` 不再包含這兩個報表。
- API/MCP 指定這兩個 report key 會視為 unknown report。
- chart section registry 不再保留這兩個 section。
- archive 程式只供人工追溯，不得被 production import。

## 恢復條件

若未來要恢復，至少要先重新確認：

- WIPS 引用欄位在正式匯入批次的完整度。
- 引用數只是下載時點快照，前端與報表文字必須清楚標示。
- 公司名稱正規化完成後，`company_rd_energy` 才能作為公司層級統計。
