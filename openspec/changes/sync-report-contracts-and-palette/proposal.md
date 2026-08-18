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
7. **版型庫收斂**（2026-08-18 追加）：版型清單定唯一定義處並加三處同步閘門；
   補上結論頁的三個閘門破口與涵蓋率對帳；表格抽成參數化通用版型；
   路線圖頁併入結論頁並移除期程。
8. **引擎供給外部訊號**：`cluster_topic_table` 加法律狀態分解，供結論頁排序。

### 為什麼版型庫進輪一

原本規劃把版型留給輪二。2026-08-18 實查後改判：

- 版型庫的問題**全是機械型**——三份清單不一致、閘門缺口、繪製能力綁死在單頁。
  零自由度，與輪二那些「有自由度、需目視兜底」的閘門是不同性質，混在同一輪
  會讓輪二一次壓上太多條，重演 v5／v7／v9 的形式鎖。
- 而且它與本 change 已在做的事同源：`conclusions` 有畫法有閘門卻不在範本裡，
  和「母體沒接」「文件指向不存在的頁」是同一種病——**能力在、守門在，
  中間那段沒接上**。

## Scope

- 引擎：`chart_runner.py`、`report_definitions.py`、`content_blocks.py`、
  `transforms/patent_kind.py`（母體閘門、封面數字、法律狀態分解、用詞、色票）
- 提示文件：`backend/app/worker/prompts/*.md`
- deck skill：`deck_layout.py`（版型清單、表格版型、結論頁）、`check_content.py`
  （三個破口、涵蓋率對帳、三處同步閘門）、`references/content-template.json`、
  `references/narrative.md`
- 文件回寫：`add-deck-delivery-line/design.md` 的頁面盤點

## Activation

- 引擎變更需重啟 backend 與 worker；報表要**重新產製**才會反映
  （既有版本的 `report_data.json` 不會回填）。
- deck skill 是檔案，隨 repo 部署；下一次跑 deck 即生效。
- 提示文件由 CLI 讀取，同上。
- 無 migration。

## Non-Goals

- **不做有自由度的內容閘門**——依據層級、專利行動的受控對象、表格的適用情境
  全部留 `deepen-deck-evidence-layer`（輪二），且輪二內部一次只上一項、
  每項後跑目視。
- **不把「本公司」做成一級概念**。它可以是資料驅動的（給定代碼後只是篩選、
  無推論），但它算的是「我方在這個 workspace 裡的持有」而非「在這個技術領域的
  持有」——匯入母體若沒刻意涵蓋我方組合，數字就是錯的且不會報錯。
  ⚠ 要做的前提是先有母體對帳行（「我方 N 件／庫內共 M 件」），
  2026-08-18 使用者裁決延後。
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

## Acceptance Gate

1. **母體**：`fetch_patent_kind_summary` 與受理局家族註記在兩個 workspace 產出
   **不相等**的數字，且各自等於該 workspace 的實際母體（滑雪機 55／48；
   割草機 226／163）。⚠ 只驗「沒報錯」不算——要比對數字。
2. **母體閘門反向驗證**：把已修好的 `patent_ids` 拿掉，閘門要紅。
3. **文件契約**：`report_key` 集合對帳閘門綠；塞一個假 key 要紅。
4. **色票**：`chart_runner` 裸 hex 字面數為 0（常數定義區除外）；兩套深藍收斂。
5. **版型庫**：`LAYOUTS` 三處同步閘門綠；`conclusions` **實際產得出那一頁**
   （不是「閘門沒紅」）。
6. **結論頁**：缺 `conclusions` 要紅；主題名不在 `topic_facts` 要紅；
   缺涵蓋率對帳要紅；刪一列不更新對帳要紅。
7. **路線圖**：content 內不再出現 `roadmap*`；結論頁依動詞分組、依他人審查中件數排序。
8. **表格版型**：欄數與資料欄位數不符要紅；欄寬總和超寬要紅。
9. 三層範圍回歸全綠 ＋ 符號反查消費者。
10. 兩個 workspace 各產一份報表，**全部頁面目視**（不抽樣）。

## Confirmed Decisions（2026-08-18 追加）

- **期程欄整個拿掉**。`短期 0–3 個月`／`中期`／`長期` 是全份唯一沒有資料支撐的
  欄位——系統不知道人力、預算與產品排程。排序改由**外部訊號**（該主題的他人
  審查中件數）決定：那是對手給的時間壓力，可查證。
- **路線圖頁併入結論頁**：拿掉期程後兩頁功能重疊，合併為依 `ACTION_VERBS`
  分組的行動盤，少一頁也少一份要維護的一致性。
- **表格版型參數化欄數與欄寬**（而非沿用固定四欄）：逐家時序表、主題×年矩陣
  這類需求五欄以上，固定四欄等於馬上要再改一次版型。
- **兩輪維持主題分界**，但輪二內部再分小批：有自由度的閘門一次只上一項，
  每項後跑一次目視迴圈。⚠ 同時上兩項，出問題時分不出是哪一條逼的——
  v5→v9 花了三個版本才找到根因就是這個原因。
- **結論頁用涵蓋率對帳，不用最小列數**。規定列數是形式鎖，會逼出硬湊；
  涵蓋率只要求「沒寫的要現形」，把缺席型偏差轉成可見清單。

## Open Questions

無。用詞（設計）、家族數落點（封面數字磚＋解讀）、封面格式（四格，專利類型一格三數字）、
期程／路線圖／表格版型均已於 2026-08-18 由使用者裁決。
