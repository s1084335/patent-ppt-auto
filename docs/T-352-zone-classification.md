# T-352 Zone 分類方法 — 借鏡參考（專利分類用）

> 來源：`.pm/tasks/T-352.md` + `Toolbox/classify_zones.py`
> 整理日期：2026-06-29
> 用途：把 Uniflow PM 的 zone 分類機制，抽象成可套用到「專利分類」的方法論

---

## 0. 一句話總結

**「便宜的規則先分，分不掉的才丟 AI；分類樹不是一次定死，而是隨資料量成長自動偵測該拆/該新增。」**

三個獨立但互補的機制：
1. **兩階段分類**（規則 lookup → AI fallback）— 控成本
2. **密度驅動拆分偵測**（max_density heuristic）— 防止單一類別過載
3. **跨類別主題發現**（discovery）— 發現分散在多類別的隱藏共同主題

---

## 1. 為什麼這樣設計（決策考量）

| 問題 | T-352 的選擇 | 理由 |
|------|------------|------|
| 規模不大（300+ items） | **max_density + LLM 異質性判斷**，而非 embedding + clustering | 幾百筆規模太小，clustering 不穩定也不划算 |
| 分類成本 | **規則先行，AI 只接規則漏網的** | 大部分 item 用關鍵字就能命中，AI 只處理少數疑難 → 省 token |
| 分類樹會不會過時 | **不一次定死，靠密度偵測自然演化** | 0~50 筆：大分類即可；51~100：部分要細分；100+：歷史錯分累積，需全量重分 + 拆分偵測 |
| 要不要階層分類（A::B::C） | **保持扁平，但允許拆分成多個獨立類別** | 階層會大幅增加渲染/篩選複雜度；扁平 + 拆分更實際 |
| 拆分要不要自動套用 | **只給建議，人工確認才套用** | 分類樹結構變動風險高，保留人工把關 |

> 研究參考：TaxoAdapt（max_density 閾值 + LLM depth/width expansion）、TaxonomySynthesis（TreeNode + Classifier）、GitLab Scoped Labels、Linear Label Groups。

---

## 2. 核心機制細節

### 2.1 兩階段分類（成本控制的關鍵）

```
候選 item ──> Phase 1: 規則 lookup ──命中──> 完成
                   │
                未命中
                   │
                   v
              Phase 2: AI fallback (LLM) ──> 完成 / 仍 pending
```

**Phase 1 — 規則 lookup（`derive_zone`）**
- 先用「**類別 key 直接出現在 tags/關鍵字字串**」匹配，長 key 優先（避免 partial match 誤判）
- 再用 **alias 別名表**（每個類別在 `zones.json` 裡帶一組 `aliases` 關鍵字）
- 都沒命中 → 回 `None`，丟給 Phase 2

**Phase 2 — AI fallback（`_ai_classify`）**
- 只收集 Phase 1 失敗的 item，**批次**送 LLM（一次一批，不是一筆一呼叫）
- prompt 餵：合法類別清單（key: label）+ 每筆的 title / 需求前 80 字 / tags
- 強制 **JSON schema 結構化輸出**：`[{id, zone}]`
- **防呆**：AI 回的 zone 必須在合法 key 集合內才採用，否則仍算 pending
- 不確定時 → 標 `unclassified`，**絕不亂塞預設類別**（T-352 的原則：禁止 fallback 到 infra）

> 套到專利：Phase 1 = IPC/CPC 代碼前綴、技術關鍵字命中既有分類；Phase 2 = 讀專利標題 + 摘要 + 申請項，由 LLM 判斷最適分類。

### 2.2 密度驅動拆分偵測（`_detect_splits`）

```python
MAX_DENSITY = 20   # 單一類別超過此值就送檢查
```

- 統計每個類別的 item 數
- 對 **超過閾值** 的類別：把該類所有 item 的 title 清單送 LLM，問
  「這些是否屬於同一領域？若異質性高，建議拆成哪 2~4 個子分類？」
