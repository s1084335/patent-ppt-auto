# DB Index Governance

更新：2026-08-17
範圍：Supabase/PostgreSQL 正式資料庫、backend API、worker、MCP 取證查詢。

## 原則

- 每個新增索引必須對應到實際查詢路徑，不為猜測加索引。
- 本輪只新增 `derived_layer.patent_search_terms` 必要索引；本輪不刪除既有索引。
- 既有索引只標記 `uses_existing_index`、`observe`、`no_index_rationale` 或 `needs_new_index`，移除候選另開 change。
- 多值欄位搜尋一律先進 `derived_layer.patent_search_terms`，不在 API 或 MCP 端複製欄位清單。

## 新增搜尋層索引

| Object | Index | 用途 | Status |
|---|---|---|---|
| `derived_layer.patent_search_terms` | `uq_patent_search_terms_patent_field_term` | 去重與 `ON CONFLICT` idempotent refresh | `needs_new_index` |
| `derived_layer.patent_search_terms` | `idx_patent_search_terms_patent_id` | 回 join patent row、topic/workspace 清單合併 | `needs_new_index` |
| `derived_layer.patent_search_terms` | `idx_patent_search_terms_field_lookup` | exact/field-scoped lookup | `needs_new_index` |
| `derived_layer.patent_search_terms` | `idx_patent_search_terms_lookup_trgm` | `%keyword%` trigram search | `needs_new_index` |

## API / MCP Hot Paths

| Hot path | 查詢行為 | 期望索引 | Status | 備註 |
|---|---|---|---|---|
| `GET /api/v1/patents` | 分頁全庫專利；keyword 走 `patent_search_terms` | `idx_patent_search_terms_lookup_trgm`, `idx_patent_search_terms_patent_id` | `needs_new_index` | 回原 patent row，不攤開 terms |
| `GET /api/v1/workspaces/{workspace_id}/patents` | workspace 成員分頁；keyword 同一 predicate | `idx_patent_search_terms_lookup_trgm`, `idx_patent_search_terms_patent_id` | `needs_new_index` | workspace 成員仍先由 `patent_ids_json` 展開 |
| `GET /api/v1/workspaces/{workspace_id}/topics/{topic_key}/patents` | topic 指派 patent ids 交集；可帶 keyword | `idx_patent_search_terms_lookup_trgm`, `idx_patent_search_terms_patent_id` | `needs_new_index` | topic id 來源仍是 `derived_layer.topic_assignments` |
| `MCP query_database` | 自主取證的自由 SQL | `patent_search_terms` search route | `needs_new_index` | 指引禁止多欄 ILIKE 掃 core 大表 |
| `workflow_runs` | job claim、status list、workspace task list | 既有 job/status/workspace 類索引 | `observe` | 本輪不新增，需以 Supabase `pg_indexes` 實查補齊 |
| `report_artifacts` | report/latest、artifact lookup、版本輸出 | 既有 version/name lookup | `observe` | 本輪不新增，待 `EXPLAIN` 確認熱路徑 |
| `company_aliases` | 公司正規化 confirmed alias/code lookup | confirmed alias/code lookup indexes | `uses_existing_index` | 0050 前後已加入集團正規化索引；本輪不改 |
| `company_groups` | 集團 confirmed name lookup | `uq_company_groups_confirmed_name` | `uses_existing_index` | 0050 建立 |
| `import_blobs` | 匯入 blob cleanup、終結態清理 | cleanup/status/time 索引待盤點 | `observe` | 後續 object storage/retention change 再決定 |

## Supabase Inventory SQL

部署後在 DBeaver 或 Supabase SQL editor 執行：

```sql
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname IN ('core_layer', 'derived_layer', 'app_layer')
ORDER BY schemaname, tablename, indexname;
```

確認 search terms：

```sql
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'derived_layer'
  AND tablename = 'patent_search_terms'
ORDER BY indexname;
```

## Representative EXPLAIN

參與者／分類碼搜尋：

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT DISTINCT st.patent_id
FROM derived_layer.patent_search_terms st
WHERE st.term_lookup LIKE '%briggs%'
ORDER BY st.patent_id
LIMIT 200;
```

workspace 搜尋交集：

```sql
EXPLAIN (ANALYZE, BUFFERS)
WITH ws_patents AS (
  SELECT (m.pid)::bigint AS patent_id
  FROM app_layer.workspaces w
  JOIN LATERAL jsonb_array_elements(w.patent_ids_json) AS m(pid) ON TRUE
  WHERE w.workspace_id = 1
)
SELECT wp.patent_id
FROM ws_patents wp
WHERE EXISTS (
  SELECT 1
  FROM derived_layer.patent_search_terms st
  WHERE st.patent_id = wp.patent_id
    AND st.term_lookup LIKE '%owner b%'
)
ORDER BY wp.patent_id
LIMIT 50;
```

MCP evidence 查詢應沿用同一條路徑，先拿 `patent_id` 再 JOIN 詳細表；若查詢目標不是 keyword discovery，
才依實際 evidence route 使用 `report_artifacts`、`topic_assignments`、`workflow_runs` 等既有索引。

## Pending Live Evidence

- `pg_trgm` extension 是否存在：待 Supabase migration 後確認。
- `EXPLAIN` 是否使用 `idx_patent_search_terms_lookup_trgm`：待正式資料量下確認；小資料量 planner 可能選擇 seq scan，須記錄原因。
- `workflow_runs`、`report_artifacts`、`import_blobs` 的實際索引與熱路徑：本輪先盤點入口與狀態，不刪既有索引。
