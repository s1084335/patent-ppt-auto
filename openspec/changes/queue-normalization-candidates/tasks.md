# Tasks: queue-normalization-candidates

⚠ 分支：`feat/normalization-candidate-queue`（自 `feat/ai-company-normalization-review`
的 `cef38bf` 分出——本 change 的剩餘數要掛在該分支剛修好的建議 payload 上）。

⚠ 行為變更要在驗收時明確揭露：一次 job 只處理 20 個候選（原本是全部）。

---

## 1. Migration（TDD：契約測試先行）

- [ ] 1.1 Red：`company_normalization_asked` 契約——`lookup_key` 為 PK、
      `asked_patent_count` NOT NULL、`outcome` NOT NULL
- [ ] 1.2 Red：downgrade 對稱（刪表）
- [ ] 1.3 Green：建表
      ⚠ 不得在 migration 裡重寫名稱正規化運算式——本表只存候選查詢產出的值

## 2. 排隊查詢

- [ ] 2.1 Red：沒問過的排在問過的前面
- [ ] 2.2 Red：同層依 `patent_count DESC`
- [ ] 2.3 Red：蓋章且件數不變 → 不出現在候選清單
- [ ] 2.4 Red：件數增加 → 重新出現
- [ ] 2.5 Green：候選 SQL 加 LEFT JOIN、資格條件與排序
- [ ] 2.6 `list_company_normalization_candidates` 一併回傳 `lookup_key`（內部用）

## 3. 內部鍵不外洩

- [ ] 3.1 Red：`build_prompt` 的輸出不含 `lookup_key` 字樣與其值
      ⚠ 兩個都要斷言——只斷言欄名，值照樣可能被 dump 進去
- [ ] 3.2 Green：加 `_public_candidates` 投影（與既有 `_public_targets` 對稱）

## 4. 分段執行

- [ ] 4.1 Red：候選 20 個時，CLI 被呼叫 4 次、每次 5 個
- [ ] 4.2 Red：第 2 段丟契約錯誤 → 其餘 3 段照常寫入，整體不是零產出
- [ ] 4.3 Red：失敗段的候選**沒有**蓋章列，且下次會再被取到
- [ ] 4.4 Red：段內缺證據仍只跳過該筆（既有行為不得被分段改壞）
- [ ] 4.5 Green：切段、逐段隔離、成功段才蓋章
- [ ] 4.6 結果加 `failed_chunks`（段序、原因）與 `batch_size`／`chunk_size`

## 5. 剩餘數與失敗揭露

- [ ] 5.1 Red：建議端點回 `queue.remaining`／`never_asked`／`recheck`
- [ ] 5.2 Red：前端用 node **實際執行**渲染函式，畫得出剩餘數與失敗段
      ⚠ 不得只斷言字串存在於 HTML——2026-08-18 已因此漏掉一次
      （`test_normalization_skip_transport.py` 的由來）
- [ ] 5.3 Green：端點與前端

## 6. 驗收

- [ ] 6.1 逐項對 design §8 的八條判準
- [ ] 6.2 範圍回歸（直接／整合／契約）＋符號反查消費者
- [ ] 6.3 實機：部署後跑一次真 job，確認分段、剩餘數、蓋章都如預期
      ⚠ 真 job 會消耗 CLI 額度；先確認額度足夠再跑
- [ ] 6.4 揭露行為變更（一次只做 20）與未覆蓋範圍（併發、首次會重問全部）
- [ ] 6.5 使用者接受後 archive；同步 main specs 與 migration ledger
