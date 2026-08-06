## Why

0046 與 A1–A4 程式已合併，但正式 DB upgrade、derived refresh、完整重匯與報告實物仍未完成驗收。若現在結案，schema、importer 與報表之間的斷線仍可能未被發現。

## What Changes

- 完成 0045/0046 在目標 DB 的 upgrade 與資料完整性驗證。
- 重匯代表資料並確認分析／分群／報表使用欄位只從 core 或 people 取得。
- 執行 derived refresh、關鍵欄查詢、報表 smoke 與完整報告重產。
- 將最小 A5 gate 與正式交付驗收分開記錄，不把 smoke 冒充完整驗收。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `patent-data-model`：新增欄位重分類後的 live migration 與資料完整性契約。
- `patent-reporting`：新增正式資料重產與受影響報表不得靜默漏產的契約。
- `report-export`：新增以重匯資料完成整份報告實物驗收的契約。

## Scope

0045、0046、WIPS importer、derived report views、受影響報表與 A5 報告輸出。

## Non-goals

- 不在本 change 實作報告專業度的新 B 段功能。
- 不修改 DP-Means、snapshot cache 或 Installer。

## Impact

影響 Alembic、Supabase/目標 PostgreSQL、匯入 mapping/importer、derived refresh、報表與 PPT artifact。正式 DB 操作前需確認連線目標與備份／回復方式。

## Activation

需要 `alembic upgrade head`、代表資料重匯、`refresh_report_patent_base`、報表重產與 artifact 驗證；backend/worker 程式已進 image 時仍需依部署環境重建／重啟。

## Acceptance Gate

先過 15–30 分鐘最小 DB smoke 才可開始 A5；正式結案仍需完整 DB、受影響報表與逐頁實物驗收，最後由使用者裁決。

