# Design: 公司實體表與參照完整性

## §1 為什麼是「讓錯誤無法表示」而不是「表示了再抓」

今晚查到的三個缺口（刪代碼留孤兒、轉正漏更新集團、新增代碼無登記處）
有同一個根因：**沒有實體，就沒有東西可以被參照**。

三種解法比較：

| | 做法 | 錯誤何時被發現 | 涵蓋範圍 |
|---|---|---|---|
| 甲 | 加測試查孤兒 | 有人跑測試時 | 只涵蓋測試想得到的路徑；假資料抓不到正式資料漂移 |
| **乙** | **建實體表＋外鍵** | **寫入當下** | **所有路徑**——API、psql、未來端點、別人的腳本 |
| 丙 | 靠人工 | 不會被發現 | 已證明會漏（今晚查出來的） |

⚠ 原本規劃的是甲。乙 才對，因為它讓錯誤狀態**無法表示**。

## §2 表的最小形狀

```sql
CREATE TABLE derived_layer.companies (
    company_code text PRIMARY KEY,
    is_temp      boolean GENERATED ALWAYS AS (company_code LIKE 'TEMP:%') STORED,
    created_at   timestamptz NOT NULL DEFAULT now()
);
```

⚠ **只放「代碼存在」這一件事**。中文名／正規化名留在 `company_aliases`——
它們現在被每一列別稱攜帶，搬進實體表會牽動所有讀取路徑（報表的四層
COALESCE、集團投影、匯出），那是另一個 change 的範圍。

`is_temp` 用衍生欄而不是應用層判斷：`TEMP:` 前綴的語意
（2026-07-28「代碼只能查 WIPS 給」）就固定在 schema 裡，不會有第二套判定。

## §3 兩條外鍵，語意刻意不同

```sql
company_aliases.申請人代碼         → ON UPDATE CASCADE  ON DELETE CASCADE
company_group_members.company_code → ON UPDATE CASCADE  ON DELETE RESTRICT
```

### ON DELETE 的取捨

刪一個仍在集團裡的代碼，三種可能：

| 設定 | 行為 | 問題 |
|---|---|---|
| 現況（無外鍵） | 別稱刪掉、集團成員留下指向空的代碼 | 集團少一家，**無提示** |
| `CASCADE` | 集團成員一起消失 | 一個動作偷偷做了兩件事——你只想清 TEMP 代碼，卻順手改了集團統計 |
| **`RESTRICT`** | **擋下，要求先退出集團** | 多一步 |

選 RESTRICT 的理由只有一個：**把靜默的副作用變成明確的動作**。
刪代碼與改集團是兩件事，分兩步才看得見自己在改什麼。

別稱給 CASCADE，是因為別稱沒有獨立意義——公司沒了，它的別名留著也沒用；
且現有 delete 端點行為就是如此，不算改變語意。

### ON UPDATE CASCADE 是修 P3 的關鍵

```sql
UPDATE companies SET company_code = 'UN177843'
                 WHERE company_code = 'TEMP:briggs-stratton-llc';
```

DB 自動把 `company_aliases` 與 `company_group_members` 一起換掉。
`promote` 端點「要記得更新哪幾張表」的問題，**在結構上消失**。

## §4 三層擋：各自的職責

| 層 | 負責 | 拿掉會怎樣 |
|---|---|---|
| DB 約束 | **保證**不發生 | 回到現在：靠每個端點記得，漏一個就出事 |
| API 轉譯 | 讓錯誤**可行動** | 使用者看到 500 與英文堆疊 |
| 前端顯示 | 讓使用者**看得到** | 按了沒反應 |

API 層形如：

```python
except ForeignKeyViolation as exc:
    raise HTTPException(
        status_code=409,
        detail=f"無法刪除 {code}：它仍是集團成員。請先在集團區移除，再刪代碼。"
    ) from exc
```

⚠ **不得改用「先 SELECT 檢查再刪」**取代外鍵。那有兩個問題：
檢查到刪除之間有競態；而且新端點會忘記加。約束沒有這兩個問題。

## §5 工作量集中在 4a（掃描）

已知有兩處會動代碼（`delete`、`promote`），但**尚未掃過全庫**。
可能還有匯入、代碼區重建、AI 建議確認等路徑。

⚠ 4a 掃完才知道 4b 要改幾處，因此**不預估工時**。動工第一件事是掃描並回報範圍。

## §6 驗收判準

| 層 | 判準 | 反向驗證 |
|---|---|---|
| DB | `companies` 筆數＝`SELECT DISTINCT 申請人代碼` | — |
| DB | 直接 SQL 刪除集團中的代碼 | **被 DB 擋** |
| DB | `UPDATE companies` 換碼 | 別稱與集團成員**自動跟著換** |
| API | 刪集團中的代碼 | 回 **409**＋可讀中文，不是 500 |
| API | `promote` 目標已存在 | 409，訊息說明合併請走代碼區 |
| 前端 | 顯示 409 | 使用者知道下一步 |
| 結構 | 集團孤兒恆為 0 | **由外鍵保證，不需要測試** |
| Migration | downgrade 後回到原狀 | — |

最後兩列是本 change 的核心價值：孤兒檢查從「要寫測試」變成「不可能發生」。

## §7 不做什麼

- 不搬名稱欄位進實體表
- 不合併既有重複代碼
- 不改「一家公司多代碼」的結構
- 不處理別稱唯一性（前一個 change 負責）
