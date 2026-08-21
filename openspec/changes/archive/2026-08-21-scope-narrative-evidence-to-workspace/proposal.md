# Scope Narrative Evidence To Workspace

## Why

`ai:narrative` can ask the read-only report research MCP for extra evidence while writing report narratives. Report versions already carry `parameters.workspace_id`, but the narrative execution path did not enforce that scope when the CLI used `query_database`. This could let a narrative cite live database rows outside the workspace that produced the report.

## What Changes

- Read `report_data.json.parameters.workspace_id` before running the narrative CLI.
- Expose the workspace and snapshot scope only for the duration of that CLI run.
- When a narrative workspace scope is active, restrict `query_database` to row-level patent evidence and filter returned rows by the workspace patent ids.
- Keep aggregate claims on existing snapshot/report evidence tools instead of raw live SQL.

## Impact

- Affects narrative CLI evidence gathering and the `patent-report-research` MCP server.
- Does not change report generation, chart rows, clustering, database schema, or workspace creation.
- Existing unscoped administrative/read-only `query_database` behavior is preserved outside narrative workspace scope.
