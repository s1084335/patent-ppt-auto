# Proposal: 修正公司歸戶衝突與集團漏算（fix-company-alias-conflicts）

## Why

2026-08-18 驗收 AI 公司正規化建議（#411）時，順帶掃出既有資料的兩類問題。
兩者都**不會報錯**，只會讓數字悄悄算錯。

### P1｜同一個別稱被兩個公司代碼認領，歸戶結果取決於查詢順序

```
alias_lookup_key = 'tti (macao commercial offshore) ltd.'
  → UN164421（Techtronic Industries Co., Ltd.）
  → UN240278（Chuang Ke (Macao offshore business services) Limited）
```

WIPS 權威資料（使用者提供畫面）明確：`TTI (MACAO COMMERCIAL OFFSHORE) Ltd`
只屬 `UN240278`。`UN164421` 的 WIPS 別稱清單裡沒有它——只有
`Chuang Ke Industry Co Ltd`（不同法人，Industry 非 Macao）。

⚠ **這不是「一家公司多個代碼」的問題**——那是 WIPS 常態，由 `company_groups`
收攏，完全合法。問題是**一個法人名字對到兩個法人代碼**，使歸戶依查詢順序而定。

目前那 5 件（#503／#507／#513／#562／#596）碰巧命中 `UN240278`，結果正確，
但那是運氣不是保證。

### P2｜創科集團少算 5 件

```
company_group_members（group_id=3 創科）
  UN164421  創科                          16 件
  UN109300  美沃奇                        21 件
  TEMP:techtronic-outdoor-products...      2 件
  ❌ 沒有 UN240278
```

`Chuang Ke Limited`（TTI Macao，創科的澳門離岸公司）那 5 件自成一國。
**集團實際 44 件，現在算 39 件**——申請人排名、集中度、Key Players 全部受影響。

⚠ 這是本 change 唯一會改變報表數字的一項。

### P3｜沒有任何機制防止 P1 再發生

`company_aliases` 現有的唯一索引是
`UNIQUE (申請人代碼, alias_lookup_key) WHERE review_status='confirmed'`
——代碼在鍵裡，所以「同一別稱、不同代碼」是被允許的。

## What Changes

1. 依 WIPS 權威資料，把 `TTI (MACAO COMMERCIAL OFFSHORE) Ltd` 從 `UN164421`
   的別稱中移除。
2. 把 `UN240278` 加入 `group_id=3`（創科）。
3. 新增 partial unique index：`UNIQUE (alias_lookup_key) WHERE
   review_status='confirmed'`，讓 P1 在寫入當下就被擋。

## Non-Goals

- **不禁止一家公司擁有多個 WIPS 代碼**——那是常態，集團層負責收攏。
  本 change 只禁「一個別稱對到多個代碼」。
- 不建立公司實體表、不加外鍵——那是 `add-company-entity-table` 的範圍。
- 不修改 `promote`／`delete` 端點的連動缺口（同上）。
- 不動 `company_groups` 既有的四個集團定義，只補一個成員。

## Impact

- Affected specs: `company-governance`
- Affected data: `derived_layer.company_aliases`（移除 1 列）、
  `derived_layer.company_group_members`（新增 1 列）
- Affected reports: 創科集團件數 39 → 44；申請人排名與集中度連帶變動
- ⚠ 動到 `confirmed` 正式資料，執行前須存快照

## Open Questions

無。TTI Macao 的歸屬由 WIPS 畫面確認（2026-08-18 使用者提供），
`UN240278` 屬創科集團由公司名（Chuang Ke = 創科的澳門離岸公司）確認。
