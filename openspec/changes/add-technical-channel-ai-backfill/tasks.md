# Tasks — add-technical-channel-ai-backfill

## 1. 開工閘門與基線

- [ ] 1.1 獨立分支（feat/tech-channel-ai-backfill，已建）；記錄 HEAD、Alembic current/head；跑範圍測試基線並保存既有失敗。

## 2. Slice A：候選判定與來源標記（CLU-013）

- [ ] 2.1 Red：is_backfill_candidate 契約測試——B 組入選、設計案排除（斷言不可用 patent_type）、有 embeddings 者排除、已核准者排除；唯一定義處（呼叫端無第二份條件）。
- [ ] 2.2 Green：實作候選判定（clustering 模組，document_kind 判定共用既有 transforms/patent_kind）。
- [ ] 2.3 Red：migration 契約測試——topic_assignments 加 assigned_source（NOT NULL DEFAULT 'geometric'）、既有列不變、downgrade 移除欄。
- [ ] 2.4 Green：Alembic migration 0048。

## 3. Slice B：AI 建議任務（CLU-014）

- [ ] 3.1 Red：prompt 組裝測試——輸入含文獻備註 fallback 文本＋現有主題清單；輸出契約（patent_id／suggested_topic_key／reason）；清單外主題標 invalid 現形。
- [ ] 3.2 Green：worker 新增 ai:topic_backfill 任務（沿用佇列→Companion→CLI→MCP 回存架構），建議落 analysis_outputs（output_type='topic_backfill_suggestion'）。
- [ ] 3.3 Red/Green：重跑語意——同通道重跑取最新建議、已指派者不列候選。

## 4. Slice C：批次核准與正式指派（CLU-015、CLU-016）

- [ ] 4.1 Red：核准 API 契約——批次寫入單一交易、assigned_source='ai_backfill_approved'、逐筆改選值以請求為準、清單外主題拒絕、不觸發分群工作、既有指派不變。
- [ ] 4.2 Green：核准端點＋確定性寫入。
- [ ] 4.3 Red/Green：報表母體反映——核准後技術通道母體含補分件、註記分計幾何/AI 核准件數；核准前建議不進統計。
- [ ] 4.4 前端：分類區建議清單（列建議＋理由＋下拉改選＋全部核准）、核准後清單清空；invalid 建議呈現不可核准。

## 5. 組合驗收

- [ ] 5.1 OpenSpec strict validation、目標測試、範圍回歸、verify_module。
- [ ] 5.2 真資料端到端：滑雪機 workspace 產 9 件建議（全 TW、無設計案）；批次核准後 topic_assignments 9 筆帶標記；技術通道母體 35→44；前端實物截圖。
- [ ] 5.3 未適用與未執行項分開揭露；使用者確認後才 archive。
