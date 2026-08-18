# Tasks: fix-company-alias-conflicts

⚠ 本 change 動到 `confirmed` 正式資料。任何寫入前先存快照，否則出錯無法回復。

⚠ 閘門判準必須落在 `alias_lookup_key`，**不得寫成「防止多代碼」**——
一家公司多個 WIPS 代碼是常態，擋它會破壞合法的集團結構（design §1）。

⚠ 本 change 未經使用者確認前不得 apply。

---

## 1. 前置（唯讀）

- [x] 1.1 存快照：受影響列的原值（`company_aliases` 的 TTI Macao 兩列、
      `company_group_members` 的 group 3 全部成員）
- [x] 1.2 記錄基準數字：創科 39、泉峰 57、曾晴 14、格力博 13、
      廈門帝瑪斯 13、寶時得 12
      ⚠ 後五個是**防外溢**的對照組，本 change 不該動到它們

## 2. 修正歸戶衝突

- [x] 2.1 Red：一致性檢查——`confirmed` 內 `alias_lookup_key` 對到多代碼即紅
      （現況應抓到 1 組：`tti (macao commercial offshore) ltd.`）
- [x] 2.2 移除 `UN164421` 的 `TTI (MACAO COMMERCIAL OFFSHORE) Ltd.` 別稱
      依據＝WIPS 該代碼的別稱清單不含此名（2026-08-18 使用者提供畫面）
- [x] 2.3 Green：2.1 的檢查轉綠

## 3. 補集團成員

- [x] 3.1 `UN240278` 加入 `group_id=3`（創科）
      ⚠ 走既有集團區流程，不直接 INSERT——寫入規則只有一份
- [x] 3.2 觸發 `refresh_derived`
- [x] 3.3 驗數字：創科 39 → **44**；⚠ 其他五個集團**不得變動**

## 4. 加約束

- [x] 4.1 migration：`CREATE UNIQUE INDEX ux_alias_lookup_single_code
      ON company_aliases (alias_lookup_key) WHERE review_status='confirmed'`
- [x] 4.2 downgrade：可 DROP
- [x] 4.3 **反向驗證**：手動插一筆重複別稱 → 必須被 DB 擋
      ⚠ 沒做這步就是空閘門
- [x] 4.4 契約測試：migration 前後的 schema 差異

## 5. 驗收

- [ ] 5.1 OpenSpec strict validation
- [ ] 5.2 範圍回歸（直接／整合／契約）＋**符號反查消費者**比對已跑清單
- [ ] 5.3 逐項對 design §5 判準
- [ ] 5.4 揭露未覆蓋範圍
- [ ] 5.5 使用者接受後 archive；同步 main specs 與 migration ledger

