> Runbook 就緒度：缺工具 3 項（refresh_derived_data、save_analysis_narrative、save_market_evidence）；步驟 D 的 `generate_report_ppt` 已有本目錄內建產生器可直接執行。

# 專利報告 PPT 產製流程

## 執行 Runbook

供 CLI 直接執行，不需任何專案背景。原則：引擎數字不可改寫、AI 只產敘事與草稿、每個文案槽過使用者確認才入版。

### 觸發時機

- 使用者要求產出或更新專利分析報告 PPT。
- 使用者要求補齊報告中的敘事解讀、痛點調查或市場章節。

### 前置條件

1. 引擎全量報表已產出（由 Web 平台觸發，非本流程負責）：可取得本次報表版本的輸出目錄，內含 `report_data.json` 與 opportunity_quadrant／pain_point_quadrant／cluster_topic_table 三個圖表 artifact。目錄位置一律以工具回傳為準，不自行猜路徑。
2. Patent MCP server（`patent`）已連線。先呼叫 `get_data_status` 確認連線目標與資料刷新時間；回傳 `warnings` 非空必須先處理完才繼續（常見＝匯入後資料尚未刷新；刷新──待工具：`refresh_derived_data`）。
3. 讀資料規則：圖表與 `report_data.json` 的完整結構化數據**成對讀取**；禁止只看圖寫結論、禁止改用 CSV。

### 可用 MCP 工具

- reporting：
  - `list_reports`：報表目錄與篩選白名單。
  - `run_report_analysis`：同時回數據 rows＋同口徑圖表。`with_charts=false` 快速問數字不落檔；帶 `analysis_id` 綁快照走正式追溯；家族層級報表帶篩選＝先圈家族再回完整佈局，引用時要講明圈定條件。
- clustering（輕量）：`list_workspaces` → `get_workspace_dashboard` → `get_topic_labeling_payload` → `apply_topic_labels`（只更新非人工定案）→ `get_merge_history`；另有 `get_candidate_review_payload`／`apply_candidate_explanations`。重負載（calibrate／finalize／incremental／merge／unmerge）不在 MCP，由 Web 平台觸發。
- **取數鐵律**：報告數字只能來自工具回傳或 `report_data.json`，不得繞過報表定義自行取數；工具沒回的就說沒有。

### 步驟 A｜敘事文案（頁 2/3/4/5/6 解讀）

1. 解讀 prompt 與規則唯一來源＝同套 skill 的 `report-narrative-flow.md`（含 prompt v1、逐報表重點、`narratives.json` 契約與口徑守則），本檔不重複維護。
2. 逐頁讀對應「圖表＋數據」→ 產解讀草稿：每頁一段、附數字依據；不得出現數據中不存在的數字。
3. ⛔ 等使用者確認：彙整成**單一確認清單（單頁 HTML）**一次過稿，不逐頁打斷。
4. 確認後回存──待工具：`save_analysis_narrative`（輸入＝report_key／text／ai_model／prompt_version／based_on_version；未建成前依 `report-narrative-flow.md` 只落 `narratives.json` 檔案）。

### 步驟 B｜痛點輸入（頁 7）

1. 取正式 topic_code 清單（自分群工作區工具回傳，例如 `get_workspace_dashboard`）。
2. 逐主題外部研究（WebSearch）：產業研究／政府法規／公司公開資料／可靠新聞／論壇；優先近兩年，逐年放寬需標年份；付費牆只用公開摘要。
3. 產草稿表：`topic_code × severity(high/medium/low/unknown) × 依據 × 來源 × 日期`；查無資料者誠實標 unknown，不得預填等級。
4. ⛔ 等使用者確認：草稿表以單頁 HTML 逐項開給使用者裁決（可改等級）。
5. 確認版寫入痛點輸入──與市場證據共用同一管線（kind='pain_point'、target＝topic_code，詳見 `market-data-flow.md`；待工具：`save_market_evidence`）→ 重跑痛點矩陣 artifact（工具：`run_report_analysis`）。
6. 未確認前，報表顯示全 unknown「待調查」是既定行為，不得預填。

### 步驟 C｜附錄 2 市場側／附錄 3 市場規模（頁 9/10）

1. ⛔ 等使用者確認市場範圍（產品定義、含排除、地區、基準年、幣別）：以單頁 HTML 範圍確認單呈現；**過此閘門才開始研究**。
2. 依 `market-data-flow.md` 的 Runbook 執行搜集→篩選→摘要→確認；證據逐筆保存 metric／來源／網址／日期／範圍／年／數值／單位／CAGR／可靠性；只對可比較來源取 min–max，不強行平均。
3. 草稿（區域趨勢、客群、Key Players 市場側）→ ⛔ 等使用者確認（單頁 HTML）→ 按 analysis／report version 回存（回存工具見 `market-data-flow.md` Runbook）。
4. 核對：專利數據不得推算市場規模／市占。

### 步驟 D｜PPTX 組裝（deterministic）

本步驟由本目錄內建產生器執行，**不呼叫 AI**：只把已確認的文案與引擎圖表組版。

