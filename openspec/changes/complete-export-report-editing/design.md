# Design: 匯出報告編輯與單頁重產

## Context

現行流程已具 `report_data.json`、`narratives.json`、`artifact_manifest.json`、真 PPTX preview、`approval_overrides` 與 `ai:report_ppt` artifact；後續初次輸出會改由 goal-driven `plan_id + slide_id` 識別。缺口集中在跨 refresh 的草稿、歷史版本狀態、theme 同源 HTML，以及單頁候選的比較／核准／回復。

## Goals / Non-Goals

**Goals:** 編輯可持久化、候選不破壞現版、AI scope 只一頁、PPTX 完整 rebuild、歷史可追溯。

**Non-Goals:** 不恢復 CSS 模擬 PPT；不做自由拖曳；不把 layout engine 搬進前端。

## Decisions

### 1. 原始輸出、草稿、核准覆寫三層分離

selected chart bundle／evidence／AI 原文不可變；draft 保存使用者編輯；approved overrides 才進正式 build。三層共用 `workspace_id + based_on_version + plan_id + slide_id + revision_id`，避免動態頁序變動後用頁碼套錯內容。

### 2. 單頁重產產生 candidate revision

request 帶 `plan_id + slide_id`、based-on revision、instruction。AI 只收到該 slide 原有選圖、evidence refs 與內容；不得補入未選圖或跨 snapshot 查詢。deterministic builder 可產候選頁 preview 或完整候選 PPTX，候選未核准前不得取代 latest pointer。

### 3. 樂觀鎖避免舊頁覆蓋新頁

核准時比對 based-on revision；已有人更新則回 conflict，要求重新比較。取消只改候選狀態，不改現行 artifact。

### 4. Theme 由 portable skill 輸出/消費

前端與單頁 HTML 讀 structure/theme metadata，不複製 PPT 常數。跨 Python/JS 不可 import 時，由 builder/endpoint 產生可序列化 token，consumer 只讀。

## Code And Data Boundaries

- API/artifact：report latest/content/PPT download 與 revision endpoints。
- worker：`ai_report_ppt_runner.py` 限定 page scope、建立 candidate、完整 build。
- portable skill：`skills/patent-report-ppt/` theme、builder、validation。
- frontend：歷史、草稿 autosave/explicit save、candidate compare/approve。

## Output And Test Evidence

- revision metadata：workspace、source version、plan/slide identity、chart/evidence identities、status、author、timestamps、hash、previous revision。
- 測試：scope payload、非目標頁不變、conflict、cancel、refresh readback、workspace 隔離、artifact persistence。
- 實物：HTML、原版/候選/核准 PPTX、全頁 PNG、manifest/hash 與逐頁 diff。

## Risks / Trade-offs

- [revision 數增加] → retention policy 分類 candidate/draft/approved，latest/pin 受保護。
- [AI 一頁但 rebuild 影響其他頁或選圖] → 對非目標 slide、chart coverage 與 evidence refs 做結構化／渲染 diff gate。
- [兩套 theme] → producer-generated token + consistency test。
- [跨程序 autosave 競態] → revision/ETag conflict，不採最後寫入者無條件覆蓋。

## Migration Plan

先新增 revision persistence 與唯讀歷史，再接 draft save，再做 theme HTML，最後開 candidate/approve。舊 artifact 以 read-only revision 0 顯示；任何階段可回退到現行整份 PPT 產生，不刪舊輸出。
