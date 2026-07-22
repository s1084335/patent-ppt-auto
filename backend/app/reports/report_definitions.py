from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReportDefinition:
    name: str
    report_type: str
    label: str
    # 中文標題：報表輸出（圖表標題、index 章節、PPT）一律用這個；label 保留英文供內部/log 用
    label_zh: str
    source_table: str
    columns: tuple[str, ...]
    group_by: tuple[str, ...] = ()
    count_column: str = "patent_id"
    default_order: tuple[tuple[str, str], ...] = ()
    default_limit: int | None = None
    exclude_blank_columns: tuple[str, ...] = ()
    # 來源表沒有 patent_id 欄（如家族×國家表）時設 False：
    # run_report 收到 patent_ids 會 fail loud，analysis_runner 會跳過此報表。
    supports_patent_ids: bool = True
    allowed_filter_columns: tuple[str, ...] = ()
    # aggregate 型報表的額外聚合欄：(函式, 來源欄, 輸出別名)。
    # 函式白名單見 report_engine.AGGREGATE_FUNCTIONS（sum / count_distinct / avg / max）。
    # 例：(("sum", "(F1)引用文獻數", "cited_total"),) → COALESCE(SUM("(F1)引用文獻數"), 0) AS cited_total
    aggregates: tuple[tuple[str, str, str], ...] = ()


REPORT_SOURCE_TABLE = "derived_layer.report_patent_base"

