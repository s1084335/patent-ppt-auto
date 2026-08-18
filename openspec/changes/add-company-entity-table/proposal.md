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

1. 建立 `derived_layer.companies`（`company_code` 為主鍵），回填現有 80 個代碼。
2. 兩條外鍵，語意各不相同：
   - `company_aliases.申請人代碼` → `ON UPDATE CASCADE ON DELETE CASCADE`
   - `company_group_members.company_code` → `ON UPDATE CASCADE ON DELETE RESTRICT`
3. 三層擋：DB 約束保證、API 把違反翻成可行動的 409、前端顯示並指向下一步。
4. `promote` 改為 `UPDATE companies`，連動交由 `ON UPDATE CASCADE`。
5. 所有新增代碼的路徑補寫 `companies`。

## Non-Goals

- **不搬中文名／正規化名進 `companies`**。它們目前活在每一列別稱上，
  搬動會牽動所有讀取路徑。第一版只回答「這個代碼存在嗎」。
- 不改變「一家公司多個代碼」的既有結構。
- 不處理別稱唯一性（`fix-company-alias-conflicts` 已負責）。
- 不合併既有重複代碼——那是資料決策，需逐筆判斷。

## Impact

- Affected specs: `patent-data-model`（新實體與參照完整性）、
  `company-governance`（刪除／轉正的行為改變）
- Affected code: `backend/app/api/company_aliases.py`（delete／promote／
  code registry）、`backend/app/derived/company_alias_importer.py`、
  `backend/app/worker/ai_company_normalization_suggestion_runner.py`（確認寫入）
- Affected behaviour: **刪除仍在集團中的代碼會從「可以」變成「被擋」**
- Migration: 建表＋回填＋兩條外鍵；downgrade 可還原

## Open Questions

無。`ON DELETE` 語意已於 2026-08-18 定案：別稱 CASCADE（公司沒了別稱無獨立意義）、
集團成員 RESTRICT（把靜默的副作用變成明確的動作）。

⚠ 但 4a「掃出所有會刪除或改動公司代碼的路徑」的結果未知，
掃完才知道 API 層要改幾處——動工第一件事就是掃並回報範圍，不自行擴大。
