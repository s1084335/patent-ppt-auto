# Design: 正規化候選排隊分批

## 1. 根因

候選**不是資料列**，是每次即時算出來的：

```sql
SELECT md5(n.lookup_key) AS ref_hash, ... FROM names n
WHERE NOT EXISTS (... confirmed ...) AND NOT EXISTS (... ai_suggested ...)
ORDER BY count(DISTINCT n.patent_id) DESC, min(n.raw_name)
```

「被問過」不是這個查詢能表達的事實——它不改變任何欄位。所以**沒有地方蓋章**，
才會每次都重問。這是本 change 唯一的根因；分批、剩餘數都是它的衍生。

## 2. 蓋章表

```sql
CREATE TABLE derived_layer.company_normalization_asked (
    lookup_key        TEXT PRIMARY KEY,
    last_asked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_run_id       BIGINT,
    asked_patent_count INTEGER NOT NULL,
    outcome           TEXT NOT NULL      -- 'suggested' | 'no_evidence'
);
```

### 為什麼鍵是 `lookup_key` 而不是 `candidate_ref`

`candidate_ref` 是 `'cand:' || left(md5(lookup_key), 16)`，**衍生自** lookup_key。
用它當鍵等於把「怎麼算 ref」這段邏輯複製到 SQL 的 JOIN 裡（目前它在 Python）。
⚠ 同一份知識只能有一個定義處——用自然鍵 `lookup_key` 就沒有第二份。

### 為什麼不需要新的正規化運算式

`lookup_key` 由候選查詢算出（`lower(regexp_replace(BTRIM(part), '\s+', ' ', 'g'))`），
蓋章時**原樣寫回**，不重算。⚠ 若在 Python 或 migration 裡再寫一次同樣的
運算式，兩份會各自演進而不報錯——改為「一方產生、一方消費」。

為此 `list_company_normalization_candidates` 要把 `lookup_key` 一起回傳（內部用）。

### `outcome` 存來做什麼

本輪不用它做判斷（重新入列只看 `patent_count`）。存它是為了**日後查得出來**
某個候選是「問過查不到」還是「問過有結果但使用者沒確認」。
⚠ 不得因為現在沒用到就省略——這兩者在畫面上長得一樣，事後無從分辨。

## 3. 排隊

候選查詢加一個 LEFT JOIN：

```sql
LEFT JOIN derived_layer.company_normalization_asked a ON a.lookup_key = n.lookup_key
WHERE ...既有兩個 NOT EXISTS...
  AND (a.lookup_key IS NULL OR count(DISTINCT n.patent_id) > a.asked_patent_count)
ORDER BY a.last_asked_at NULLS FIRST,
         count(DISTINCT n.patent_id) DESC,
         min(n.raw_name)
LIMIT %(limit)s
```

（`patent_count` 是聚合值，實際實作以 `HAVING` 或包一層 subquery 表達；
語意以上式為準。）

兩個子句各自負責一件事，不要混談：

| 子句 | 保證 |
|---|---|
| `WHERE (未問過 OR 件數變多)` | **誰有資格**進隊列（使用者裁決「乙」） |
| `ORDER BY last_asked_at NULLS FIRST` | **誰先被問**——沒問過的一律排前面 |

⚠ 只有 ORDER BY 沒有 WHERE，等於「延後重問」而不是「不重問」，
輪完一圈後那批自然人會再燒一次。兩者缺一不可。

## 4. 分段

一個 job：`fetch_candidates(limit=20)` → 切成 `ceil(20/5)=4` 段 → 逐段呼叫 CLI。

```
批次 20
├─ 段1 (5) ─ CLI ─ 驗證 ─ 寫入 ─ 蓋章
├─ 段2 (5) ─ CLI ─ ✗ 契約錯誤 ─ 記入 failed_chunks，不蓋章
├─ 段3 (5) ─ CLI ─ 驗證 ─ 寫入 ─ 蓋章
└─ 段4 (5) ─ CLI ─ 驗證 ─ 寫入 ─ 蓋章
```

### 失敗段為什麼不蓋章

契約錯誤代表「**協定壞了**」，不是「這個候選查不到證據」。蓋了章等於把一個
程式問題當成資料結論，把候選推到隊尾——下次也不會回來（因為件數沒變）。
⚠ 這會讓一批候選因為一次程式錯誤**永久消失**，而且沒有任何訊息。

### 契約錯誤從「整批拒絕」改為「拒絕該段」

原決策成立於「一個 job＝一次呼叫」的時代，那時「整批」與「該段」是同一件事。
現在切段後若仍整批拒絕，分段就完全失去意義。
⚠ 原決策要守的是「**不得靜默吞掉協定錯誤**」——這一點由
「失敗段數與原因必須出現在結果與畫面」承接，沒有放寬。

### 段內仍維持既有規則

- 缺證據 → 跳過該筆（不影響同段其他筆）
- 契約錯誤 → 拒絕該段

## 5. 剩餘數

建議端點回傳：

```json
"queue": {"remaining": 34, "never_asked": 30, "recheck": 4}
```

- `never_asked`：沒有蓋章紀錄的
- `recheck`：問過但件數變多、等著重問的
- `remaining` = 兩者相加

⚠ 前端不得只顯示「完成」。跑完一批要能讀出「這批做完了，還有 N 個沒做」。

## 6. 內部鍵不外洩

`build_prompt` 目前把 `candidates` **整包** dump 進 prompt，只有 targets 有
`_public_targets` 投影。加了 `lookup_key` 之後就會外洩。

補一個對稱的 `_public_candidates`，只放 `candidate_ref`／`raw_name`／
`candidate_type`／`source_fields`／`patent_count`。

⚠ 外洩 lookup_key 本身不算敏感（它就是小寫化的名字，AI 已從 raw_name 看得到），
但它是**內部識別鍵**：一旦出現在 prompt 裡，AI 就可能在輸出裡引用它，
之後就會有人拿 AI 回傳的值去 JOIN——受控輸入的邊界就破了。

## 7. 已知限制（刻意不處理）

- **併發**：兩個正規化 job 同時跑會取到同一批 20 個。蓋章在段完成後才寫，
  所以會重複查證與重複寫入建議（建議寫入本身是 DELETE+INSERT，冪等）。
  現況只有一個 Companion 消費此 job type、且由使用者手動觸發，
  加鎖的複雜度不划算。⚠ 若日後開多個 worker 消費此型，必須回頭處理。
- **上線首次會重問所有候選**：舊 run 沒有留下誰被問過，無從回填。

## 8. 驗收判準（對應 proposal Acceptance Gate）

| # | 怎麼驗 |
|---|---|
| 1 | 造 A（沒問過）與 B（問過），查詢結果 A 必在 B 前 |
| 2 | 蓋章且件數不變 → 該候選不出現在候選清單 |
| 3 | 蓋章後把件數加 1 → 該候選重新出現 |
| 4 | 以假 CLI runner 側錄呼叫次數＝4、每次 candidates 長度＝5 |
| 5 | 讓第 2 段丟契約錯誤 → 其餘 3 段照常寫入；第 2 段的 5 個候選**沒有**蓋章列 |
| 6 | 結果含 `failed_chunks`＋原因；前端 HTML 實際渲染得出來（node 執行） |
| 7 | 端點回 `queue.remaining`；前端渲染出剩餘數 |
| 8 | `build_prompt` 輸出字串中不含 `lookup_key` 與其值 |

⚠ 判準 5 與 6 是本 change 最容易做對一半的地方：段隔離做了、但失敗沒顯示，
或顯示了卻仍蓋章。兩邊都要驗。
