## 1. 基準與 required checks

- [ ] 1.1 記錄 Python/Docker/uv 版本、現有無 DB/DB tests、ruff/mypy 歷史基準、OpenSpec 與 `verify_module.py` 現況
- [ ] 1.2 定義 fast required、DB/browser/AI integration profiles、skip manifest 與 CI artifact 保留契約
- [ ] 1.3 固定 changed-scope blocking 規則，禁止以本次量測結果回頭放寬門檻

## 2. TDD：版本、lint、type、test

- [ ] 2.1 Red：新增 Python 版本一致性與 CI runtime 漂移測試
- [ ] 2.2 Green：加入 `.python-version` 及與 Docker/uv/CI 相容的單一版本設定
- [ ] 2.3 Red：用 fixture/temporary diff 證明新增 lint/type 違規會被守門抓到、既有債分開報告
- [ ] 2.4 Green：加入 ruff/mypy 漸進設定及 changed-scope runner
- [ ] 2.5 Red：新增 production/Supabase DATABASE_URL 防護與 skipped integration manifest 測試
- [ ] 2.6 Green：建立 required test/spec jobs 與隔離 integration profiles

## 3. TDD：跨層契約

- [ ] 3.1 盤點 PATENT_COLUMNS、API schema、REPORT_DEFINITIONS、manifest 與 portable PPT input 的 producer/consumer
- [ ] 3.2 Red：刻意移除／改名欄位，確認 contract check 能指出 producer/consumer 差異
- [ ] 3.3 Green：建立 producer-generated contract 與 consumer compare，不複製手工欄位清單
- [ ] 3.4 Refactor：讓 CI 與 `verify_module.py` 共用底層輸出／門檻，不建立第二份規則

## 4. CI 驗收

- [ ] 4.1 在乾淨環境依序跑 version、OpenSpec strict、lint、type、無 DB tests、contract checks，保存所有 artifacts
- [ ] 4.2 對 spec/lint/type/test/contract 各做一次 mutation，記錄真紅與還原後全綠
- [ ] 4.3 執行本 change 的目標測試與 `scripts/verify_module.py`；揭露未啟用的 DB/browser/AI profiles
- [ ] 4.4 交使用者確認 required checks 與耗時後才設 branch protection 或 archive
