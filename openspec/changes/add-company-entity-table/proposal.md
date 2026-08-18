# Proposal: 建立公司實體表與參照完整性（add-company-entity-table）

## Why

### P1｜「公司」在資料模型裡不存在

```
derived_layer.company_aliases        226 列 ／ 80 代碼   一代碼多列（別稱表）
derived_layer.company_group_members    9 列 ／  9 代碼   只有「有集團」的 9 家
                                                        ← 沒有任何表定義「這個代碼存在」
```

公司代碼只是**散在別稱表裡的一個重複欄位**。沒有實體，就沒有東西可以被參照，
於是任何「代碼被刪掉或改掉」的操作都無法保證連動。

### P2｜刪代碼會留下集團孤兒

`DELETE /company-codes/{code}` 只刪 `company_aliases`，
不動 `company_group_members`：

```python
cur = conn.execute('DELETE FROM company_aliases WHERE "申請人代碼" = %s', (code,))
# company_group_members 完全沒被碰
```

刪完之後集團成員列指向一個不存在的代碼，集團統計少一家，**不報錯**。

### P3｜轉正（TEMP → 真代碼）漏更新集團成員

`POST /company-codes/{code}/promote` 一句 UPDATE 換掉 `company_aliases` 的代碼，
同樣不動 `company_group_members`。換完之後集團仍記著舊的 TEMP 代碼——
該公司從集團統計消失，**同樣不報錯**。

⚠ 庫裡目前有 3 個 TEMP 代碼是集團成員
（`TEMP:techtronic-outdoor-products-*`、`TEMP:leo-group-pump-*`、
`TEMP:zhejiang-liou-landscape-*`），未來任一個轉正都會踩到。

### P4｜新增代碼沒有唯一登記處

代碼由多條路徑產生（AI 建議確認、代碼區手動新增、匯入），各自直接 INSERT
進 `company_aliases`。打錯字或重複建立不會被任何機制發現。

## What Changes

1. 建立 `derived_layer.companies`（`company_code` 為主鍵），回填現有代碼
   （別稱表 ∪ 集團成員表，實測 80 筆）。
2. **一條**外鍵：`company_group_members.company_code` →
   `ON UPDATE CASCADE ON DELETE RESTRICT`。
3. 三層擋：DB 約束保證、API 把違反翻成可行動的 409、前端顯示並指向下一步。
4. `promote` 改為 `UPDATE companies`，集團成員連動交由 `ON UPDATE CASCADE`。
5. 新增代碼的路徑補寫 `companies`（`_ensure_company()`）。

## Capabilities

### New Capabilities

- `patent-data-model`：公司代碼成為可被參照的實體，且代碼改名時參照自動連動。

### Modified Capabilities

- `company-governance`：刪除代碼的行為改變——仍在集團中的代碼會被擋下並回 409。

## Scope

- 建 `derived_layer.companies` 與回填。
- 加 `company_group_members.company_code` 外鍵。
- `delete_company_group`、`promote_company_code`、`add_group_member`、
  `ingest_cli_suggestions` 四處寫入路徑配合調整。
- 前端沿用既有 `callGroupMaintenance` 的錯誤顯示通道（不新增 UI）。

⚠ 2026-08-18 使用者裁決「丙」：**只做 `company_group_members` 那一條外鍵**。
`company_aliases."申請人代碼"` 那條不做——它要動 12 處寫入點，效益低很多，
而別稱唯一性已由 `fix-company-alias-conflicts`（0052）顧到。

## Non-goals

- **不搬中文名／正規化名進 `companies`**。它們目前活在每一列別稱上，
  搬動會牽動所有讀取路徑。第一版只回答「這個代碼存在嗎」。
- 不改變「一家公司多個代碼」的既有結構——那是 WIPS 的常態，集團正規化正是為此而存在。
- 不處理別稱唯一性（`fix-company-alias-conflicts` 已負責）。
- 不合併既有重複代碼——那是資料決策，需逐筆判斷。
- 不加 `company_aliases` 那條外鍵（見 Scope 的裁決）。

## Impact

- Affected specs: `patent-data-model`（新實體與參照完整性）、
  `company-governance`（刪除／轉正的行為改變）
- Affected code: `backend/app/api/company_aliases.py`（delete／promote／確認）、
  `backend/app/repositories/company_group_repository.py`（`_ensure_company`）、
  `backend/app/derived/company_alias_importer.py`
- Affected behaviour: **刪除仍在集團中的代碼會從「可以」變成「被擋」**（回 409）
- Migration: `0053_company_entity`——建表＋回填＋一條外鍵；downgrade 可還原
  （已對實庫實跑一輪並 rollback 驗證）

## Activation

- Migration `alembic upgrade head` 後即生效，不需重跑 derived refresh
  （不改任何既有欄位值）。
- 後端需重啟以載入 API 變更；Companion 需重啟（確認流程的例外翻譯有變）。
- 前端為 bind mount 的靜態檔，`git pull` 後重新整理即可。

## Acceptance Gate

1. `companies` 筆數＝別稱表的相異代碼數（實測 80 = 80）。
2. `is_temp` 為衍生欄（GENERATED ... STORED），寫不進去。
3. 反向驗證四條全過：刪集團中的代碼被擋、改代碼時集團成員連動、
   集團成員不得指向不存在的代碼、**未入集團的代碼仍可正常刪除（不誤擋）**。
4. downgrade 對實庫實跑可還原，重跑 upgrade 後筆數一致、外鍵語意不變。
5. 範圍回歸（直接／整合／契約）全綠。
6. 行為變更（刪代碼會被擋）已明確揭露給使用者。

## Confirmed Decisions

- 2026-08-18：`ON DELETE RESTRICT`——把靜默的副作用變成明確的動作。
  刪代碼與改集團是兩件事，`CASCADE` 會讓一個動作偷偷做兩件。
- 2026-08-18：`ON UPDATE CASCADE`——轉正時集團成員自動跟著換，
  P3 那個 bug 從此**寫不出來**，不是靠記得去改。
- 2026-08-18：範圍取「丙」，只做集團成員那一條外鍵（理由見 Scope）。
- 2026-08-18：不用「先 SELECT 檢查再刪」取代外鍵——有競態，且新端點會漏。

## Open Questions

無。

（原本掛在這裡的「掃出所有會刪除或改動公司代碼的路徑」已於動工第一件事完成：
4 個檔、22 處 DML，結果記在 `tasks.md` 1.1。）
