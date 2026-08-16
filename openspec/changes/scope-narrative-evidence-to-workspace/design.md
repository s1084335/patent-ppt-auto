# Design

## Scope Source

The report version is the authority for narrative scope. `run_narrative()` reads `report_data.json.parameters.workspace_id` after resolving `based_on_version`.

## Enforcement

During the CLI call, `narrative_report_scope()` sets:

- `PATENT_REPORT_WORKSPACE_ID`
- `PATENT_REPORT_SNAPSHOT_ID`

The MCP server reads these values. If no workspace scope is active, `query_database()` remains the existing read-only SQL tool. If a workspace scope is active, `query_database()`:

- rejects aggregate SQL such as `COUNT`, `SUM`, `GROUP BY`, and window functions;
- requires the query/result to expose `patent_id` or `id`;
- loads allowed patent ids from `app_layer.workspaces.patent_ids_json`;
- returns only rows whose patent identity belongs to the workspace.

Typed snapshot tools remain the preferred path for report-level aggregate claims.

## Limits

This is a row-level evidence gate. Raw SQL in narrative scope is intentionally constrained; the CLI should use `query_report_evidence()` for aggregate report statements.
