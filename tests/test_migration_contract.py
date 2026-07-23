"""0019~0021 migration 契約測試（獨立測試 DB，不碰 patent_ppt 值）。

在同一 PostgreSQL 伺服器上建拋棄式資料庫 patent_ppt_migcontract，升到 0018 後灌最小
fixture，再嘗試 upgrade head，對「最終 3+3 表、資料搬移、複合鍵/FK/版本、舊表移除、
upgrade/downgrade 可執行、chain 線性」做契約斷言。本輪只到 Red：多數斷言會失敗，代表
0021 併表行為尚未實作。tearDown 會 DROP 測試 DB，全程不連 patent_ppt。
"""
from __future__ import annotations

import os
import unittest

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_migcontract"
BASE_REV = "0018_compose_created_at_comment"
HEAD_REV = "0021_derived_app_consolidation"

APP_TARGET_TABLES = {"workspaces", "workflow_runs", "workflow_outputs"}
DERIVED_TARGET_TABLES = {"company_aliases", "topic_runs", "topic_assignments"}
# 0021 之後才新增的表：本檔驗的是「0021 併表後的骨架」，但測試 DB 一律 upgrade 到 head，
# 故後續 migration 新增的表要在此登記，否則骨架斷言會被無關的新表誤判為失敗。
# 新增 migration 建表時，一併在這裡補上表名與來源 revision。
POST_0021_TABLES = {
    # 0023_market_evidence：市場資料證據庫。
    "derived_layer": {"market_evidence"},
    # 0024_import_blobs：匯入上傳內容的跨容器傳輸表。
    # 0025_report_artifacts：報表產物的跨容器共享表。
    # 0027_workspace_documents：workspace 的技術文獻（PDF）內容保存表。
    # （0028_global_workspace 只在 workspaces 加 is_global 欄，未建表，故不需登記。）
    "app_layer": {"import_blobs", "report_artifacts", "workspace_documents"},
}
OLD_TABLES = {
    "app_layer": {
        "workspace_patents", "workspace_compose_sources", "processing_jobs",
        "analysis_runs", "analysis_outputs", "export_runs", "company_normalization_tasks",
    },
    "derived_layer": {"topic_candidates", "topics"},
}

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _kw(dbname: str) -> dict:
    """由 PG* 環境變數組連線參數，明確指定 dbname（絕不連 patent_ppt 以外做寫入）。"""
    kw = dict(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    password = os.getenv("PGPASSWORD")
    if password:
        kw["password"] = password
    return kw


def _alembic_cfg() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


class MigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 強制走 127.0.0.1：Windows 上 localhost 會先試 IPv6(::1) 造成連線延遲（實測 localhost
        # 5~130s、127.0.0.1 0.03s）；alembic env.py 與本測試連線都讀 PGHOST。
        cls._prev_pghost = os.environ.get("PGHOST")
        os.environ["PGHOST"] = "127.0.0.1"
        # 連 postgres 維護庫建/刪測試庫；不可用則 skip（不觸碰 patent_ppt）。
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")

        cls._prev_pgdb = os.environ.get("PGDATABASE")
        cls._prev_dburl = os.environ.get("DATABASE_URL")
        os.environ.pop("DATABASE_URL", None)     # 強制走 PG* 路徑
        os.environ["PGDATABASE"] = TEST_DB       # alembic env.py 依此解析 URL

        cfg = _alembic_cfg()
        command.upgrade(cfg, BASE_REV)           # 0001..0018 於測試庫
        cls._seed()
        cls.upgrade_error: Exception | None = None
        try:
            command.upgrade(cfg, "head")         # 0019→0020→0021
        except Exception as exc:                 # noqa: BLE001
            cls.upgrade_error = exc

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_prev_pghost", None) is None:
            os.environ.pop("PGHOST", None)
        else:
            os.environ["PGHOST"] = cls._prev_pghost
        if cls._prev_pgdb is None:
            os.environ.pop("PGDATABASE", None)
        else:
            os.environ["PGDATABASE"] = cls._prev_pgdb
        if cls._prev_dburl is not None:
            os.environ["DATABASE_URL"] = cls._prev_dburl
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:  # noqa: BLE001
            pass

    @classmethod
    def _seed(cls):
        """代表 fixture：每張待併舊表各種一列有值資料，供「資料按 3+3 對應保留」契約。

        皆以 900001/900002 為業務鍵，滿足各表 NOT NULL / FK / CHECK。
        """
        with psycopg.connect(**_kw(TEST_DB)) as c:
            c.execute("INSERT INTO core_layer.patents (id, title) VALUES (900001, 'mig contract fixture')")
            # workspaces（900001 為組合結果、900002 為來源）＋成員＋compose 來源
            # 固定稽核欄值，供 workspace metadata 契約（升級須存 settings_json、downgrade 逐值還原）
            c.execute(
                "INSERT INTO app_layer.workspaces "
                "(workspace_id, workspace_name, created_by, created_at, updated_at, archived_at) "
                "VALUES (900001, 'mig_contract_ws', 'test', "
                "'2020-01-02 03:04:05+00', '2020-03-04 05:06:07+00', '2020-05-06 07:08:09+00')"
            )
            c.execute("INSERT INTO app_layer.workspaces (workspace_id, workspace_name, created_by) VALUES (900002, 'mig_src_ws', 'test')")
            c.execute("INSERT INTO app_layer.workspace_patents (workspace_id, patent_id, source_type, added_by) VALUES (900001, 900001, 'manual', 'test')")
            c.execute("INSERT INTO app_layer.workspace_patents (workspace_id, patent_id, source_type, added_by) VALUES (900002, 900001, 'manual', 'test')")
            c.execute("INSERT INTO app_layer.workspace_compose_sources (workspace_id, source_workspace_id, source_patent_count) VALUES (900001, 900002, 1)")
            # processing_jobs
            c.execute(
                "INSERT INTO app_layer.processing_jobs "
                "(job_id, job_type, status, workspace_id, payload_json, result_json, progress_percent, idempotency_key) "
                "VALUES (900001, 'clustering_calibrate', 'succeeded', 900001, "
                "'{\"source_field\":\"wips_independent_claims\"}'::jsonb, '{\"run_id\":900001}'::jsonb, 100, 'job-key-900001')"
            )
            # analysis_runs / analysis_outputs / export_runs
            c.execute("INSERT INTO app_layer.analysis_runs (analysis_id, analysis_name, analysis_type, status) VALUES (900001, 'mig analysis', 'report', 'completed')")
            c.execute("INSERT INTO app_layer.analysis_outputs (analysis_id, output_type, output_name, result_json) VALUES (900001, 'chart_data', 'applicant_ranking', '{\"rows\":[{\"applicant\":\"力山\"}]}'::jsonb)")
            c.execute("INSERT INTO app_layer.export_runs (analysis_id, export_type, file_path, file_hash) VALUES (900001, 'pdf', 'reports/900001.pdf', 'sha256abc')")
            # company_normalization_tasks
            c.execute("INSERT INTO app_layer.company_normalization_tasks (task_id, raw_name, status) VALUES (900001, 'REXON INDUSTRIAL', 'pending')")
            # topic 叢集：topic_runs → topics → topic_candidates → topic_assignments
            c.execute("INSERT INTO derived_layer.topic_runs (run_id, workspace_id, source_field, run_mode, status) VALUES (900001, 900001, 'wips_independent_claims', 'full', 'completed')")
            c.execute("INSERT INTO derived_layer.topics (topic_id, workspace_id, source_field, created_run_id, topic_code) VALUES (900001, 900001, 'wips_independent_claims', 900001, 'T01')")
            c.execute("INSERT INTO derived_layer.topic_candidates (candidate_id, run_id, candidate_type, candidate_k) VALUES (900001, 900001, 'balanced', 5)")
            c.execute("INSERT INTO derived_layer.topic_assignments (assignment_id, workspace_id, source_field, patent_id, topic_id, assigned_run_id) VALUES (900001, 900001, 'wips_independent_claims', 900001, 900001, 900001)")
            # report_* 底表（含必要報表欄位）
            c.execute("INSERT INTO derived_layer.report_patent_base (patent_id, applicant_display_name) VALUES (900001, '力山工業')")
            c.execute("INSERT INTO derived_layer.report_family_country (family_id, country_code) VALUES (900001, 'TW')")
            c.execute("INSERT INTO derived_layer.report_family_quality (family_id, member_rows) VALUES (900001, 3)")
            c.commit()

    def _scalar(self, sql: str):
        with psycopg.connect(**_kw(TEST_DB)) as c:
            row = c.execute(sql).fetchone()
        return row[0] if row else None

    # ── helpers ──
    def _base_tables(self, schema: str) -> set[str]:
        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=%s AND table_type='BASE TABLE'",
                (schema,),
            ).fetchall()
        return {r[0] for r in rows}

    def _pk_columns(self, schema: str, table: str) -> list[str]:
        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                """
                SELECT a.attname
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_namespace ns ON ns.oid = rel.relnamespace
                JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
                JOIN pg_attribute a ON a.attrelid = rel.oid AND a.attnum = k.attnum
                WHERE con.contype='p' AND ns.nspname=%s AND rel.relname=%s
                ORDER BY k.ord
                """,
                (schema, table),
            ).fetchall()
        return [r[0] for r in rows]

    # ── 契約斷言 ──
    def test_chain_is_linear_single_head(self):
        """0018→0019→0020→0021 段為線性，且整條 chain 只有單一 head（不分叉）。

        head 不寫死某個 revision：0021 之後仍會持續新增 migration，寫死會讓每次新增都誤紅。
        真正要守的不變量是「只有一個 head」＋「本檔涵蓋的 0018~0021 段仍線性」。
        """
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(_alembic_cfg())
        self.assertEqual(len(script.get_heads()), 1, f"migration 分叉：{script.get_heads()}")
        chain = {rev.revision: rev.down_revision for rev in script.walk_revisions(BASE_REV, HEAD_REV)}
        self.assertEqual(chain.get(HEAD_REV), "0020_core_layer_simplify")
        self.assertEqual(chain.get("0020_core_layer_simplify"), "0019_raw_layer_simplify")
        self.assertEqual(chain.get("0019_raw_layer_simplify"), BASE_REV)

    def test_upgrade_to_head_executes(self):
        """upgrade head 必須可執行（缺行為：0021 upgrade 資料搬移未實作）。"""
        self.assertIsNone(
            self.upgrade_error,
            msg=f"upgrade head 失敗：{type(self.upgrade_error).__name__}: {self.upgrade_error}",
        )

    def test_app_layer_three_base_tables(self):
        """app_layer 併表後骨架＝workspaces/workflow_runs/workflow_outputs（扣掉 0021 後新增的表）。"""
        actual = self._base_tables("app_layer") - POST_0021_TABLES["app_layer"]
        self.assertEqual(actual, APP_TARGET_TABLES)

    def test_derived_layer_three_base_tables(self):
        """derived_layer 併表後骨架＝company_aliases/topic_runs/topic_assignments（扣掉 0021 後新增的表）。"""
        actual = self._base_tables("derived_layer") - POST_0021_TABLES["derived_layer"]
        self.assertEqual(actual, DERIVED_TARGET_TABLES)

    def test_old_tables_removed(self):
        """舊表確實移除（併入新表）。"""
        app_now = self._base_tables("app_layer")
        derived_now = self._base_tables("derived_layer")
        self.assertEqual(OLD_TABLES["app_layer"] & app_now, set(), "app_layer 仍有舊表")
        self.assertEqual(OLD_TABLES["derived_layer"] & derived_now, set(), "derived_layer 仍有舊表")

    def test_workspace_patent_association_preserved(self):
        """專利↔workspace 關聯不遺失：workspaces.patent_ids_json 應含該專利。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            row = c.execute(
                "SELECT patent_ids_json FROM app_layer.workspaces WHERE workspace_id=900001"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertIn(900001, list(row[0]))

    def test_composite_primary_keys(self):
        """workflow_outputs PK (run_id,output_type,version)、topic_assignments PK (run_id,patent_id)。"""
        self.assertEqual(
            self._pk_columns("app_layer", "workflow_outputs"), ["run_id", "output_type", "version"]
        )
        self.assertEqual(
            self._pk_columns("derived_layer", "topic_assignments"), ["run_id", "patent_id"]
        )

    def test_fk_and_request_key_unique(self):
        """workflow_runs.request_key UNIQUE 且 topic_runs/topic_assignments FK 存在（版本不覆蓋靠 outputs PK 的 version）。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            uniq = c.execute(
                "SELECT count(*) FROM pg_constraint con "
                "JOIN pg_class r ON r.oid=con.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace "
                "WHERE n.nspname='app_layer' AND r.relname='workflow_runs' AND con.contype='u'"
            ).fetchone()[0]
            fks = c.execute(
                "SELECT count(*) FROM pg_constraint con "
                "JOIN pg_class r ON r.oid=con.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace "
                "WHERE n.nspname='derived_layer' AND r.relname='topic_assignments' AND con.contype='f'"
            ).fetchone()[0]
        self.assertGreaterEqual(uniq, 1, "workflow_runs 缺 request_key UNIQUE")
        self.assertGreaterEqual(fks, 2, "topic_assignments 缺 run_id/patent_id FK")

    def test_downgrade_restores_old_tables_and_values(self):
        """downgrade 回 0018 後，舊表結構、代表欄位與 fixture 值皆可還原（非只表存在）。"""
        if self.upgrade_error is not None:
            self.fail(
                "無法驗 downgrade：upgrade head 尚未可執行；"
                f"{type(self.upgrade_error).__name__}: {self.upgrade_error}"
            )
        cfg = _alembic_cfg()
        try:
            command.downgrade(cfg, BASE_REV)
            self.assertIn("workspace_patents", self._base_tables("app_layer"))
            # 代表欄位值還原（下列任一未還原即 Red）
            self.assertEqual(self._scalar("SELECT patent_id FROM app_layer.workspace_patents WHERE workspace_id=900001"), 900001)
            self.assertEqual(self._scalar("SELECT job_type FROM app_layer.processing_jobs WHERE job_id=900001"), "clustering_calibrate")
            self.assertEqual(self._scalar("SELECT analysis_name FROM app_layer.analysis_runs WHERE analysis_id=900001"), "mig analysis")
            self.assertEqual(self._scalar("SELECT file_hash FROM app_layer.export_runs WHERE analysis_id=900001"), "sha256abc")
            self.assertEqual(self._scalar("SELECT raw_name FROM app_layer.company_normalization_tasks WHERE task_id=900001"), "REXON INDUSTRIAL")
            self.assertEqual(self._scalar("SELECT topic_code FROM derived_layer.topics WHERE topic_id=900001"), "T01")
            self.assertEqual(self._scalar("SELECT candidate_k FROM derived_layer.topic_candidates WHERE candidate_id=900001"), 5)
            self.assertEqual(self._scalar("SELECT applicant_display_name FROM derived_layer.report_patent_base WHERE patent_id=900001"), "力山工業")
        finally:
            # 還原共用測試 DB 至 head，避免污染同類其他測試（只修測試隔離，不改契約斷言）。
            command.upgrade(cfg, "head")

    # ── 資料按 3+3 對應保留（升級後；非只表存在）──
    def test_workflow_runs_preserve_job_analysis_normalization(self):
        """workflow_runs 應保留 job／analysis／company_normalization run 的資料。"""
        self.assertGreaterEqual(
            self._scalar("SELECT count(*) FROM app_layer.workflow_runs"), 3,
            "workflow_runs 應含 job＋analysis＋normalization 三類 run")
        self.assertEqual(
            self._scalar("SELECT worker_state_json->>'progress' FROM app_layer.workflow_runs WHERE run_id=900001"),
            "100", "processing_jobs 的 progress 未保留到 workflow_runs.worker_state_json")
        self.assertGreaterEqual(
            self._scalar("SELECT count(*) FROM app_layer.workflow_runs WHERE run_type LIKE '%normalization%'"), 1,
            "company_normalization_tasks 未轉為 workflow_runs(run_type=company_normalization)")

    def test_workflow_outputs_preserve_analysis_export_report(self):
        """workflow_outputs 應保留 analysis_output／export／report 版本資料。"""
        self.assertGreaterEqual(
            self._scalar("SELECT count(*) FROM app_layer.workflow_outputs"), 2,
            "workflow_outputs 應含 analysis_output＋export 至少兩列")
        self.assertGreaterEqual(
            self._scalar(
                "SELECT count(*) FROM app_layer.workflow_outputs "
                "WHERE artifact_manifest_json::text LIKE '%sha256abc%' OR data_json::text LIKE '%sha256abc%'"
            ), 1, "export_runs 的 file_hash 未保留到 workflow_outputs")

    def test_topic_state_preserves_topic_candidate_assignment(self):
        """topic_runs.topic_state_json 保留 topic／candidate；topic_assignments 保留指派。"""
        self.assertEqual(
            self._scalar("SELECT count(*) FROM derived_layer.topic_runs WHERE run_id=900001"), 1,
            "topic_runs 未保留 run 900001")
        state = self._scalar("SELECT topic_state_json::text FROM derived_layer.topic_runs WHERE run_id=900001")
        self.assertIsNotNone(state, "topic_runs 900001 不存在")
        self.assertIn("T01", state, "topic_state_json 未含 topic(T01)/candidate 資料")
        self.assertEqual(
            self._scalar("SELECT count(*) FROM derived_layer.topic_assignments WHERE run_id=900001 AND patent_id=900001"),
            1, "topic_assignments 未保留指派 (run 900001, patent 900001)")

    def test_report_views_have_required_columns(self):
        """report 相容 view 不得只剩 patent_id，需保留必要報表欄位並重現值。"""
        cols = set()
        with psycopg.connect(**_kw(TEST_DB)) as c:
            for r in c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='derived_layer' AND table_name='report_patent_base'"
            ).fetchall():
                cols.add(r[0])
        self.assertIn("applicant_display_name", cols, "report_patent_base view 缺必要欄位（只剩 patent_id）")
        self.assertEqual(
            self._scalar("SELECT applicant_display_name FROM derived_layer.report_patent_base WHERE patent_id=900001"),
            "力山工業", "report_patent_base view 未重現申請人資料")

    # ── Regression Red：0021 review 缺口 ──
    def test_topic_run_workflow_preserves_workspace_id(self):
        """topic_run 轉 clustering workflow_run 時不得遺失 workspace_id（fixture topic_run 900001 屬 workspace 900001）。"""
        if self.upgrade_error is not None:
            self.fail(f"upgrade head 未可執行：{type(self.upgrade_error).__name__}: {self.upgrade_error}")
        self.assertEqual(
            self._scalar(
                "SELECT wr.workspace_id FROM app_layer.workflow_runs wr "
                "JOIN derived_layer.topic_runs nt ON nt.workflow_run_id = wr.run_id "
                "WHERE nt.run_id = 900001"
            ),
            900001, "clustering workflow_run 遺失 workspace_id（搬移未帶 tr.workspace_id）")

    def test_report_refresh_truncate_rebuild_idempotent(self):
        """真正 refresh 路徑：TRUNCATE+INSERT 寫入 legacy_0021.report_* 實體表，經 derived_layer VIEW 讀取，可重跑不重複。"""
        if self.upgrade_error is not None:
            self.fail(f"upgrade head 未可執行：{type(self.upgrade_error).__name__}: {self.upgrade_error}")
        # 對應 refresh 程式：重建對象是實體表 legacy_0021.report_*，正式查詢仍走 derived_layer VIEW。
        refresh = (
            ("report_patent_base",
             "INSERT INTO legacy_0021.report_patent_base (patent_id, applicant_display_name) VALUES (900001, '力山工業')"),
            ("report_family_country",
             "INSERT INTO legacy_0021.report_family_country (family_id, country_code) VALUES (900001, 'TW')"),
            ("report_family_quality",
             "INSERT INTO legacy_0021.report_family_quality (family_id, member_rows) VALUES (900001, 3)"),
        )
        for _ in range(2):  # 重跑不得重複：refresh 對實體表 TRUNCATE 清空後重建
            with psycopg.connect(**_kw(TEST_DB)) as c:
                for tbl, ins in refresh:
                    c.execute(f"TRUNCATE TABLE legacy_0021.{tbl}")
                    c.execute(ins)
                c.commit()
        # 正式查詢走 derived_layer 相容 VIEW，且不因重跑而重複
        for view in ("report_patent_base", "report_family_country", "report_family_quality"):
            self.assertEqual(
                self._scalar(f"SELECT count(*) FROM derived_layer.{view}"), 1,
                f"{view} refresh 後 VIEW 讀數不為 1（重跑重複或未反映實體表）")
        self.assertEqual(
            self._scalar("SELECT applicant_display_name FROM derived_layer.report_patent_base WHERE patent_id=900001"),
            "力山工業", "derived_layer VIEW 未反映 legacy 實體表寫入")

    def test_workflow_runs_identity_advances_past_migrated(self):
        """遷入顯式 run_id 後，default run_id 必須大於既有 max，否則 IDENTITY 從 1 起會與遷入 ID 碰撞。"""
        if self.upgrade_error is not None:
            self.fail(f"upgrade head 未可執行：{type(self.upgrade_error).__name__}: {self.upgrade_error}")
        with psycopg.connect(**_kw(TEST_DB)) as c:
            mx = c.execute("SELECT COALESCE(max(run_id),0) FROM app_layer.workflow_runs").fetchone()[0]
            new_id = c.execute(
                "INSERT INTO app_layer.workflow_runs (run_type) VALUES ('probe:identity') RETURNING run_id"
            ).fetchone()[0]
            c.rollback()  # 只探測序列，不污染共用 DB
        self.assertGreater(
            new_id, mx,
            f"IDENTITY 未推進：新 run_id={new_id} <= 既有 max={mx}，default 會與遷入 ID 碰撞")

    def test_workspace_metadata_preserved_and_restored(self):
        """workspaces 稽核欄升級須存 settings_json、downgrade 逐值還原（現升級即遺失）。"""
        if self.upgrade_error is not None:
            self.fail(f"upgrade head 未可執行：{type(self.upgrade_error).__name__}: {self.upgrade_error}")
        s = self._scalar("SELECT settings_json FROM app_layer.workspaces WHERE workspace_id=900001") or {}
        self.assertEqual(s.get("created_by"), "test", "created_by 未存入 settings_json（升級即遺失）")
        for k in ("created_at", "updated_at", "archived_at"):
            self.assertIn(k, s, f"{k} 未存入 settings_json（升級即遺失）")
        cfg = _alembic_cfg()
        try:
            command.downgrade(cfg, BASE_REV)
            self.assertEqual(
                self._scalar("SELECT created_by FROM app_layer.workspaces WHERE workspace_id=900001"),
                "test", "downgrade 未還原 created_by")
            self.assertEqual(
                self._scalar("SELECT to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') "
                             "FROM app_layer.workspaces WHERE workspace_id=900001"),
                "2020-01-02 03:04:05", "downgrade 未逐值還原 created_at")
        finally:
            command.upgrade(cfg, "head")  # 還原共用測試 DB 至 head


if __name__ == "__main__":
    unittest.main()
