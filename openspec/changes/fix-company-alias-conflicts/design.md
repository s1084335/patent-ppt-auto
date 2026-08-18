# Design: 修正公司歸戶衝突與集團漏算

## §1 兩種「多」要分清楚

這是本 change 最容易做錯的地方，寫在最前面：

| 情形 | 合法？ | 由誰處理 |
|---|---|---|
| 一家公司有**多個 WIPS 代碼** | ✅ **常態** | `company_groups` 收攏成集團 |
| 一個**別稱**對到多個代碼 | ❌ 歸戶不確定 | 本 change 的 unique index |

⚠ 閘門如果寫成「防止多代碼」，會去擋合法的集團結構——**那比沒有閘門更糟**。
判準必須落在 `alias_lookup_key` 這一層。

實例：創科集團合法地擁有 `UN164421`（Techtronic Industries）、`UN109300`（美沃奇）、
`UN240278`（Chuang Ke Macao）、`TEMP:techtronic-outdoor-products-*` 四個代碼。
不合法的是 `TTI (MACAO...)` 這個名字同時掛在 `UN164421` 與 `UN240278` 下。

## §2 為什麼用資料庫約束，不用測試閘門

原本規劃了四條機械閘門，用 deepen design §1.2 的三問逐條檢查後刪掉三條：

| 原提案 | Q2「滿足它的唯一途徑＝把事情做對？」 | 判決 |
|---|---|---|
| 一別稱不得多代碼 | ✅ 恆等式 | 留，但改用 DB 約束 |
| TEMP 不得與真代碼共用別稱 | ⚠ 上一條的**子集**，自動成立 | 刪 |
| 同名多代碼跨集團就揭露 | ❌ 代理指標；且上一條成立後此情形不存在 | 刪 |
| 集團成員代碼必須存在 | ✅ 但這是**外鍵**不是測試 | 移到 `add-company-entity-table` |

⚠ 四條是「發現一個加一條」加出來的，那正是規則愈補愈碎的典型。

**約束優於測試**的三個理由：
- 寫入當下就擋，不是事後才發現
- 任何路徑都擋（API、psql、未來新端點、別人的腳本）
- 不需要有人記得跑

⚠ 測試只驗你記得要驗的路徑；而且單元測試用假資料時，抓不到正式資料的漂移。

## §3 index 的形式與時機

```sql
CREATE UNIQUE INDEX ux_alias_lookup_single_code
  ON derived_layer.company_aliases (alias_lookup_key)
  WHERE review_status = 'confirmed';
```

- **partial（只管 confirmed）**：`ai_suggested` 是待審草稿，允許暫時重複；
  ⚠ 但這也表示**確認一筆會撞名的建議時會被 index 擋下**——那是 index 正確工作，
  不是新 bug。UI 需能把該錯誤翻成可讀訊息（本 change 只加 index，
  訊息翻譯併入 `add-company-entity-table` 的三層擋一起做）。
- **必須先清資料**：現存 1 組違規（TTI Macao），不清的話 index 建不起來。
  ⚠ 這是好事——建不起來就是資料還沒修對的證據。

## §4 執行順序與不可逆點

```
1. 存快照（受影響的列原值）        ← 可回復的前提
2. 移除 UN164421 的 TTI Macao 別稱
3. UN240278 加入 group 3
4. 建 unique index
5. refresh_derived → 驗數字
```

⚠ 2 與 3 沒有先後依賴，但 **4 必須在 2 之後**。
⚠ 3 是唯一會改報表數字的一步（39→44），驗收要盯它。

## §5 驗收判準

| 項目 | 判準 | 反向驗證 |
|---|---|---|
| 集團件數 | 創科 39 → **44** | — |
| `Chuang Ke Limited` | 不再自成一國 | — |
| 那 5 件的顯示名 | **不變**（仍 `Chuang Ke Limited`） | 變了＝動到別稱而非集團 |
| Briggs | 維持現況（待審處理時已解決） | — |
| unique index | 建立成功 | 手動插一筆重複別稱 → **被 DB 擋** |
| 其他集團 | 泉峰 57／曾晴 14／格力博 13 等**不變** | 變了＝改動範圍外溢 |

⚠ 最後一列是防外溢：本 change 只該動創科，其他集團的數字一動就是錯。

## §6 不做什麼

- 不建公司實體表、不加外鍵（`add-company-entity-table`）
- 不修 `promote`／`delete` 的連動缺口（同上）
- 不改「多代碼」這件事本身
