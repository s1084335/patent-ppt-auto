## Context

0045、0046 與 A1–A4 已在 `master`；目前缺口是目標 DB、重匯、derived 與實物輸出的 live evidence。核心風險為 view 相依順序、psycopg 參數名、舊 attribute 資料搬移及報表漏產。

## Goals / Non-Goals

**Goals:** 驗證 migration、匯入、derived、報表與 PPT 的完整資料流，留下可重現證據。

**Non-Goals:** 不加入報告專業度新功能，不修改分群模型。

## Decisions

1. **先最小 DB gate，再完整驗收。** 最小 gate 只決定能否進 A5，不等於結案。
2. **以 live schema/data 為準。** 檢查 `alembic_version`、欄位、非空值與 query output，不引用 migration 檔即宣告已套用。
3. **欄位責任由 mapping/importer/schema/derived 契約共同鎖定。** 使用欄位不得在 attributes 留第二正式來源。
4. **報表完整性用 selected/persisted/rendered 三集合對帳。** 任一漏產都要帶原因。

## 程式與測試落點

- Migration：`0045_expanded_view_columns.py`、`0046_core_field_reclassification.py`
- Import：`backend/app/mappings/wips.py`、`backend/app/importers/wips_importer.py`
- Derived/report：`backend/app/derived/`、`backend/app/reports/`
- Tests：`test_core_field_reclassification.py`、`test_expanded_view_aggregates.py`、`test_patent_kind.py`、`test_applicant_split.py`、`test_report_analysis_types.py`、`test_chart_sections.py`

## 輸出契約

保存 migration/refresh 查詢摘要、匯入摘要、報表版本、`report_data.json`、charts、narratives、`.pptx` 與逐頁驗收產物。秘密與完整 DB dump 不進 repo。

## Risks / Trade-offs

- [誤連正式 DB] → 執行前查 `current_database/current_user/server` 並明示目標。
- [VIEW dependency 阻擋] → 依 expanded/base 實體相依順序 upgrade/rollback。
- [smoke 假綠] → 正式交付另跑完整報表與逐頁驗收。

## Migration Plan

1. 記錄目標 DB 與 migration baseline。
2. `alembic upgrade head`，失敗即停。
3. refresh、關鍵欄與兩報表 smoke。
4. 代表資料重匯與完整 report/PPT 重產。
5. 回復以 DB 備份或已驗證 downgrade 為準，不在未知正式資料上即興回滾。

