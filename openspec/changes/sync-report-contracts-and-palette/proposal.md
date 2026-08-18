# Proposal: 報表契約同步與母體閘門（sync-report-contracts-and-palette）

## Why

2026-08-17～18 連續改了七輪報表（受理局狀態堆疊、外觀策略矩陣、技術交叉時序表、
主題演進表、欄位精簡、IPC/CPC 切階層）。改完之後實掃發現三類**不會報錯的失準**：

### 一、報表結構被描述在五個檔，改一次要追五份

| 過期描述 | 出現在 |
|---|---|
| `family_country_layout`（頁已於 08-07 併入受理局） | narrative-flow、deck SKILL、deck narrative.md、deck design |
| 年度矩陣「更多」（08-12 退場） | narrative-flow×2、deck design |
| 年度矩陣形式（圖已改跨度、表已改五欄摘要、已改名） | narrative-flow、deck narrative.md、deck design×2 |
| 現存有效（08-18 已移除） | deck design×2 |
| 技術主題欄（08-17 已移除） | narrative-flow×4、content_standard×2、deck design |
| 主題演進（表已改主題×年） | deck design×3 |

後果：解讀 CLI 照過期指引寫（被要求去區分一個不存在的頁面），deck 照過期盤點產頁。
兩者都不會報錯，只會產出與實際報表對不上的內容。這是 2026-08-18 判定
「deck 這線暫時不能跑」的直接原因。

### 二、母體沒接——同一個 bug 的第二、三個實例

| # | 位置 | 顯示 | 實際（滑雪機 workspace） | 狀態 |
|---|---|---|---|---|
| 1 | 報表引擎母體 | 61 件 | 55 件 | ✅ 2026-08-17 修（`_resolve_workspace_patent_ids`） |
| 2 | 受理局頁家族註記 | **187** | 48 | 🔴 本 change |
| 3 | 封面三分法 `fetch_patent_kind_summary` | **281 件（設計 21）** | 55 件（設計 11） | 🔴 本 change |

第 2 例來自 `family_country_layout`（`supports_patent_ids=False` 的 legacy 快照）；
第 3 例的 SQL 直接沒有 `WHERE`。⚠ **同型錯誤出現三次代表這是系統性的**，
逐次修不如加閘門一次抓完——凡是繞過 `run_report` 自行 `cur.execute` 的彙總都有嫌疑。

### 三、色票失控，且新圖仍在增加

`deepen-deck-evidence-layer` 5b 盤點時 chart_runner 有 28 種硬編碼色，
**2026-08-18 實查已 49 種**；deck 側另有 12 個 `RGBColor`；兩套深藍
（`#00094A` vs `#0B2545`）未收斂。5b.4 明文「不得新增第 29 種」，
而 08-17 新增的 `COLOR_TRANSFERRED` 就是違反實例。

### 四、家族數與專利類型的口徑分散

- 家族數在四處出現（趨勢表欄、KP 表、KP 象限泡泡、受理局註記），其中趨勢表那欄是
  **中間量**，而 `report-narrative-flow.md:295` 早已要求 CLI 自行查同族做
  「真爆發 vs 同族延伸」判讀——表格再放一次既重複又違反「過程不是結論」原則。
- 專利類型三分法在程式裡叫「設計」、在外觀策略頁與封面草稿叫「外觀」，
  同一份知識兩個名字。

## What Changes

1. **文件契約同步**：定義五個檔的權責邊界，逐檔同步四類過期描述，
   並加 `report_key` 集合對帳閘門。
2. **母體閘門**：修復第 2、3 例，並加閘門要求所有直查 DB 的彙總必須吃 `patent_ids`
   或列入明確的「全庫用途」白名單。
3. **封面數字由引擎供給**：件／族／受理局／專利類型三數字，一方產生、一方消費。
4. **色票唯一定義處**：兩套深藍收斂，定唯一來源並加一致性測試。
5. **用詞統一為「設計」**：三分法與外觀保護策略頁一律改稱設計
   （台灣法定用語為「設計專利」）。
6. **趨勢兩表移除家族數欄**（資料保留在 rows 供 CLI 取證）。

## Non-Goals

- **不改任何圖表版型與頁面配置**——版型議題留 `deepen-deck-evidence-layer`（輪二）。
- 不重跑 deck、不消耗 CLI 週用量。
- 不修改 `family_country_layout` 這張 legacy 報表本身（只停止在受理局頁誤用它）；
  它是否退場屬另案。
- 不處理 narrative prompt 的**內容深度**（那是輪二的證據可核議題），
  本 change 只修正「指向不存在的東西」。

## Impact

- Affected specs: `patent-reporting`（報表母體與封面數字契約）、
  `ai-companion`（解讀指引與報表結構同步）
- Affected code: `backend/app/reports/chart_runner.py`、`report_definitions.py`、
  `content_blocks.py`、`transforms/patent_kind.py`、
  `backend/app/worker/prompts/*.md`、`skills/html-report-to-deck/**`
- Affected docs: `openspec/changes/add-deck-delivery-line/design.md`（頁面盤點）

## Open Questions

無。用詞（設計）、家族數落點（封面數字磚＋解讀）、封面格式（四格，專利類型一格三數字）
均已於 2026-08-18 由使用者裁決。
