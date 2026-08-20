# deck 交付線（已停產）

2026-08-20 使用者定案：**PPT／簡報停產，HTML 報表為唯一交付物**。
deck 交付線（`skills/html-report-to-deck/` ＋ 後端 runner／API）於 2026-08-21
自主線移除，本目錄保存可再利用的文件與抓回的方法。

⚠ 這是 PPT 交付線退場的**第二段**：2026-08-10 拔的是舊 PPT 線
（見 `../ppt-delivery-line/`），deck 是後來另起、想取代它的新線，同樣停產。

## 退場歷程

| 日期 | 事件 |
|---|---|
| 2026-08-13 起 | `add-deck-delivery-line`（30/35）、`deepen-deck-evidence-layer`（28/40）開發中 |
| 2026-08-20 | 使用者定案停產；分支封存為 tag 後刪除，**但當時整條線未進主線** |
| 2026-08-21 | 報表引擎線合併回主線時 deck 被一併帶回；依授權自主線移除（本次） |

## 怎麼抓回來

🔴 **從 `8c0c19a` 抓，不要從舊分支 tag 抓。**

`8c0c19a` 是 deck 與**合併後的報表引擎**唯一並存的 commit。從舊分支 tag
（`archive/2026-08-20/add-deck-delivery-line` 等）抓的話，那是 139 個 commit 之前
的引擎狀態，要重做一次合併才能用。

```bash
# 整條線一次抓回（59 個檔）
git checkout archive/2026-08-21/deck-line-at-merge -- \
    skills/html-report-to-deck \
    backend/app/api/deck_exports.py \
    backend/app/worker/ai_report_deck_runner.py

# 測試（20 個檔）
git checkout archive/2026-08-21/deck-line-at-merge -- \
    tests/test_deck_*.py tests/test_api_deck_exports.py \
    tests/test_report_deck_runner.py tests/test_deepen_deck_evidence_layer.py
```

⚠ 抓回程式碼**不等於接得回去**，還要復原三處接線（本次一併移除）：

| 檔案 | 移除的內容 |
|---|---|
| `backend/app/main.py` | `deck_exports` import 與 `include_router` |
| `backend/app/worker/ai_bridge.py` | `_run_ai_report_deck_job` 與 `ai:report_deck` 註冊 |
| `backend/app/db/job_repository.py` | job type 白名單的 `ai:report_deck` |
| `backend/app/static/index.html` | `RESOURCE_REFRESHERS['deckExports']`、SSE 表的 `ai:report_deck` |
| `tests/test_cli_gateway.py` | 最小權限政策表的 `ai:report_deck` |

⚠ **前端那一處是致命的**：`RESOURCE_REFRESHERS` 是 `const` 物件字面量、載入時即
求值，只刪後端不刪它會 `ReferenceError` 讓整個前端掛掉。移除當天就踩到一次
——`test_frontend_js_syntax` 是綠的（未定義參照不是語法錯），只有真的開頁面才現形。

## 本目錄保存的文件

| 檔案 | 為什麼留在主線可見處 |
|---|---|
| `SKILL.md` | deck 的完整 Runbook；要復活時的起點 |
| `narrative.md` | **敘事品質標準**——其中 16 項是 HTML 線的 `content_standard.md` 沒有的（措辭紀律／斷言強度、決策語言與十種圖表判讀對照、假空白、聯集能不能算、量詞、階段用技術語言、名詞口徑、末頁邊界說明…）。這些**與載體無關**，評估回收進 HTML 線時直接讀這份，不必翻 tag |
| `pitfalls.md` | 版面與組版踩過的坑；若日後另做任何簡報輸出仍適用 |

其餘（scripts 17 個、regression_baseline 8 個、references/content-template.json）
只在 tag 內，不佔主線。

## 規格封存

- `openspec/changes/archive/2026-08-21-add-deck-delivery-line/`（30/35 未完成）
- `openspec/changes/archive/2026-08-21-deepen-deck-evidence-layer/`（28/40 未完成）

⚠ 兩案都是**未完成即封存**，不是驗收通過。復活時 tasks 未打勾的部分仍未做。

## 移除時的測試處置

刪掉 skill 後有 27 個測試檔會斷（它們從 `skills/` 載 `deck_layout`）。逐檔實跑分辨後：

- **19 檔整檔都是 deck** → 刪（`test_deck_*`、`test_conclusions_*`、`test_svg_*` 等）
- **8 檔混著引擎側覆蓋** → 只外科式移除 deck 的 15 支，**保住 60 支引擎測試**
- `tests/test_export_page_cleanup.py` → 刪：整份前提是「清空匯出頁、保留給 deck
  進駐」，deck 停產且使用者確認匯出報告區塊不再開發，前提已不存在

⚠ 廢棄目錄內檔案不得被正式後端、正式測試或部署流程 import。
若要恢復，先提出原因、影響範圍與驗證方式，再移回正式模組。
