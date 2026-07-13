from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReportDefinition:
    name: str
    report_type: str
    label: str
    source_table: str
    columns: tuple[str, ...]
    group_by: tuple[str, ...] = ()
    count_column: str = "patent_id"
    default_order: tuple[tuple[str, str], ...] = ()
    default_limit: int | None = None
    exclude_blank_columns: tuple[str, ...] = ()


REPORT_SOURCE_TABLE = "derived_layer.report_patent_base"

REPORT_DEFINITIONS: dict[str, ReportDefinition] = {
    "application_trend": ReportDefinition(
        name="application_trend",
        report_type="aggregate",
        label="Patent Application Trend",
        source_table=REPORT_SOURCE_TABLE,
        columns=("application_year",),
        group_by=("application_year",),
        default_order=(("application_year", "asc"),),
        exclude_blank_columns=("application_year",),
    ),
    "publication_trend": ReportDefinition(
        name="publication_trend",
        report_type="aggregate",
        label="Patent Publication Trend",
        source_table=REPORT_SOURCE_TABLE,
        columns=("publication_year",),
        group_by=("publication_year",),
        default_order=(("publication_year", "asc"),),
        exclude_blank_columns=("publication_year",),
    ),
    "country_distribution": ReportDefinition(
        name="country_distribution",
        report_type="aggregate",
        label="Patent Jurisdiction Distribution",
        source_table=REPORT_SOURCE_TABLE,
        columns=("country_code",),
        group_by=("country_code",),
        default_order=(("patent_count", "desc"), ("country_code", "asc")),
        exclude_blank_columns=("country_code",),
    ),
    "ipc_main_distribution": ReportDefinition(
        name="ipc_main_distribution",
        report_type="aggregate",
        label="IPC Classification Distribution",
        source_table=REPORT_SOURCE_TABLE,
        columns=("Curr. IPC(Main)",),
        group_by=("Curr. IPC(Main)",),
        default_order=(("patent_count", "desc"), ("Curr. IPC(Main)", "asc")),
        exclude_blank_columns=("Curr. IPC(Main)",),
    ),
    "cpc_main_distribution": ReportDefinition(
        name="cpc_main_distribution",
        report_type="aggregate",
        label="CPC Classification Distribution",
        source_table=REPORT_SOURCE_TABLE,
        columns=("Curr. CPC(Main)",),
        group_by=("Curr. CPC(Main)",),
        default_order=(("patent_count", "desc"), ("Curr. CPC(Main)", "asc")),
        exclude_blank_columns=("Curr. CPC(Main)",),
    ),
    "applicant_ranking": ReportDefinition(
        name="applicant_ranking",
        report_type="aggregate",
        label="Top Patent Applicants",
        source_table=REPORT_SOURCE_TABLE,
        columns=("applicant_display_name",),
        group_by=("applicant_display_name",),
        default_order=(("patent_count", "desc"), ("applicant_display_name", "asc")),
        default_limit=100,
        exclude_blank_columns=("applicant_display_name",),
    ),
    "owner_ranking": ReportDefinition(
        name="owner_ranking",
        report_type="aggregate",
        label="Current Patent Assignee Ranking",
        source_table=REPORT_SOURCE_TABLE,
        columns=("current_assignee_display_name",),
        group_by=("current_assignee_display_name",),
        default_order=(("patent_count", "desc"), ("current_assignee_display_name", "asc")),
        default_limit=100,
        exclude_blank_columns=("current_assignee_display_name",),
    ),
}


ALLOWED_FILTER_COLUMNS = {
    "patent_id",
    "application_year",
    "publication_year",
    "application_date",
    "country_code",
    "Curr. IPC(Main)",
    "Curr. CPC(Main)",
    "applicant_display_name",
    "current_assignee_display_name",
}
