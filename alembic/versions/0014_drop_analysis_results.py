"""drop unused derived_layer.analysis_results

Revision ID: 0014_drop_analysis_results
Revises: 0013_company_alias_normalization
Create Date: 2026-07-17

derived_layer.analysis_results 是被取代的早期單表分析設計：0 筆資料、無任何
程式讀寫（全庫僅出現在 DDL 檔）、無 FK/view 依賴。現行分析走 app_layer
的 analysis_runs＋analysis_outputs＋export_runs；案件比對（docs/
infringement_comparison_design.md）另規劃自己的表（claim_comparison_runs／
claim_elements／claim_element_findings／claim_comparison_summary），亦不使用
本表。故安全移除，不影響現有流程。downgrade 忠實重建空表結構與索引。
"""
from __future__ import annotations

from alembic import op


revision = "0014_drop_analysis_results"
down_revision = "0013_company_alias_normalization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """移除未使用的 derived_layer.analysis_results（含其索引，一併 DROP）。"""
    op.execute("DROP TABLE derived_layer.analysis_results;")


def downgrade() -> None:
    """依 0001 baseline 定義重建空表與兩個索引（回復到本 migration 前狀態）。"""
    op.execute(
        """
        CREATE TABLE derived_layer.analysis_results (
            id BIGSERIAL PRIMARY KEY,
            analysis_name TEXT NOT NULL,
            analysis_type TEXT NOT NULL,
            filter_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            family_dedup_mode TEXT NOT NULL DEFAULT 'none',
            patent_set_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            comparison_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_analysis_results_analysis_type "
        "ON derived_layer.analysis_results(analysis_type);"
    )
    op.execute(
        "CREATE INDEX idx_analysis_results_status "
        "ON derived_layer.analysis_results(status);"
    )
