"""分類 migration 與 WIPS 匯入資料的 PostgreSQL 整合測試。

測試寫入全部包在 transaction 內，tearDown 一律 rollback，因此不會留下
workspace、embedding、topic 或 assignment 測試值。若設定
CLUSTERING_DB_EXPECTED_PATENTS，還會檢查目前匯入總筆數與欄位完整性；
CLUSTERING_DB_EXPECTED_SOURCE_FILES 可另外指定來源檔數。
"""

from __future__ import annotations

import os
import unittest
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from alembic.config import Config
from alembic.script import ScriptDirectory

from backend.app.db.connection import get_connection_kwargs


EXPECTED_CLUSTERING_TABLES = {
    "app_layer.workspace_patents",
    "app_layer.workspaces",
    "core_layer.patent_effect_embeddings",
    "core_layer.patent_technical_embeddings",
    "derived_layer.topic_assignments",
    "derived_layer.topic_candidates",
    "derived_layer.topic_runs",
    "derived_layer.topics",
}

EXPECTED_EMBEDDING_COLUMNS = [
    "embedding_id",
    "patent_id",
    "text_hash",
    "embedding_model",
    "model_version",
    "preprocessing_version",
    "embedding_vector",
    "chunk_count",
    "metadata_json",
    "created_at",
]

EXPECTED_PATENT_NUMBER_COLUMNS = [
    "授權公告號",
    "審查的公告號",
    "未審查的公開號",
    "未審查的公開號(轉換後)",
    "申請號",
    "申請號(轉換後)",
    "country_code",
]


