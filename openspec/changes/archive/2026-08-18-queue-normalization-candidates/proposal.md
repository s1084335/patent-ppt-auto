# Proposal: 正規化候選改為排隊分批查證（queue-normalization-candidates）

## Why

### P1｜查不到證據的候選永遠留在池子裡，每跑一次就再燒一次

`list_company_normalization_candidates` 的定義是「還沒正式歸戶的原始名稱」。
查證失敗**不會改變任何狀態**，所以下一次執行會原封不動再問一遍。

實測（2026-08-18）：

| job | 候選數 | 產出建議 | 耗時 | 每候選 |
|---|---|---|---|---|
| #411 | 17 | 10 | 588s | ~35s |
| #416 | 7 | 1 | 263s | ~38s |

#411 有 7 個沒結論，#416 的候選數正好是 7——第二次跑等於重問了同一批，
花 263 秒換到 1 筆。且現在池子裡多數是自然人（Robert Sitarz、Shi Jiangbin、
Wang Xiangming、William Shaun McCleave…），自然人的集團歸屬**結構性**缺乏公開證據，
不是運氣問題，重問幾次都一樣。

### P2｜一次全查，成本隨資料量單調上升且無上界

現況一個 job 把**所有**候選塞進**一次** CLI 呼叫。以 ~35s／候選估：

- 今天 7 個 → 4 分鐘
- 下一批 WIPS 進來 50 個 → **約 30 分鐘**，而且是單一呼叫

### P3｜單一呼叫的爆炸半徑＝全部

契約錯誤（AI 回傳格式壞掉、指到不存在的 ref）目前是整批硬失敗。
#396／#397 連續兩次 failed、使用者拿到零筆，就是這個形狀。
候選愈多，一次協定失誤損失愈大。

### P4｜分批本身會製造新的缺席型偏差

如果只是改成一次做 20 個而畫面照樣只說「建議已產生」，
使用者會把它讀成「全部查過了」。⚠ 這與 2026-08-18 修掉的「跳過靜默」是同一類錯
（見 `fix-company-alias-conflicts`）：多出來的看得見，沒做的沒人會發現。

## What Changes

1. 新增 `derived_layer.company_normalization_asked`，記下每個候選「被問過」的事實
   （`lookup_key`、時間、run_id、當時的 `patent_count`、結果）。
2. 候選查詢改為**排隊**：沒問過的排在問過的前面（`last_asked_at NULLS FIRST`），
   同層再依 `patent_count DESC`。
3. **重新入列規則**：問過的候選只有在**該名稱又有新專利進來**
   （`patent_count > asked_patent_count`）時才回到隊列。
4. 一個 job 取 **20** 個候選，內部切成 **4 段各 5 個** CLI 呼叫。
5. 一段失敗不影響其他段；**失敗的段不蓋章**，下次會再試，且失敗要顯示。
6. 建議端點帶出 `queue`（剩餘數與組成），前端顯示「還有 N 個沒問過」。
7. 候選送 CLI 前做公開欄位投影，內部 `lookup_key` 不得進入 prompt。

## Capabilities

### Modified Capabilities

- `company-governance`：AI 正規化查證從「一次全查、失敗即重問」
  變成「排隊分批、有新資料才重問」。

## Scope

- `backend/app/derived/company_alias_importer.py`：候選查詢加排隊條件與 `lookup_key` 輸出、
  新增蓋章寫入函式。
- `backend/app/worker/ai_company_normalization_suggestion_runner.py`：分段呼叫、
  逐段錯誤隔離、蓋章、結果統計。
- `backend/app/api/company_aliases.py`：建議端點帶出 `queue`。
- `backend/app/static/index.html`：顯示剩餘數與失敗段。
- 新 migration：建 `company_normalization_asked`。

## Non-goals

- **不做並行**。分段是為了縮小爆炸半徑，不是為了加速；並行會同時放大額度消耗與
  撞週限的風險，等排隊與重問規則穩定後再單獨評估。
- **不做自然人前篩**（原討論的「丙」）。它會誤判且需要可覆寫的介面，
  本輪先用重新入列規則把重複成本降到零，前篩之後再視情況做。
- 不改 CLI 的工具白名單（維持 `WEB_RESEARCH_TOOLS`）。
- 不改建議的驗證規則、證據要求或 `suggestion_kind` 契約。
- 不自動連續跑下一批——批次仍由使用者手動觸發，避免成本失控。

## Impact

- Affected specs: `company-governance`
- Affected code: 見 Scope
- Affected behaviour：
  - 一次 job 最多處理 20 個候選（原本是全部）
  - 查無證據的候選**不會**在下一次執行被重問，除非該名稱有新專利
- Migration: 建一張新表；無外鍵；downgrade 直接刪表
- ⚠ 既有的候選（含歷史上被問過的）在本 change 上線時**都沒有蓋章紀錄**，
  會被視為「沒問過」各排一輪。這是刻意的：舊 run 的結果沒有留存，
  無從得知誰被問過。

## Activation

- `alembic upgrade head` 後即生效，不需重跑 derived refresh。
- 後端與 worker 需重啟（runner 與端點都有變）。
- 前端為 bind mount，`git pull` 後重新整理即可。

## Acceptance Gate

1. **排隊不變式**：沒問過的候選一定排在問過的前面。
2. **不重問**：問過且 `patent_count` 未增加的候選，不會再被送進 CLI。
3. **會重問**：`patent_count` 增加後該候選回到隊列。
4. **分批**：一個 job 最多取 20 個候選，且切成 4 段各 5 個呼叫。
5. **爆炸半徑**：一段契約錯誤不影響其他段；失敗段的候選**不蓋章**，
   下次執行會再被取到。
6. **失敗要現形**：失敗的段數與原因出現在結果與畫面上，不得靜默。
7. **剩餘要現形**：畫面顯示「還有 N 個沒問過」；跑完一批不得讀起來像全部查完。
8. **內部鍵不外洩**：`lookup_key` 不出現在送給 CLI 的 prompt。
9. 範圍回歸全綠。

## Confirmed Decisions

- 2026-08-18（使用者裁決）：重新入列採「**乙**」——只有該名稱又有新專利進來才重問。
  ⚠ 這比「固定 N 天後重問」好在**不需要選一個時間常數**：判斷依據是
  「有沒有新資料值得再查」，而不是「過了多久」。
- 2026-08-18（使用者裁決）：批量 **20**，內部切 **4 段各 5 個**。
- 2026-08-18（使用者裁決）：畫面要顯示「還有 N 個沒問過」。
- 2026-08-18：排序用 `last_asked_at NULLS FIRST`。⚠ 這一個子句就是
  「全部輪過一遍才會有人被問第二次」的全部實作——不需要額外的輪次計數器。
- 2026-08-18：**修正**既有決策「契約錯誤整批拒絕」
  （`fix-company-alias-conflicts` 時代，當時一個 job＝一次呼叫）。
  改為「**拒絕該段**，其餘段照常」。原決策的用意是「不得靜默吞掉協定錯誤」，
  該用意由「失敗段必須顯示且不蓋章」承接，未被放寬。

## Open Questions

無。
