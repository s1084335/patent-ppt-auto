## Why

專案已有 `scripts/verify_module.py` 與大量測試，但缺 `.python-version`、ruff/mypy 專案設定與 CI，前後端欄位契約也只由零散測試保護。人工記得跑守門不足以阻止未來 PR 漂移。

## What Changes

- 固定支援的 Python 版本並與 Docker、uv、CI matrix 保持一致。
- 在 `pyproject.toml` 定義 ruff 與漸進式 mypy 範圍，不要求一次清除全庫歷史債。
- 建立 CI：規格 strict validation、目標靜態分析、無 DB 測試、可選 DB integration stage 與 artifact 報告。
- 建立結構化契約輸出／比較，保護 `PATENT_COLUMNS`、API schema、report definitions 與 portable PPT 所消費欄位。
- 將 `verify_module.py` 作為交付證據而非另一套重複規則。

## Capabilities

### New Capabilities

- `quality-automation`: 定義版本鎖定、lint/type/test/spec/contract CI 與失敗證據契約。

### Modified Capabilities

無。

## Scope

只建立可持續守門與漸進式採用範圍；不在同一 change 修復全庫既有 lint/type/complexity 問題。

## Non-goals

- 不以刪測試或放寬斷言換取綠燈。
- 不讓 CI 連正式 Supabase 或使用正式 secret。
- 不重複定義 AGENTS.md 的門檻；腳本與 CI 消費同一規則。

## Impact

- `.python-version`、`pyproject.toml`、CI workflow、contract export/compare scripts 與測試。
- 可能新增開發依賴，但必須鎖版並只用於開發／CI。

## Activation

先以目前變更範圍和既有無 DB 測試啟用 required checks；mypy 與 DB integration 採明確擴張清單，不把已知歷史債一次設成 blocking。

## Acceptance Gate

在乾淨環境重跑 CI；刻意製造 spec、lint、type、測試與前後端契約漂移時相應 job 必須變紅，還原後全綠，且輸出能區分本次新增問題與既有債。
