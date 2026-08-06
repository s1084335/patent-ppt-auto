## Why

現行 MiniBatchKMeans 增量流程固定 k，只會移動既有中心，新增資料即使形成明顯新群也長不出新主題。需要改為可依資料自動建立新主題的 Cosine Online DP-Means。

## What Changes

- 以 Cosine Online DP-Means 取代現行增量 MiniBatchKMeans 聚類核心。
- 保留 PCA，降維後重新 L2 normalization。
- 由資料自動推導 lambda，不新增人工調參入口。
- 增量產生的新主題自動排入 `ai:topic_label`。
- 所有正式主題都進主題統計與後續報表。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `clustering-and-topics`：修改正式與增量聚類行為，使新資料可形成新主題。

## Scope

聚類模型、artifact schema、calibrate/finalize/incremental、主題持久化、AI label follow-up 與報表整合。

## Non-goals

- 不改技術／功效兩通道來源欄。
- 不加入任意 topic split。
- 不把 lambda 暴露成使用者必要參數。

## Impact

影響模型 artifact 相容性、run chain、主題 ID／label follow-up、回歸資料與既有人工主題治理。

## Activation

舊 artifact 需明確相容策略或重建；正式套用前需在代表 workspace 重跑 embeddings／分群並確認人工調整保存策略。

## Acceptance Gate

以合成邊界案例與滑雪機代表資料驗證穩定群保持、新群產生、lambda 可重現、label job 建立與報表完整，再由使用者裁決。