class ClusteringDatabaseIntegrationTests(unittest.TestCase):
    """驗證精簡後分類 schema、目前匯入結果與完整 FK 關聯。"""

    def setUp(self) -> None:
        """為每個測試建立獨立 transaction，避免測試值互相污染。"""
        self.conn = psycopg.connect(**get_connection_kwargs(), row_factory=dict_row)

    def tearDown(self) -> None:
        """回滾所有測試寫入並關閉資料庫連線。"""
        self.conn.rollback()
        self.conn.close()

    def test_migration_schema_and_vector_extension(self) -> None:
        """目前 head 的主題表、向量表與相鄰專利號欄位必須存在。"""
        with self.conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            database_revision = cur.fetchone()["version_num"]

            # 後續 migration 可以接在 0004 之後；資料庫應追上目前程式碼的 head，
            # 而不是永遠硬編碼停在 clustering migration 本身。
            script = ScriptDirectory.from_config(Config("alembic.ini"))
            self.assertEqual(database_revision, script.get_current_head())
            self.assertIsNotNone(script.get_revision("0004_clustering_tables"))

            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            self.assertIsNotNone(cur.fetchone())

            cur.execute(
                """
                SELECT table_schema || '.' || table_name AS qualified_name
                FROM information_schema.tables
                WHERE table_schema IN ('core_layer', 'derived_layer', 'app_layer')
                """
            )
            actual_tables = {row["qualified_name"] for row in cur.fetchall()}
            self.assertTrue(EXPECTED_CLUSTERING_TABLES.issubset(actual_tables))
            removed_tables = {
                "derived_layer.topic_model_profiles",
                "derived_layer.topic_model_artifacts",
                "derived_layer.topic_quality_metrics",
                "derived_layer.topic_candidate_selections",
                "derived_layer.topic_labels",
            }
            self.assertTrue(removed_tables.isdisjoint(actual_tables))

            for table_name in ("patent_technical_embeddings", "patent_effect_embeddings"):
                cur.execute(
                    """
                    SELECT data_type, udt_name
                    FROM information_schema.columns
                    WHERE table_schema = 'core_layer'
                      AND table_name = %s
                      AND column_name = 'embedding_vector'
                    """,
                    (table_name,),
                )
                vector_column = cur.fetchone()
                self.assertEqual(
                    vector_column,
                    {"data_type": "USER-DEFINED", "udt_name": "vector"},
                )

                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'core_layer'
                      AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (table_name,),
                )
                actual_embedding_columns = [row["column_name"] for row in cur.fetchall()]
                self.assertEqual(actual_embedding_columns, EXPECTED_EMBEDDING_COLUMNS)

            # 功效摘要只屬於專利核心資料，attribute 表不可再保留同名欄位。
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.columns
                WHERE table_schema = 'core_layer'
                  AND column_name = '效果 摘要[US,EP,PCT,JP,KR,CN,TW]'
                  AND table_name IN ('patents', 'patent_attributes')
                ORDER BY table_name
                """
            )
            self.assertEqual(
                [row["table_name"] for row in cur.fetchall()],
                ["patents"],
            )

            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'core_layer'
                  AND table_name = 'patents'
                  AND ordinal_position BETWEEN 2 AND 8
                ORDER BY ordinal_position
                """
            )
            actual_patent_number_columns = [row["column_name"] for row in cur.fetchall()]
            self.assertEqual(actual_patent_number_columns, EXPECTED_PATENT_NUMBER_COLUMNS)

            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'derived_layer'
                  AND table_name = 'report_patent_base'
                  AND ordinal_position BETWEEN 3 AND 9
                ORDER BY ordinal_position
                """
            )
            actual_report_number_columns = [row["column_name"] for row in cur.fetchall()]
            self.assertEqual(actual_report_number_columns, EXPECTED_PATENT_NUMBER_COLUMNS)

            cur.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname IN ('core_layer', 'derived_layer', 'app_layer')
                """
            )
            indexes = {row["indexname"] for row in cur.fetchall()}
            self.assertIn("ux_patent_technical_embeddings_identity", indexes)
            self.assertIn("ux_patent_effect_embeddings_identity", indexes)
            self.assertIn("topics_workspace_code_key", indexes)
            self.assertIn("ux_topic_assignments_current", indexes)
            self.assertIn("ux_topic_candidates_one_selected", indexes)

            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'derived_layer'
                  AND table_name = 'topic_runs'
                  AND column_name IN ('reverted_at', 'reverted_by', 'reverted_by_run_id')
                ORDER BY column_name
                """
            )
            self.assertEqual(
                [row["column_name"] for row in cur.fetchall()],
                ["reverted_at", "reverted_by", "reverted_by_run_id"],
            )

    def test_transformed_patent_number_columns_follow_country_rule(self) -> None:
        """generated 欄對 TW 轉民國年，非 TW 保留原值，且測試後 rollback。"""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO core_layer.patents (
                    "未審查的公開號", "申請號", country_code
                ) VALUES
                    ('202619621', '2024132600', 'TW'),
                    ('20240001', '18/648768', 'US')
                RETURNING
                    country_code,
                    "未審查的公開號", "未審查的公開號(轉換後)",
                    "申請號", "申請號(轉換後)"
                """
            )
            rows = cur.fetchall()

        self.assertEqual(
            rows[0],
            {
                "country_code": "TW",
                "未審查的公開號": "202619621",
                "未審查的公開號(轉換後)": "11519621",
                "申請號": "2024132600",
                "申請號(轉換後)": "113132600",
            },
        )
        self.assertEqual(rows[1]["未審查的公開號(轉換後)"], "20240001")
        self.assertEqual(rows[1]["申請號(轉換後)"], "18/648768")

    def test_expected_wips_import_integrity(self) -> None:
        """檢查指定 WIPS 測試檔匯入後的筆數、四號碼與追溯 FK。"""
        expected = int(os.getenv("CLUSTERING_DB_EXPECTED_PATENTS", "0"))
        expected_source_files = int(os.getenv("CLUSTERING_DB_EXPECTED_SOURCE_FILES", "0"))
        if expected <= 0:
            self.skipTest("CLUSTERING_DB_EXPECTED_PATENTS is not set")

        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT count(*) FROM raw_layer.source_files) AS source_files,
                    (SELECT count(*) FROM raw_layer.raw_records) AS raw_records,
                    (SELECT count(*) FROM core_layer.patents) AS patents,
                    (SELECT count(*) FROM core_layer.patent_sources) AS patent_sources,
                    (SELECT count(*) FROM core_layer.patent_people) AS patent_people,
                    (SELECT count(*) FROM core_layer.patent_attributes) AS patent_attributes
                """
            )
            counts = cur.fetchone()
            if expected_source_files > 0:
                self.assertEqual(counts["source_files"], expected_source_files)
            for key in ("raw_records", "patents", "patent_sources", "patent_people", "patent_attributes"):
                self.assertEqual(counts[key], expected, key)

            cur.execute(
                """
                SELECT
                    count(*) FILTER (WHERE NULLIF(BTRIM("授權公告號"), '') IS NOT NULL) AS grant_no,
                    count(*) FILTER (WHERE NULLIF(BTRIM("審查的公告號"), '') IS NOT NULL) AS examined_no,
                    count(*) FILTER (WHERE NULLIF(BTRIM("未審查的公開號"), '') IS NOT NULL) AS unexamined_no,
                    count(*) FILTER (
                        WHERE NULLIF(BTRIM("未審查的公開號(轉換後)"), '') IS NOT NULL
                    ) AS transformed_unexamined_no,
                    count(*) FILTER (WHERE NULLIF(BTRIM("申請號"), '') IS NOT NULL) AS application_no,
                    count(*) FILTER (
                        WHERE NULLIF(BTRIM("申請號(轉換後)"), '') IS NOT NULL
                    ) AS transformed_application_no,
                    count(*) FILTER (
                        WHERE NULLIF(BTRIM("獨立項[KR,JP,US,CN,EP,IN]"), '') IS NOT NULL
                    ) AS independent_claims,
                    count(*) FILTER (
                        WHERE COALESCE(
                            NULLIF(BTRIM("授權公告號"), ''),
                            NULLIF(BTRIM("審查的公告號"), ''),
                            NULLIF(BTRIM("未審查的公開號"), ''),
                            NULLIF(BTRIM("申請號"), '')
                        ) IS NULL
                    ) AS missing_all_patent_numbers
                FROM core_layer.patents
                """
            )
            field_counts = cur.fetchone()
            self.assertGreater(field_counts["application_no"], 0)
            self.assertEqual(field_counts["unexamined_no"], field_counts["transformed_unexamined_no"])
            self.assertEqual(field_counts["application_no"], field_counts["transformed_application_no"])
            self.assertGreater(field_counts["independent_claims"], 0)
            self.assertEqual(field_counts["missing_all_patent_numbers"], 0)

            cur.execute(
                """
                SELECT count(*) AS orphan_count
                FROM core_layer.patent_sources ps
                LEFT JOIN core_layer.patents p ON p.id = ps.patent_id
                LEFT JOIN raw_layer.raw_records r ON r.id = ps.raw_record_id
                LEFT JOIN raw_layer.source_files sf ON sf.id = ps.source_file_id
                WHERE p.id IS NULL OR r.id IS NULL OR sf.id IS NULL
                """
            )
            self.assertEqual(cur.fetchone()["orphan_count"], 0)

    def test_full_clustering_write_path_rolls_back(self) -> None:
        """走完雙 embedding、候選、穩定 topic、assignment 與 incremental run。"""
        with self.conn.cursor() as cur:
            patent = self._fetch_test_patent(cur)
            if patent is None:
                self.skipTest("core_layer.patents is empty")
            suffix = uuid4().hex

            cur.execute(
                """
                INSERT INTO app_layer.workspaces (workspace_name, description, created_by)
                VALUES (%s, %s, %s)
                RETURNING workspace_id
                """,
                (f"db-integration-{suffix}", "migration integration test", "unittest"),
            )
            workspace_id = cur.fetchone()["workspace_id"]
            cur.execute(
                """
                INSERT INTO app_layer.workspace_patents (workspace_id, patent_id, source_type, added_by)
                VALUES (%s, %s, 'manual', 'unittest')
                """,
                (workspace_id, patent["id"]),
            )

            vector_text = "[" + ",".join(["0"] * 768) + "]"
            embedding_ids: list[int] = []
            for table_name in ("patent_technical_embeddings", "patent_effect_embeddings"):
                cur.execute(
                    f"""
                    INSERT INTO core_layer.{table_name} (
                        patent_id, text_hash, embedding_model, model_version,
                        preprocessing_version, embedding_vector, chunk_count, metadata_json
                    ) VALUES (
                        %s, %s, 'AI-Growth-Lab/PatentSBERTa', %s,
                        'patent_text_clean_v1', %s::vector, 1, %s
                    )
                    RETURNING embedding_id
                    """,
                    (
                        patent["id"],
                        f"{table_name}-{suffix}",
                        f"model-{suffix}",
                        vector_text,
                        Jsonb({"aggregation_method": "weighted_mean"}),
                    ),
                )
                embedding_ids.append(cur.fetchone()["embedding_id"])

            cur.execute(
                """
                INSERT INTO derived_layer.topic_runs (
                    workspace_id, source_field, run_mode, status,
                    input_doc_count, parameters_json,
                    model_artifact_path, model_artifact_hash
                ) VALUES (
                    %s, 'wips_independent_claims', 'full', 'running',
                    1, %s, %s, %s
                ) RETURNING run_id
                """,
                (
                    workspace_id,
                    Jsonb({"n_components": 100, "coherence": "c_v"}),
                    f"models/{suffix}",
                    f"artifact-{suffix}",
                ),
            )
            run_id = cur.fetchone()["run_id"]
            cur.execute(
                """
                INSERT INTO derived_layer.topics (
                    workspace_id, source_field, created_run_id, topic_code,
                    model_topic_ids, topic_kind, doc_count,
                    coherence, diversity, balance, keywords_json,
                    representative_patent_ids_json, label, summary, label_source
                ) VALUES (
                    %s, 'wips_independent_claims', %s, 'T1', ARRAY[0], 'model',
                    1, 0.5, 1.0, 1.0, %s, %s, %s, %s, 'manual'
                )
                RETURNING topic_id
                """,
                (
                    workspace_id,
                    run_id,
                    Jsonb(["test topic"]),
                    Jsonb([patent["id"]]),
                    "Integration test",
                    "Rollback after validation",
                ),
            )
            root_topic_id = cur.fetchone()["topic_id"]

            candidate_ids: list[int] = []
            for candidate_type, candidate_k, is_selected in (
                ("conservative", 5, False),
                ("balanced", 8, True),
                ("detailed", 12, False),
            ):
                cur.execute(
                    """
                    INSERT INTO derived_layer.topic_candidates (
                        run_id, candidate_type, candidate_k,
                        coherence, diversity, balance, score,
                        llm_explanation, is_selected, selected_by, selected_at
                    ) VALUES (
                        %s, %s, %s, 0.5, 1.0, 1.0, 1.0,
                        %s, %s, %s,
                        CASE WHEN %s THEN now() ELSE NULL END
                    ) RETURNING candidate_id
                    """,
                    (
                        run_id,
                        candidate_type,
                        candidate_k,
                        f"{candidate_type} explanation",
                        is_selected,
                        "unittest" if is_selected else None,
                        is_selected,
                    ),
                )
                candidate_ids.append(cur.fetchone()["candidate_id"])

            cur.execute(
                """
                INSERT INTO derived_layer.topic_assignments (
                    workspace_id, source_field, patent_id, topic_id,
                    assigned_run_id, distance_to_centroid, is_current
                ) VALUES (%s, 'wips_independent_claims', %s, %s, %s, 0.1, true)
                """,
                (workspace_id, patent["id"], root_topic_id, run_id),
            )
            cur.execute(
                """
                INSERT INTO derived_layer.topic_runs (
                    workspace_id, source_field, run_mode, previous_run_id,
                    status, input_doc_count, new_doc_count, parameters_json
                ) VALUES (
                    %s, 'wips_independent_claims', 'incremental', %s,
                    'pending', 2, 1, %s
                ) RETURNING run_id
                """,
                (workspace_id, run_id, Jsonb({"partial_fit": True})),
            )
            incremental_run_id = cur.fetchone()["run_id"]
            cur.execute(
                """
                INSERT INTO derived_layer.topic_runs (
                    workspace_id, source_field, run_mode, previous_run_id,
                    status, input_doc_count, parameters_json
                ) VALUES (
                    %s, 'wips_independent_claims', 'unmerge', %s,
                    'pending', 1, %s
                ) RETURNING run_id
                """,
                (
                    workspace_id,
                    incremental_run_id,
                    Jsonb({"target_merge_run_id": run_id}),
                ),
            )
            unmerge_run_id = cur.fetchone()["run_id"]

            for generated_id in (
                workspace_id,
                *embedding_ids,
                run_id,
                root_topic_id,
                incremental_run_id,
                unmerge_run_id,
                *candidate_ids,
            ):
                self.assertGreater(generated_id, 0)

            cur.execute(
                "SELECT count(*) AS assignment_count FROM derived_layer.topic_assignments WHERE assigned_run_id = %s",
                (run_id,),
            )
            self.assertEqual(cur.fetchone()["assignment_count"], 1)

            # pgvector 固定 768 維；錯誤維度必須由資料庫拒絕。
            cur.execute("SAVEPOINT invalid_vector_dimension")
            with self.assertRaises(psycopg.Error):
                cur.execute(
                    """
                    INSERT INTO core_layer.patent_technical_embeddings (
                        patent_id, text_hash, embedding_model, model_version,
                        preprocessing_version, embedding_vector, chunk_count
                    ) VALUES (
                        %s, 'text-invalid', 'test', 'model-invalid',
                        'test', '[0]'::vector, 1
                    )
                    """,
                    (patent["id"],),
                )
            cur.execute("ROLLBACK TO SAVEPOINT invalid_vector_dimension")

            # 同一層只能有一組 is_selected 候選。
            cur.execute("SAVEPOINT duplicate_selected_candidate")
            with self.assertRaises(psycopg.Error):
                cur.execute(
                    """
                    INSERT INTO derived_layer.topic_candidates (
                        run_id, candidate_type, candidate_k,
                        is_selected, selected_by, selected_at
                    ) VALUES (%s, 'detailed', 20, true, 'unittest', now())
                    """,
                    (run_id,),
                )
            cur.execute("ROLLBACK TO SAVEPOINT duplicate_selected_candidate")

    @staticmethod
    def _fetch_test_patent(cur: psycopg.Cursor) -> dict[str, object] | None:
        """取得一筆有獨立項且至少有一種專利號的真實匯入資料。"""
        cur.execute(
            """
            SELECT
                id,
                country_code,
                "授權公告號",
                "審查的公告號",
                "未審查的公開號",
                "申請號",
                "獨立項[KR,JP,US,CN,EP,IN]"
            FROM core_layer.patents
            WHERE NULLIF(BTRIM("獨立項[KR,JP,US,CN,EP,IN]"), '') IS NOT NULL
              AND COALESCE(
                    NULLIF(BTRIM("授權公告號"), ''),
                    NULLIF(BTRIM("審查的公告號"), ''),
                    NULLIF(BTRIM("未審查的公開號"), ''),
                    NULLIF(BTRIM("申請號"), '')
                  ) IS NOT NULL
            ORDER BY id
            LIMIT 1
            """
        )
        return cur.fetchone()


if __name__ == "__main__":
    unittest.main()
