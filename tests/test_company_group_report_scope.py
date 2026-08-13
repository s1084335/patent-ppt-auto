from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.reports.report_definitions import REPORT_DEFINITIONS
from backend.app.reports.report_engine import build_report_sql


def test_company_scope_keeps_applicant_company_display_name():
    """預設 company scope 不可被集團層改寫，兩個公司仍分開統計。"""
    sql, params = build_report_sql(
        REPORT_DEFINITIONS["applicant_ranking"],
        filters=None,
        limit=10,
        report_scope="company",
    )

    assert '"applicant_display_name" AS "applicant_display_name"' in sql
    assert 'GROUP BY "applicant_display_name"' in sql
    assert "report_patent_applicant_expanded_with_groups" not in sql
    assert params["limit"] == 10


def test_group_scope_groups_by_confirmed_group_display_name():
    """group scope 只在明確指定時使用集團顯示欄位，並保留既有輸出 key。"""
    sql, _ = build_report_sql(
        REPORT_DEFINITIONS["applicant_ranking"],
        filters=None,
        limit=10,
        report_scope="group",
    )

    assert "report_patent_applicant_expanded_with_groups" in sql
    assert '"applicant_group_display_name" AS "applicant_display_name"' in sql
    assert 'GROUP BY "applicant_group_display_name"' in sql


def test_group_scope_maps_display_name_filters_to_group_fields():
    """group scope 下使用既有 filter key 時，SQL 要改查 group 欄位。"""
    sql, params = build_report_sql(
        REPORT_DEFINITIONS["applicant_ranking"],
        filters={"applicant_display_name": {"values": ["創科集團"]}},
        limit=None,
        report_scope="group",
    )

    assert '"applicant_group_display_name" = ANY(%(filter_0)s)' in sql
    assert params["filter_0"] == ["創科集團"]


def test_invalid_report_scope_is_rejected():
    with pytest.raises(ValueError, match="Unsupported report scope"):
        build_report_sql(
            REPORT_DEFINITIONS["applicant_ranking"],
            filters=None,
            limit=None,
            report_scope="workspace",
        )


def test_report_api_and_renderer_carry_scope_contract():
    """API、worker、chart runner 都要保留 report_scope，產圖才會用同一口徑。"""
    api_src = (Path(__file__).resolve().parents[1] / "backend" / "app" / "api" / "reports.py").read_text(encoding="utf-8")
    worker_src = (Path(__file__).resolve().parents[1] / "backend" / "app" / "worker" / "handlers.py").read_text(encoding="utf-8")
    chart_src = (Path(__file__).resolve().parents[1] / "backend" / "app" / "reports" / "chart_runner.py").read_text(encoding="utf-8")

    assert 'Literal["company", "group"]' in api_src
    assert 'payload["report_scope"] = request.report_scope' in api_src
    assert '"report_scope": str(payload.get("report_scope") or "company")' in worker_src
    assert "report_scope=self.report_scope" in chart_src
    assert '"report_scope": report_scope' in chart_src