**D-1 產生確認槽範本**（第一次執行時）：

```
uv run --no-project --with python-pptx --with pymupdf --python 3.12 \
  python <skill 目錄>/scripts/build_ppt.py --init-approvals approvals.json
```

**D-2 填入定稿文案**：把步驟 A／B／C 經使用者確認的文案逐槽填進 `approvals.json`。槽位契約：

```json
{
  "report_version": "<報表版本，須與報表目錄一致>",
  "slots": {
    "cover.title": "封面標題（頁1）",
    "direction.body": "研發方向建議全文（頁2）",
    "trend.narrative": "申請趨勢解讀（頁3）",
    "tech.narrative": "技術分布解讀（頁4）",
    "competitor.narrative": "競爭者佈局解讀（頁5）",
    "opportunity.narrative": "機會四象限解讀（頁6）",
    "pain_point.narrative": "痛點交叉驗證解讀（頁7）",
    "key_players.market": "市場側 Key Players 名單（頁9）",
    "market.scope": "市場範圍定義（頁10）",
    "market.size": "市場規模數字與區間（頁10）"
  }
}
```

留空或未列出的槽＝未確認，該頁自動標「待確認」浮水印；頁 8（附錄1 全分類）為純表無文案槽，永不標浮水印。

**D-3 產生 PPTX**：

```
uv run --no-project --with python-pptx --with pymupdf --python 3.12 \
  python <skill 目錄>/scripts/build_ppt.py \
    --report-dir <報表版本目錄> --approvals approvals.json \
    --output-dir data/report_artifacts/ppt
```

行為契約：
- 輸入＝版型對應（`PAGE_LAYOUT`）＋報表版本目錄（`report_data.json`／`narratives.json`／圖檔）＋確認槽定稿文案。
- 缺任一確認槽即該頁標「待確認」浮水印，**不擋整檔產出**。
- 輸出＝`<report_version>.pptx`；**版本不覆蓋**（同版本重跑產生 `<report_version>_r2.pptx`、`_r3`…）。
- 同時產 `<report_version>.manifest.json`：記 SHA-256、來源報表版本與來源目錄、逐頁槽位填充狀態（`filled_slots`／`missing_slots`／`watermarked`）。
- 缺報表或缺圖檔的頁面以佔位呈現，不中斷產出。

**D-4** ⛔ 等使用者驗收：開啟產出的 PPT 給使用者確認；驗收通過才算頁面定稿。

### 版型對照表（頁面 ↔ 資料來源 ↔ 文案槽）

改版型只改 `scripts/build_ppt.py` 的 `PAGE_LAYOUT`，不動組版邏輯。

| 頁 | 內容 | 資料來源 report_key | 文案槽 |
|---|---|---|---|
| 1 | 封面＋統計時間段 | country_distribution／application_trend／lifecycle | cover.title |
| 2 | 研發方向建議 | cluster_topic_table | direction.body |
| 3 | 申請趨勢 | application_trend／publication_trend | trend.narrative |
| 4 | 技術分布 | cluster_topic_table | tech.narrative |
| 5 | 競爭者佈局 | applicant_country_distribution／applicant_ranking | competitor.narrative |
| 6 | 機會評估四象限 | opportunity_quadrant | opportunity.narrative |
| 7 | 痛點交叉驗證 | pain_point_quadrant | pain_point.narrative |
| 8 | 附錄1 全分類清單 | cluster_topic_table | （無，純表） |
| 9 | 附錄2 Key Players 對照 | applicant_ranking／owner_ranking | key_players.market |
| 10 | 附錄3 市場規模／區域／客群 | （外部證據，無引擎 report_key） | market.scope／market.size |

### 完成判準

- 每個文案槽都經使用者確認才入版；未確認槽在 PPT 中帶「待確認」浮水印。
- PPT 已產出且使用者驗收通過。
- 全部文案回存與 artifact 皆帶版本（append，不覆蓋）。

### 執行核對清單

- [ ] 引擎數字未被改寫；AI 只產敘事與草稿。
- [ ] 報告中每個數字可回溯到工具回傳或 `report_data.json`。
- [ ] AI 不決定正式分類、不算統計、不捏數字；等級／名單／數字類全部過人工閘門。
- [ ] 解讀口徑遵守 `report-narrative-flow.md` 的口徑守則。
- [ ] manifest 的 `missing_slots` 已清空（或未清空部分已向使用者說明為預期）。

## 開發備註

（開發環境細節、設計原因與定案沿革；佈署版只抽取上方 Runbook 區。）