- 回傳 `{should_split, reason, suggested_zones:[{key,label}]}`
- **只回建議，不自動改分類樹**

> 套到專利：某技術領域累積太多專利時，自動提示「該不該細分成子技術領域」。閾值依你的專利量調整（T-352 用 20 是因為它的最大類別已破 90）。

### 2.3 跨類別主題發現（`_discover_zones`）

- 與「拆分」相反：拆分是「一個類別太大該切開」，發現是「**一個主題被分散在多個類別**，該獨立成新類別」
- 餵 LLM：各類別分布摘要（key/label/count/前 5 筆樣本標題）+ top 50 高頻 tag
- 規則：跨類別 ≥3 筆共同主題才建議、不建議已存在類別、不做拆分
- **成本路由**：item > 30 筆走較便宜的批次模型，否則走 CLI 小模型

> 套到專利：發現「明明該獨立成一個技術主軸，卻散落在數個既有分類」的隱藏領域。

---

## 3. 資料結構（`zones.json` schema）

```json
{
  "version": "1.0",
  "zones": [
    { "key": "unclassified", "label": "Unclassified", "order": 0 },
    { "key": "engine", "label": "引擎層", "aliases": ["gemini-cli","codex","claude-code"], "order": 1 }
  ]
}
```

- `key`：機器用、kebab-case、唯一
- `label`：人看的顯示名（可中可英）
- `aliases`：Phase 1 規則 lookup 用的關鍵字
- `order`：UI 排序
- **合法類別集合 = `zones.json` 的唯一真相**；分類器讀它、AI 也只能回它裡面的 key

---

## 4. 執行模式（CLI flags）

| 指令 | 行為 |
|------|------|
| `python classify_zones.py` | 只分「未分類」的（增量） |
| `--all` | 全量重新分類（覆寫既有，修歷史錯分） |
| `--all --ai` | 全量 + LLM 接規則漏網的 |
| `--detect-split` | 分類後跑拆分偵測（隨 `--ai` 啟用） |
| `--discover` | 分析整體分布，提議新增類別 |

輸出統一是一包 JSON：`{updated, ai_classified, pending, item_count, details, still_pending, split_suggestions, discovery}`。

---

## 5. 套用到專利分類的對照建議

| Uniflow 概念 | 專利分類對應 |
|-------------|------------|
| item（task） | 一件專利 |
| tags | IPC/CPC 代碼、申請人、技術關鍵字 |
| zone | 你自定的技術分類 / 產品線 / 佈局象限 |
| title + requirement 片段 | 專利標題 + 摘要 + 獨立項 |
| Phase 1 規則 lookup | IPC/CPC 前綴、關鍵字命中既有分類 |
| Phase 2 AI fallback | LLM 讀摘要判斷分類 |
| max_density 拆分偵測 | 技術領域過載 → 提示細分 |
| discovery | 發現分散的隱藏技術主軸 |

**踩坑提醒（T-352 執行日誌實證）**：
1. **編碼**：Windows stdout 預設 cp950，輸出含 emoji/中文要小心；用 ASCII escape 或明確 UTF-8。
2. **AI 呼叫路徑**：原本想用 SDK + API key，實測改走 CLI headless + `--json-schema` 更穩、免管 key（依你的環境取捨）。
3. **批次而非逐筆**：AI 一定要批次處理，逐筆呼叫成本與延遲都爆。
4. **結構化輸出 + 合法值過濾**：AI 回的分類一定要用合法集合過濾，擋幻覺。

---

## 6. 移植時最小可行版本（建議起手）

1. 定義 `zones.json`（類別 key/label/aliases）
2. 寫 `derive_zone()` 規則匹配（先吃掉 70~80% 簡單案例）
3. 規則漏網的批次丟 LLM + JSON schema + 合法值過濾
4. 跑量起來後再加 `_detect_splits`（閾值依量決定）與 `_discover_zones`
5. 拆分/新增一律「建議 → 人工確認」，不自動改樹
