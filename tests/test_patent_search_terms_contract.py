"""Search terms 與索引治理契約測試。

本檔先鎖住本 change 的「不可退讓」行為：多值欄位必須拆成可搜尋 term、
查詢層必須共用 search_terms predicate，且治理文件要把 API/MCP 熱路徑列清楚。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = PROJECT_ROOT / "alembic" / "versions" / "0051_patent_search_terms.py"
GOVERNANCE = PROJECT_ROOT / "docs" / "db_index_governance.md"


class SearchTermsStaticContractTests(unittest.TestCase):
    def test_migration_declares_table_extension_and_indexes(self):
        """migration 必須建立 pg_trgm、search terms 表與三類索引。"""
        src = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("CREATE EXTENSION IF NOT EXISTS pg_trgm", src)
        self.assertIn("CREATE TABLE IF NOT EXISTS derived_layer.patent_search_terms", src)
        self.assertIn("uq_patent_search_terms_patent_field_term", src)
        self.assertIn("idx_patent_search_terms_patent_id", src)
        self.assertIn("idx_patent_search_terms_field_lookup", src)
        self.assertIn("idx_patent_search_terms_lookup_trgm", src)
        self.assertIn("gin_trgm_ops", src)

    def test_allowlist_covers_user_searchable_multi_value_fields(self):
        """使用者會拿來查的多值欄位都要在 allowlist，不只申請人。"""
        from backend.app.derived import patent_search_terms as pst

        keys = {field.field_key for field in pst.SEARCH_TERM_FIELDS}
        for expected in (
            "raw_applicant",
            "standardized_applicant",
            "current_owner",
            "standardized_current_owner",
            "recent_assignee",
            "inventor",
            "orig_ipc_all",
            "curr_ipc_all",
            "orig_cpc_all",
            "curr_cpc_all",
        ):
            self.assertIn(expected, keys)

    def test_refresh_sql_splits_pipe_values_and_deduplicates(self):
        from backend.app.derived import patent_search_terms as pst

        sql = pst.REFRESH_PATENT_SEARCH_TERMS_SQL
        self.assertIn(r"regexp_split_to_table", sql)
        self.assertIn(r"\s*\|\s*", sql)
        self.assertIn("ON CONFLICT (patent_id, field_key, term_lookup) DO NOTHING", sql)

    def test_api_layers_use_shared_search_term_predicate(self):
        """全庫與 workspace 搜尋不得各自維護欄位清單。"""
        patent_queries = (PROJECT_ROOT / "backend" / "app" / "app_layer" / "patent_queries.py").read_text(
            encoding="utf-8"
        )
        workspace_queries = (
            PROJECT_ROOT / "backend" / "app" / "app_layer" / "workspace_queries.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "search_terms_exists_sql",
            patent_queries,
        )
        self.assertIn(
            "search_terms_exists_sql",
            workspace_queries,
        )
        self.assertIn("derived_layer.patent_search_terms", patent_queries)

    def test_topic_patents_endpoint_accepts_keyword(self):
        src = (PROJECT_ROOT / "backend" / "app" / "api" / "topics.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("keyword", src)
        self.assertIn("list_topic_patents", src)

    def test_mcp_prompt_prefers_indexed_search_route(self):
        prompt = (
            PROJECT_ROOT / "backend" / "app" / "worker" / "prompts" / "data_access.md"
        ).read_text(encoding="utf-8")
        self.assertIn("derived_layer.patent_search_terms", prompt)
        self.assertIn("不要用", prompt)
        self.assertIn("多欄 ILIKE", prompt)

    def test_governance_lists_api_mcp_hot_paths_and_explain_steps(self):
        text = GOVERNANCE.read_text(encoding="utf-8")
        for phrase in (
            "GET /api/v1/patents",
            "GET /api/v1/workspaces/{workspace_id}/patents",
            "GET /api/v1/workspaces/{workspace_id}/topics/{topic_key}/patents",
            "MCP query_database",
            "workflow_runs",
            "report_artifacts",
            "company_aliases",
            "company_groups",
            "import_blobs",
            "EXPLAIN",
            "pg_indexes",
            "uses_existing_index",
            "needs_new_index",
            "observe",
            "no_index_rationale",
        ):
            self.assertIn(phrase, text)

    def test_governance_does_not_plan_index_deletion(self):
        text = GOVERNANCE.read_text(encoding="utf-8")
        self.assertRegex(text, re.compile(r"本輪不刪除既有索引|不刪既有索引"))


if __name__ == "__main__":
    unittest.main()
