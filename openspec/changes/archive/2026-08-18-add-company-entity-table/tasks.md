# Tasks: add-company-entity-table

⚠ 依賴 `fix-company-alias-conflicts` 先完成：資料還有衝突時，回填與外鍵會失敗。

⚠ 本 change 會**改變既有行為**——刪除仍在集團中的代碼從「可以」變成「被擋」。
這是刻意的（design §3），但要在驗收時明確揭露給使用者。

⚠ 本 change 未經使用者確認前不得 apply。

---

## 1. 掃描（唯讀，動工第一件事）

- [x] 1.1 掃出所有會**新增／刪除／修改**公司代碼的路徑
      結果：4 個檔、22 處 DML。會**新增**代碼的：`company_group_repository.add_group_member`、
      `ingest_cli_suggestions`、`company_alias_importer`（既有代碼，不新建）；
      會**刪除**的：`delete_company_group`；會**改**的：`promote_company_code`
- [x] 1.2 回報範圍後才進 2.x

## 2. Migration（TDD：契約測試先行）

- [x] 2.1 schema 契約——`companies` 表存在、`company_code` 為 PK、`is_temp` 為衍生欄
- [x] 2.2 回填契約——取「別稱表 ∪ 集團成員表」（實庫實測 80 = 80）
- [x] 2.3 外鍵契約——`ON UPDATE CASCADE`／`ON DELETE RESTRICT` 子句正確
      ⚠ 2.1–2.3 的契約測試（`tests/test_migration_0053_company_entity.py`）是**實作後補寫**，
      不是先 Red。改以**變異檢查**取得等價證據：把外鍵子句改壞、把回填的 UNION 註解掉、
      把 `is_temp` 改成一般欄位、把 downgrade 反序、把卸的外鍵名改錯——五則變異全數轉紅，
      還原後 7 passed。過程中抓到測試自身一個假性通過（斷言被 `-- UNION` 註解裡的字餵飽），
      已加 `_strip_sql_comments` 修正
- [x] 2.4 Green：建表、回填、加外鍵（alembic head = `0053_company_entity`）
- [x] 2.5 downgrade：**對實庫實跑一輪並 rollback**——downgrade 確實移除表與外鍵，
      重跑 upgrade 在真資料上成功、筆數一致（80→80）、外鍵語意仍為 `r`／`c`

## 3. 反向驗證（沒做這步就是空約束）

- [x] 3.1 直接 SQL 刪除仍在集團中的代碼 → 被 `ForeignKeyViolation` 擋下（對象 UN177843）
- [x] 3.2 `UPDATE companies SET company_code=...` → 集團成員 1 筆自動跟著換
- [x] 3.3 插入指向不存在代碼的 `company_group_members` → 被擋
- [x] 3.4 **不誤擋**：未入集團的代碼仍可正常刪除（對象 UN254217）
      ⚠ 這條不在原規劃裡。只驗「擋得住」會漏掉過度攔截——約束擋太多與擋不住一樣是壞的

## 4. 三層擋

- [x] 4.1 API：`delete_company_group` 補 `ForeignKeyViolation` → 409，訊息指出代碼、
      卡在集團、下一步去集團區移出。未改用「先 SELECT 檢查再刪」
- [x] 4.2 `promote` 改寫為 `UPDATE companies`，集團成員交給 `ON UPDATE CASCADE`；
      目標代碼已存在時仍擋（改看 `companies`）
- [x] 4.3 新增代碼的路徑補寫 `companies`：`_ensure_company()`（INSERT ON CONFLICT DO NOTHING），
      由 `add_group_member`、`ingest_cli_suggestions` 呼叫
- [x] 4.4 前端顯示 409：既有 `callGroupMaintenance` 已統一把 `detail` 原文顯示，
      不需改前端；本輪只確保 API 的 detail 可行動

## 5. 驗收

- [x] 5.1 OpenSpec strict validation
- [x] 5.2 範圍回歸（直接／整合／契約）：495 passed / 35 skipped
      期間修好一個回歸：`test_deletes_only_that_code` 原判準綁「第一句 DELETE」，
      前面多一句 `companies` 刪除就誤報。改為**指名別稱那一句**，並補鎖
      「companies 必須先刪」的順序——順序錯了會在被外鍵擋下時已把別稱刪掉
- [x] 5.3 逐項對 design §6 判準（含 §3 三條反向驗證 ＋ 自加的不誤擋一條）
- [ ] 5.4 **明確揭露行為變更**：刪除集團中的代碼現在會被擋（待與使用者驗收時說明）
- [ ] 5.5 揭露未覆蓋範圍
- [ ] 5.6 使用者接受後 archive；同步 main specs 與 migration ledger

### 未覆蓋範圍（5.5 草稿）

- `company_aliases."申請人代碼"` **未加**外鍵（使用者裁決「丙」的刻意範圍：要動 12 處寫入點，
  別稱唯一性已由 0052 顧到）。⚠ 後果：直接對 `company_aliases` 寫入一個不存在於
  `companies` 的代碼**不會被擋**——正常路徑（本輪 `_ensure_company`）會補寫，但手動 SQL 不會
- `is_temp` 只是衍生欄，**沒有**「臨時代碼不得入正式集團」這類業務規則
- 前端 409 顯示走既有共用通道，未針對外鍵訊息做專屬 UI
