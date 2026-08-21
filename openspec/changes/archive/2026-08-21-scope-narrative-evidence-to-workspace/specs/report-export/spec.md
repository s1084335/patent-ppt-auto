## ADDED Requirements

### Requirement: Narrative Evidence Must Be Scoped To Report Workspace

When an AI narrative is generated from a report version that has `parameters.workspace_id`, the system SHALL constrain live database evidence gathering to that workspace.

#### Scenario: CLI receives workspace scope

- **GIVEN** a report version whose `report_data.json` contains `parameters.workspace_id`
- **WHEN** `ai:narrative` runs the CLI to write narratives
- **THEN** the report research MCP context SHALL include that workspace id and the report snapshot id for the duration of the CLI run
- **AND** the scope SHALL be restored after the CLI run completes

#### Scenario: scoped raw SQL is row-level only

- **GIVEN** the narrative CLI is running with a workspace scope
- **WHEN** it calls `query_database`
- **THEN** aggregate SQL SHALL be rejected
- **AND** the query SHALL expose `patent_id` or `id` so results can be filtered
- **AND** rows outside the workspace patent ids SHALL not be returned
