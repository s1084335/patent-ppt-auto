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


# 0021 併表後仍存在的分群相關表；topics/topic_candidates 併入 topic_runs.topic_state_json，
# workspace_patents 併入 workspaces.patent_ids_json，三者已從 derived/app layer 移除。
EXPECTED_CLUSTERING_TABLES = {
    "app_layer.workspaces",
    "app_layer.workflow_runs",
    "core_layer.patent_effect_embeddings",
    "core_layer.patent_technical_embeddings",
    "derived_layer.topic_assignments",
    "derived_layer.topic_runs",
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
                # 0021 併表移除（僅 legacy_0021 保留凍結 archive）
                "derived_layer.topics",
                "derived_layer.topic_candidates",
                "app_layer.workspace_patents",
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
        """走完雙 embedding、候選、穩定 topic、assignment 與 incremental run（0021 落點）。

        0021：workspace 成員存 workspaces.patent_ids_json；topic run 需先有一筆
        app_layer.workflow_runs（NOT NULL FK），候選與正式主題都併入 topic_state_json，
        assignments 以 (run_id, patent_id) 一列、topic_key=topic_code 落 derived_layer.topic_assignments。
        """
        with self.conn.cursor() as cur:
            patent = self._fetch_test_patent(cur)
            if patent is None:
                self.skipTest("core_layer.patents is empty")
            suffix = uuid4().hex

            cur.execute(
                """
                INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json, settings_json)
                VALUES (%s, %s, %s)
                RETURNING workspace_id
                """,
                (
                    f"db-integration-{suffix}",
                    Jsonb([patent["id"]]),
                    Jsonb({"description": "migration integration test", "created_by": "unittest"}),
                ),
            )
            workspace_id = cur.fetchone()["workspace_id"]

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

            # topic_runs.workflow_run_id 為 NOT NULL FK，run 的 workspace/狀態掛在 workflow_runs
            def _new_topic_run(run_mode: str, previous_run_id: int | None, state: dict) -> int:
                cur.execute(
                    "INSERT INTO app_layer.workflow_runs (workspace_id, run_type, status) "
                    "VALUES (%s, %s, 'running') RETURNING run_id",
                    (workspace_id, f"clustering_{run_mode}"),
                )
                workflow_run_id = cur.fetchone()["run_id"]
                cur.execute(
                    """
                    INSERT INTO derived_layer.topic_runs (
                        run_id, workflow_run_id, previous_run_id, source_field,
                        topic_state_json, artifact_key
                    )
                    SELECT COALESCE(max(run_id), 0) + 1, %s, %s, 'wips_independent_claims', %s, %s
                    FROM derived_layer.topic_runs
                    RETURNING run_id
                    """,
                    (workflow_run_id, previous_run_id, Jsonb(state), f"models/{suffix}"),
                )
                return cur.fetchone()["run_id"]

            # 候選與正式主題都併入 topic_state_json（0021 唯一落點）
            candidates = [
                {"candidate_id": index, "candidate_type": candidate_type, "candidate_k": candidate_k,
                 "coherence": 0.5, "diversity": 1.0, "balance": 1.0, "score": 1.0,
                 "llm_explanation": f"{candidate_type} explanation",
                 "is_selected": is_selected,
                 "selected_by": "unittest" if is_selected else None}
                for index, (candidate_type, candidate_k, is_selected) in enumerate(
                    (("conservative", 5, False), ("balanced", 8, True), ("detailed", 12, False)),
                    start=1,
                )
            ]
            topics = [{
                "topic_id": 1, "topic_code": "T1", "model_topic_ids": [0], "topic_kind": "model",
                "doc_count": 1, "coherence": 0.5, "diversity": 1.0, "balance": 1.0,
                "keywords": ["test topic"], "representative_patent_ids": [patent["id"]],
                "label": "Integration test", "summary": "Rollback after validation",
                "label_source": "manual", "status": "active", "display_order": 1,
            }]
            run_id = _new_topic_run("full", None, {
                "run_mode": "full", "status": "running", "input_doc_count": 1,
                "parameters": {"n_components": 100, "coherence": "c_v"},
                "candidates": candidates, "topics": topics,
            })
            # assignments：(run_id, patent_id) 一列，topic_key 存 topic_code
            cur.execute(
                """
                INSERT INTO derived_layer.topic_assignments (
                    run_id, patent_id, topic_key, distance_to_centroid
                ) VALUES (%s, %s, 'T1', 0.1)
                """,
                (run_id, patent["id"]),
            )
            incremental_run_id = _new_topic_run("incremental", run_id, {
                "run_mode": "incremental", "status": "pending",
                "input_doc_count": 2, "new_doc_count": 1, "parameters": {"partial_fit": True},
            })
            unmerge_run_id = _new_topic_run("unmerge", incremental_run_id, {
                "run_mode": "unmerge", "status": "pending", "input_doc_count": 1,
                "parameters": {"target_merge_run_id": run_id},
            })

            for generated_id in (
                workspace_id,
                *embedding_ids,
                run_id,
                incremental_run_id,
                unmerge_run_id,
            ):
                self.assertGreater(generated_id, 0)

            cur.execute(
                "SELECT count(*) AS assignment_count FROM derived_layer.topic_assignments WHERE run_id = %s",
                (run_id,),
            )
            self.assertEqual(cur.fetchone()["assignment_count"], 1)

            # 候選與主題讀得回同一份 topic_state_json
            cur.execute(
                "SELECT topic_state_json AS state FROM derived_layer.topic_runs WHERE run_id = %s",
                (run_id,),
            )
            state = cur.fetchone()["state"]
            self.assertEqual([c["candidate_k"] for c in state["candidates"]], [5, 8, 12])
            self.assertEqual([t["topic_code"] for t in state["topics"]], ["T1"])

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

            # 0021：候選改存 JSON，已無 topic_candidates 的唯一索引可擋重複選定；
            # 「同一 run 只有一組 is_selected」改由寫入端保證，這裡驗證該不變式。
            self.assertEqual(sum(1 for c in state["candidates"] if c["is_selected"]), 1)

            # topic_runs.workflow_run_id 為 NOT NULL：缺 workflow_run 必須被 DB 擋下
            cur.execute("SAVEPOINT missing_workflow_run")
            with self.assertRaises(psycopg.Error):
                cur.execute(
                    "INSERT INTO derived_layer.topic_runs (run_id, source_field) "
                    "VALUES (999999999, 'wips_independent_claims')"
                )
            cur.execute("ROLLBACK TO SAVEPOINT missing_workflow_run")

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
