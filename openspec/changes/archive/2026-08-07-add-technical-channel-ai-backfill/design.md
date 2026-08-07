# Design — add-technical-channel-ai-backfill

## 資料流（三段式）

```
分群完成（初次/增量）
  └─ ① 候選判定（確定性）：is_backfill_candidate(patent, source_field)
       ＝該通道無 embeddings ∧ document_kind != 'S'（唯一定義處，三處共用）
  └─ ② AI 建議（敘述型）：既有 AI 通道（佇列 → Companion → headless CLI → MCP 回存）
       輸入＝文獻備註三級 fallback 文本（PATENT_NOTE_SOURCE_COLUMNS）
             ＋該通道現有主題清單（label＋summary＋key）
       輸出＝每件 {patent_id, suggested_topic_key, reason}，落 analysis_outputs
             （output_type='topic_backfill_suggestion'，帶 ai_model／prompt_version）
       guard＝suggested_topic_key 不在現有主題清單 → 標 invalid＋原因，不得靜默丟棄
  └─ ③ 批次核准（人工定案＋確定性寫入）：
       分類區清單頁 → 逐筆可改選 → 「全部核准」
       → POST 批次寫入 topic_assignments（assigned_source='ai_backfill_approved'）
       → 報表母體自動反映（不重跑分群、不動既有指派）
```

## 關鍵決策

1. **來源標記欄**：`topic_assignments` 現無 assigned_by／source 欄——新增
   `assigned_source text NOT NULL DEFAULT 'geometric'`（migration，含 downgrade）。
   幾何指派不動（default 吸收），核准寫入標 `ai_backfill_approved`。
   報表母體註記由此欄分計（CLU-016）。
2. **建議不建新表**：建議留 `analysis_outputs`（敘述型落點規則），核准清單由
   「候選 ∩ 最新建議 ∖ 已指派」推導——不維護第二份狀態，核准後自然出清單。
3. **AI 任務型別**：沿用 job queue 既有 `ai:*` 模式新增 `ai:topic_backfill`；
   Companion 派工 prompt 由 worker 端組（同 narrative 模式），MCP 回存工具沿用
   analysis_outputs 通用回存。
4. **重跑語意**：任務可重複執行——同 workspace/通道重跑覆蓋舊建議
   （analysis_outputs 取最新版），已核准（已有 assignment）的專利不再列入候選。
5. **不動 schema 紅線**：`topic_key NOT NULL` 維持——建議階段不寫 assignments，
   所以不需要「待定」狀態。

## 母體數字契約

- 技術通道現況 35（CN 27／US 6／EP 2，TW 0）；B 組候選 9（全 TW）；核准後 44。
- 設計案 11（全 CN，`document_kind='S'`）不入候選。
- 報表註記例：「技術通道 44/55 件（其中 9 件為 AI 建議、人工核准）」。

## 邊界

- 前端只消費 API（候選與建議清單、批次核准端點），不自建主題清單或判定邏輯。
- 核准寫入為單一交易；部分失敗全回滾（不得寫一半）。
- 增量分群後新缺口：重跑 ai:topic_backfill 產新建議；已核准者不受影響。