- 版本：第二版，2026-07-23（第一版 2026-07-21）。版型與逐頁分工唯一來源＝`.agents/context/report-requirements.md`「範例 PPT 逐頁盤點」節。
- **目錄化（2026-07-23 使用者定案）**：本 skill 由單檔 `patent-report-ppt-flow.md` 改為目錄 `patent-report-ppt/`，內含 SKILL.md＋`scripts/build_ppt.py`＋`theme.json`，拿到整個目錄即可執行，不只是文件。其餘 5 個 skill 維持扁平 `.md`。
- **可攜性做法**：產生器不 import 主專案任何模組，只依賴 `python-pptx`（組版）與 `pymupdf`（SVG 轉點陣）。因此可用 `uv run --no-project --with ...` 在任何機器執行，與主專案 venv 無關；主專案內執行時 `pyproject.toml` 已含這兩個依賴，可直接 `uv run python <path>`。
- **外觀樣式**：`theme.json` 的配色、字體、字級、版面座標抽自範例 PPT `docs/reference/報告範例/自走式割草機_專利情報整合分析_20260710.pptx`。該範例檔被 `.gitignore` 排除、不進 repo，故**不採「開範例檔當 template」**：其 slide master 只有單一 `DEFAULT` layout、所有元素為絕對定位的 AutoShape，沒有可複用的 placeholder layout，當 template 得不到好處卻換來對一個不可攜檔案的硬相依。抽參數版可攜且改樣式只改 JSON。
  - 觀察到的範例規格：投影片 13.33×7.5 in（16:9）；字體全檔 `Microsoft JhengHei`；主色 `1F5C3D`（深綠）、`14402B`（更深綠，表頭帶）、`E0A23B`（金，強調）、`FDF3DD`（淺金底）、`1C2B22`（墨黑內文）、`8FAA99`（註腳灰綠）、`C24437`（警示紅）；字級 封面標題 37pt／內頁標題 24-25pt／副標 14-16pt／內文 15pt／統計數字 44pt／頁碼 13pt／註腳 9pt；版面 左右邊界 0.5 in、標題 top 0.28 in、副標 top 0.8 in、內容寬 12.33 in、頁碼固定右上 (12.5, 0.28)。
- **大綱來源與範例的差異**：大綱一律照 `report-requirements.md` 盤點表，不照抄範例頁面。已知差異──範例頁 8 標題為「18 項分類技術指標總表」（綁死該案 18 項），本產生器改為通用「附錄1：全分類技術指標總表」，項數由引擎 rows 決定；範例封面統計卡為手寫四格，本產生器改為依實際可得 report_key 動態產生（缺資料則少一格或顯示「資料待補」），不寫死四格。
- **報表卡片對應**：引擎 14 個 report definition／20 個圖表變體並非每個都上 PPT；只有盤點表指名者進版型對照表，其餘留在 Web index 供查。頁 2/4/8 依賴的 `cluster_topic_table` 與頁 6/7 的 quadrant 屬分群側 artifact，目前 `report_data.json` 尚未含這些 key，會走佔位路徑——待分群報表併入後自動生效，不需改程式。
- 開發機報表輸出位置：`output/report_trial_{ts}/`；本機 smoke 驗證用過 `output/report_full_acceptance_20260721_001539/`。
- MCP 工具來自專案 `.mcp.json` 註冊的 `patent` server（stdio）。
- derived 刷新（開發機指令；Runbook 對應待工具 `refresh_derived_data`）：`uv run python -m backend.app.derived.refresh_report_patent_base` → `uv run python -m backend.app.derived.refresh_report_family_country`（於專案根執行）。常見 warnings 原因＝匯入後未刷新 derived。
- MCP 不可用時備援（開發機）：`uv run python -m backend.app.reports.chart_runner --reports <keys>`（同引擎同口徑），修好即回工具路徑。
- 步驟 A 回存落點：以通用 AI 任務回存 `analysis_outputs`（output_type=`ai:narrative`，帶 analysis version；0021 後為 `workflow_outputs`）；Runbook 待工具 `save_analysis_narrative` 即此介面。
- 步驟 D 已實作＝`scripts/build_ppt.py`（deterministic，不呼叫 AI）；Runbook 待工具 `generate_report_ppt` 即其服務化包裝（把 CLI 參數換成 MCP 參數即可，組版邏輯共用）。測試＝主專案 `tests/test_ppt_builder.py`（8 項，以檔案路徑載入本目錄程式，驗證可獨立執行）。
- **SVG→PPT 限制**：`python-pptx` 不吃 SVG，需先轉點陣。採 `pymupdf` 的 `open(svg).get_pixmap(dpi=150)`，主專案既有依賴不需新裝；轉出的 PNG 以「原檔 SHA-256 前 16 碼」為快取鍵存 `<output-dir>/.cache/`，同一張圖只轉一次。限制：PyMuPDF 的 SVG 解析對進階特性（filter、部分 CSS、web font）支援有限，複雜圖可能與瀏覽器渲染有落差；轉換失敗不擋產出，該頁改顯示「（圖檔待產出）」佔位。若日後需像素級一致，再評估 CairoSVG 或引擎直接輸出 PNG。
- 「MCP 工具食譜」為自 patent-report-flow.md 併入、依現況修訂。
- **成品定位（2026-07-16 定案）**：本 skill 是 Patent Toolkit Installer「Patent Skills」元件的內容來源，會隨安裝檔佈署到使用者本機。包裝進 Installer 時抽取 Runbook 區＋整個 `scripts/`／`theme.json`。開發期細節（本機 5433、`uv run` 維運指令、本機輸出路徑）只留本區。
- 每次產製記工作紀錄（當日 work-log）。
