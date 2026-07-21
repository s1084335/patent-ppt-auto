"""已移出 active catalog 的引用／研發能量報表定義留存。

本檔只供歷史追溯，不應由 production code import。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArchivedReportDefinition:
    """保存舊版 ReportDefinition 需要的欄位，避免依賴 production 類別。"""

    name: str
    report_type: str
    label: str
    label_zh: str
    source_table: str
    columns: tuple[str, ...]
    group_by: tuple[str, ...] = ()
    default_order: tuple[tuple[str, str], ...] = ()
    default_limit: int | None = None
    exclude_blank_columns: tuple[str, ...] = ()
    aggregates: tuple[tuple[str, str, str], ...] = ()


REPORT_SOURCE_TABLE = "derived_layer.report_patent_base"

ARCHIVED_REPORT_DEFINITIONS: dict[str, ArchivedReportDefinition] = {
    # 高被引用專利排名：被引用數（F1，下載當下快照）由高至低。
    "top_cited_patents": ArchivedReportDefinition(
        name="top_cited_patents",
        report_type="detail",
        label="Top Cited Patents",
        label_zh="高被引用專利排名",
        source_table=REPORT_SOURCE_TABLE,
        columns=(
            "patent_id",
            "授權公告號",
            "未審查的公開號(轉換後)",
            "title",
            "application_year",
            "applicant_display_name",
            "(F1)引用文獻數",
        ),
        default_order=(("(F1)引用文獻數", "desc"), ("patent_id", "asc")),
        default_limit=50,
        exclude_blank_columns=("(F1)引用文獻數",),
    ),
    # 企業研發能量：申請量 x 被引用總數 x 發明人數合計。
    "company_rd_energy": ArchivedReportDefinition(
        name="company_rd_energy",
        report_type="aggregate",
        label="Company R&D Energy",
        label_zh="企業研發能量",
        source_table=REPORT_SOURCE_TABLE,
        columns=("applicant_display_name",),
        group_by=("applicant_display_name",),
        aggregates=(
            ("sum", "(F1)引用文獻數", "cited_total"),
            ("sum", "發明人數", "inventor_total"),
            ("count", "(F1)引用文獻數", "cited_rows"),
        ),
        default_order=(("patent_count", "desc"), ("applicant_display_name", "asc")),
        default_limit=30,
        exclude_blank_columns=("applicant_display_name",),
    ),
}