REPORT_DEFINITIONS: dict[str, ReportDefinition] = {
    "application_trend": ReportDefinition(
        name="application_trend",
        report_type="aggregate",
        label="Patent Application Trend",
        label_zh="專利申請趨勢",
        source_table=REPORT_SOURCE_TABLE,
        columns=("application_year",),
        group_by=("application_year",),
        default_order=(("application_year", "asc"),),
        exclude_blank_columns=("application_year",),
        allowed_filter_columns=(
            "patent_id",
            "application_year",
            "country_code",
            "Curr. IPC(Main)",
            "Curr. CPC(Main)",
            "applicant_display_name",
            "current_assignee_display_name",
        ),
    ),
    "publication_trend": ReportDefinition(
        name="publication_trend",
        report_type="aggregate",
        label="Patent Publication Trend",
        label_zh="專利公告趨勢",
        source_table=REPORT_SOURCE_TABLE,
        columns=("publication_year",),
        group_by=("publication_year",),
        default_order=(("publication_year", "asc"),),
        exclude_blank_columns=("publication_year",),
        allowed_filter_columns=(
            "patent_id",
            "publication_year",
            "country_code",
            "Curr. IPC(Main)",
            "Curr. CPC(Main)",
            "applicant_display_name",
            "current_assignee_display_name",
        ),
    ),
    "country_distribution": ReportDefinition(
        name="country_distribution",
        report_type="aggregate",
        label="Patent Jurisdiction Distribution",
        label_zh="專利受理局分布",
        source_table=REPORT_SOURCE_TABLE,
        columns=("country_code",),
        group_by=("country_code",),
        default_order=(("patent_count", "desc"), ("country_code", "asc")),
        exclude_blank_columns=("country_code",),
    ),
    # 公司×國家交叉表：申請人（100% 覆蓋口徑）×受理局（按件、含死案）。
    # 圖表預設取前 20 大公司；正式流程由使用者給「追蹤公司清單」，以
    # filters {"applicant_display_name": {"values": [...]}} 圈定後出圖與報告。
    "applicant_country_distribution": ReportDefinition(
        name="applicant_country_distribution",
        report_type="aggregate",
        label="Applicant × Jurisdiction Matrix",
        label_zh="公司×國家交叉表",
        source_table=REPORT_SOURCE_TABLE,
        columns=("applicant_display_name", "country_code"),
        group_by=("applicant_display_name", "country_code"),
        default_order=(
            ("patent_count", "desc"),
            ("applicant_display_name", "asc"),
            ("country_code", "asc"),
        ),
        exclude_blank_columns=("applicant_display_name", "country_code"),
    ),
    "ipc_main_distribution": ReportDefinition(
        name="ipc_main_distribution",
        report_type="aggregate",
        label="IPC Classification Distribution",
        label_zh="IPC 主分類分布",
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
        label_zh="CPC 主分類分布",
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
        label_zh="主要申請人排名",
        source_table=REPORT_SOURCE_TABLE,
        columns=("applicant_display_name",),
        group_by=("applicant_display_name",),
        aggregates=(
            # _excl_group：申請人＝最新受讓人（未離手）不算轉讓（2026-07-22 使用者定案）
            ("count_nonblank_excl_group", "recent_assignee_display_name", "recent_assignee_count"),
            ("string_agg_distinct_nonblank_excl_group", "recent_assignee_display_name", "recent_assignee_display_names"),
        ),
        default_order=(("patent_count", "desc"), ("applicant_display_name", "asc")),
        default_limit=100,
        exclude_blank_columns=("applicant_display_name",),
    ),
    "owner_ranking": ReportDefinition(
        name="owner_ranking",
        report_type="aggregate",
        label="Current Patent Assignee Ranking",
        label_zh="現專利權人排名",
        source_table=REPORT_SOURCE_TABLE,
        columns=("current_assignee_display_name",),
        group_by=("current_assignee_display_name",),
        default_order=(("patent_count", "desc"), ("current_assignee_display_name", "asc")),
        default_limit=100,
        exclude_blank_columns=("current_assignee_display_name",),
    ),
    "recent_assignee_ranking": ReportDefinition(
        name="recent_assignee_ranking",
        report_type="aggregate",
        label="Recent Assignee Ranking",
        label_zh="最新受讓人排名",
        source_table=REPORT_SOURCE_TABLE,
        columns=("recent_assignee_display_name",),
        group_by=("recent_assignee_display_name",),
        default_order=(("patent_count", "desc"), ("recent_assignee_display_name", "asc")),
        default_limit=100,
        exclude_blank_columns=("recent_assignee_display_name",),
    ),
    "applicant_year_matrix": ReportDefinition(
        name="applicant_year_matrix",
        report_type="aggregate",
        label="Applicant Year Matrix",
        label_zh="申請人年度專利分布矩陣",
        source_table=REPORT_SOURCE_TABLE,
        columns=("applicant_display_name", "application_year"),
        group_by=("applicant_display_name", "application_year"),
        default_order=(
            ("patent_count", "desc"),
            ("applicant_display_name", "asc"),
            ("application_year", "asc"),
        ),
        exclude_blank_columns=("applicant_display_name", "application_year"),
    ),
    "owner_year_matrix": ReportDefinition(
        name="owner_year_matrix",
        report_type="aggregate",
        label="Owner Year Matrix",
        label_zh="專利權人年度布局矩陣",
        source_table=REPORT_SOURCE_TABLE,
        columns=("current_assignee_display_name", "application_year"),
        group_by=("current_assignee_display_name", "application_year"),
        default_order=(
            ("patent_count", "desc"),
            ("current_assignee_display_name", "asc"),
            ("application_year", "asc"),
        ),
        exclude_blank_columns=("current_assignee_display_name", "application_year"),
    ),
    # 生命週期：年度 × 申請人家數 vs 件數（技術生命週期判讀的標準圖）。
    "lifecycle": ReportDefinition(
        name="lifecycle",
        report_type="aggregate",
        label="Patent Lifecycle",
        label_zh="專利生命週期",
        source_table=REPORT_SOURCE_TABLE,
        columns=("application_year",),
        group_by=("application_year",),
        aggregates=(("count_distinct", "applicant_display_name", "applicant_count"),),
        default_order=(("application_year", "asc"),),
        exclude_blank_columns=("application_year",),
    ),
    # 國家佈局（現有保護口徑）：家族×國家 一列，COUNT(family_id) = 各國家族數。
    # rows 已按 (family_id, country_code) 去重，alias 沿用 patent_count 讓
    # choropleth/render 零改動複用；語意是「家族數」，見 docs/report_field_matrix.md。
    "family_country_layout": ReportDefinition(
        name="family_country_layout",
        report_type="aggregate",
        label="Family Country Layout (Active Protection)",
        label_zh="國家佈局（現有保護）",
        source_table="derived_layer.report_family_country",
        columns=("country_code",),
        group_by=("country_code",),
        count_column="family_id",
        default_order=(("patent_count", "desc"), ("country_code", "asc")),
        exclude_blank_columns=("country_code",),
        supports_patent_ids=False,
    ),
    # 家族品質明細：完整性核對與異常現形（不完整/生效程序進行中/unknown 狀態等）。
    "family_quality_detail": ReportDefinition(
        name="family_quality_detail",
        report_type="detail",
        label="Family Coverage Quality Detail",
        label_zh="家族完整性明細",
        source_table="derived_layer.report_family_quality",
        columns=(
            "family_id",
            "is_surrogate_family",
            "member_rows",
            "expected_counts_raw",
            "family_incomplete",
            "unknown_status_count",
            "pending_status_count",
            "ep_in_transition_count",
            "ep_missing_epc_count",
            "non_country_row_count",
        ),
        default_order=(("family_incomplete", "desc"), ("family_id", "asc")),
        supports_patent_ids=False,
    ),
}


DEFAULT_REPORT_NAMES: tuple[str, ...] = tuple(REPORT_DEFINITIONS)


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
    "recent_assignee_display_name",
}


def allowed_filter_columns_for_report(definition: ReportDefinition) -> set[str]:
    """回傳單一報表可接受的 filter 欄位，供 API 與 report engine 共用。"""
    if definition.allowed_filter_columns:
        return set(definition.allowed_filter_columns)
    return set(ALLOWED_FILTER_COLUMNS)
