# Tasks: add-company-entity-table

⚠ 依賴 `fix-company-alias-conflicts` 先完成：資料還有衝突時，回填與外鍵會失敗。

⚠ 本 change 會**改變既有行為**——刪除仍在集團中的代碼從「可以」變成「被擋」。
這是刻意的（design §3），但要在驗收時明確揭露給使用者。

⚠ 本 change 未經使用者確認前不得 apply。

---

## 1. 掃描（唯讀，動工第一件事）

- [ ] 1.1 掃出所有會**新增／刪除／修改**公司代碼的路徑
      （已知：`delete_company_group`、`promote_company_code`；
      待查：匯入、代碼區重建、AI 建議確認）
- [ ] 1.2 回報範圍後才進 2.x
      ⚠ 不自行擴大改動範圍

## 2. Migration（TDD：契約測試先行）

- [ ] 2.1 Red：schema 契約測試——`companies` 表存在、`company_code` 為 PK、
      `is_temp` 為衍生欄
- [ ] 2.2 Red：回填契約——`companies` 筆數＝`SELECT DISTINCT 申請人代碼`
- [ ] 2.3 Red：外鍵契約——兩條外鍵的 `ON UPDATE`／`ON DELETE` 子句正確
- [ ] 2.4 Green：建表、回填、加外鍵
- [ ] 2.5 downgrade：可還原且測試通過

## 3. 反向驗證（沒做這步就是空約束）

- [ ] 3.1 直接 SQL 刪除仍在集團中的代碼 → **必須被擋**
- [ ] 3.2 `UPDATE companies SET company_code=...` → 別稱與集團成員**自動跟著換**
- [ ] 3.3 插入一筆 `company_group_members` 指向不存在的代碼 → **必須被擋**

## 4. 三層擋

- [ ] 4.1 API：1.1 掃出的每一處補 `ForeignKeyViolation` → `HTTPException(409)`
      ⚠ 訊息要**可行動**（說明哪個代碼、卡在哪、下一步做什麼）
      ⚠ **不得改用「先 SELECT 檢查再刪」**取代外鍵（競態＋新端點會漏）
- [ ] 4.2 `promote` 改寫：`UPDATE companies`，連動交給 `ON UPDATE CASCADE`
      目標已存在時仍擋，判斷改看 `companies`
- [ ] 4.3 新增代碼的路徑補寫 `companies`（依 1.1 結果）
- [ ] 4.4 前端顯示 409 訊息並指向下一步（集團區）

## 5. 驗收

- [ ] 5.1 OpenSpec strict validation
- [ ] 5.2 範圍回歸（直接／整合／契約）＋**符號反查消費者**比對已跑清單
- [ ] 5.3 逐項對 design §6 判準（含三條反向驗證）
- [ ] 5.4 **明確揭露行為變更**：刪除集團中的代碼現在會被擋
- [ ] 5.5 揭露未覆蓋範圍
- [ ] 5.6 使用者接受後 archive；同步 main specs 與 migration ledger
