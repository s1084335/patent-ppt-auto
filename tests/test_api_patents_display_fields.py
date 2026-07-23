"""驗收：GET /api/v1/patents 的專利顯示欄位（2026-07-23 定案的顯示欄位規格）。

拋棄式 DB patent_ppt_apipatdisp（upgrade head），絕不碰正式庫 patent_ppt。

規格重點（`.agents/context/patent-display-spec.md`）：
- 顯示欄位分散在 core_layer.patents／patent_attributes／patent_people 三張表，
  清單一次查回，前端不再逐筆補查。
- **欄位一律呈現**：來源實測 0% 的欄（legal_status／最近專利權人）仍須出現在回應 key 中，
  值為 None 即可——日後重匯有值就自動顯示，不需改前端。
- **不得 SELECT 主附圖 bytea**：清單只回 has_figure 布林，圖走 GET /patents/{id}/figure。
- **不得 N+1**：patent_attributes／patent_people 以 JOIN 一次帶回，查詢次數不隨筆數成長。
- patent_attributes 主鍵為 (patent_id, raw_record_id)，同一專利可有多列；取值沿用
  refresh_report_patent_base 既有的「最新非空 raw_record」規則。
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from backend.app.main import app


PREFIX = "/api/v1"
TEST_DB = "patent_ppt_apipatdisp"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

# fixture id（避開正式資料範圍）。
PID_FULL = 940001  # 欄位齊全的一筆
PID_SPARSE = 940002  # 幾乎全空的一筆（驗「欄位一律呈現、值為 None」）
WSID = 940101
RAW_OLD = 940201
RAW_NEW = 940202

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    password = os.getenv("PGPASSWORD")
    if password:
        kw["password"] = password
    return kw


def _reset_pool():
    from backend.app.db import connection

    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:
            pass
        connection._pool = None


def _seed():
    """灌 fixture：一筆欄位齊全、一筆幾乎全空，patent_attributes 刻意兩列（舊/新 raw_record）。"""
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        # patents 主表：齊全那筆填滿本表負責的顯示欄位。
        conn.execute(
            """
            INSERT INTO core_layer.patents
                (id, "授權公告號", "未審查的公開號", "申請號", country_code, patent_type,
                 application_date, application_year, title, title_original, abstract,
                 legal_status, "Orig. IPC(Main)")
            VALUES (%s, 'US94000001B2', 'US2019000001A1', 'US16/000001', 'US', '發明專利',
                    DATE '2019-03-04', 2019, 'Full patent', 'Full patent original',
                    'An abstract body.', 'ALIVE', 'H04L-051/02')
            """,
            (PID_FULL,),
        )
        conn.execute(
            "INSERT INTO core_layer.patents (id, \"申請號\", country_code) VALUES (%s, 'TW94000002', 'TW')",
            (PID_SPARSE,),
        )
        # patent_people：一對一，齊全那筆有申請人／發明人／最近專利權人。
        conn.execute(
            """
            INSERT INTO core_layer.patent_people
                (patent_id, "申請人", "發明人", "最近專利權人[US,JP,KR,CN,CA,AU]")
            VALUES (%s, 'REXON INDUSTRIAL CORP', 'WANG, DA-MING', 'REXON HOLDINGS')
            """,
            (PID_FULL,),
        )
        # raw_records：patent_attributes.raw_record_id 有 FK，須先建。
        for offset, rid in enumerate((RAW_OLD, RAW_NEW)):
            conn.execute(
                """
                INSERT INTO raw_layer.raw_records
                    (id, sheet_name, row_number, raw_data, source_system, source_file_hash)
                VALUES (%s, 'disp', %s, '{}'::jsonb, 'test', 'hash-disp')
                """,
                (rid, offset + 1),
            )
        # patent_attributes：同一專利兩列。舊列有值、新列該欄為空白字元，
        # 驗「取最新非空」而非「取最新列」——WIPS 空欄填的是 ' ' 不是 NULL。
        conn.execute(
            """
            INSERT INTO core_layer.patent_attributes
                (patent_id, raw_record_id, "摘要(原文)", "未審查的公開日", "授權公告日",
                 "優先權號", "優先權國家", "優先權日", "詳細查看連結(登入)",
                 "文圖像文件(PDF)連結", "文獻備註")
            VALUES (%s, %s, 'Original abstract.', '2019-09-05', '2021-01-12',
                    'US62/000001', 'US', DATE '2018-03-05'::text,
                    'https://wips.example/doc/1', 'https://wips.example/pdf/1.pdf',
                    'AI 產生的文獻備註。')
            """,
            (PID_FULL, RAW_OLD),
        )
        conn.execute(
            """
            INSERT INTO core_layer.patent_attributes
                (patent_id, raw_record_id, "摘要(原文)", "詳細查看連結(登入)")
            VALUES (%s, %s, '   ', '   ')
            """,
            (PID_FULL, RAW_NEW),
        )
        conn.execute(
            "INSERT INTO app_layer.workspaces (workspace_id, workspace_name, status, patent_ids_json) "
            "VALUES (%s, %s, 'active', %s::jsonb)",
            (WSID, "DISP WS", json.dumps([PID_FULL, PID_SPARSE])),
        )
        conn.commit()


def setUpModule():
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB
    _reset_pool()

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    _seed()


def tearDownModule():
    _reset_pool()
    for k, v in _prev_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    except Exception:
        pass


def _by_id(items: list[dict]) -> dict[int, dict]:
    return {it["patent_id"]: it for it in items}


class PatentDisplayFieldTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        resp = client.get(f"{PREFIX}/patents", params={"limit": 200})
        assert resp.status_code == 200, resp.text
        cls.items = _by_id(resp.json()["items"])

    def test_display_fields_all_present_on_full_row(self):
        """顯示欄位齊全那筆：每個顯示欄都有 key 且值正確。"""
        it = self.items[PID_FULL]
        expected = {
            "country_code": "US",
            "patent_type": "發明專利",
            "legal_status": "ALIVE",
            "applicant": "REXON INDUSTRIAL CORP",
            "title": "Full patent",
            "title_original": "Full patent original",
            "abstract": "An abstract body.",
            "abstract_original": "Original abstract.",
            "application_number": "US16/000001",
            "application_date": "2019-03-04",
            "application_year": 2019,
            "publication_number": "US2019000001A1",
            "publication_date": "2019-09-05",
            "grant_number": "US94000001B2",
            "grant_date": "2021-01-12",
            "inventor": "WANG, DA-MING",
            "priority_number": "US62/000001",
            "priority_country": "US",
            "priority_date": "2018-03-05",
            "current_owner": "REXON HOLDINGS",
            "orig_ipc_main": "H04L-051/02",
            "detail_url": "https://wips.example/doc/1",
            "pdf_url": "https://wips.example/pdf/1.pdf",
            "patent_note": "AI 產生的文獻備註。",
        }
        for key, value in expected.items():
            with self.subTest(field=key):
                self.assertIn(key, it, f"回應缺少顯示欄位 {key}")
                self.assertEqual(it[key], value)

    def test_fields_present_even_when_source_empty(self):
        """欄位一律呈現：來源全空那筆，每個顯示欄仍須有 key，值為 None。

        使用者定案：legal_status／最近專利權人 來源實測 0%，前端仍須有該欄；
        日後重匯有值就自動顯示，不需再改前端。
        """
        it = self.items[PID_SPARSE]
        for key in (
            "patent_type",
            "legal_status",
            "applicant",
            "title_original",
            "abstract",
            "abstract_original",
            "application_date",
            "application_year",
            "publication_number",
            "publication_date",
            "grant_number",
            "grant_date",
            "inventor",
            "priority_number",
            "priority_country",
            "priority_date",
            "current_owner",
            "orig_ipc_main",
            "detail_url",
            "pdf_url",
            "patent_note",
        ):
            with self.subTest(field=key):
                self.assertIn(key, it, f"來源無值時仍須保留欄位 {key}")
                self.assertIsNone(it[key])

    def test_attribute_picks_latest_non_empty_not_latest_row(self):
        """patent_attributes 一對多：取「最新非空」而非「最新列」。

        fixture 的新列把「摘要(原文)」與「詳細查看連結」填成空白字元 '   '（WIPS 空欄實況），
        若實作取最新列會得到空白，正確結果是舊列的有值內容。
        """
        it = self.items[PID_FULL]
        self.assertEqual(it["abstract_original"], "Original abstract.")
        self.assertEqual(it["detail_url"], "https://wips.example/doc/1")

    def test_topic_label_split_into_two_channel_columns(self):
        """分類標籤拆成兩欄（技術分類／功效分類），不是一欄塞兩個值。

        2026-07-24 使用者定案：一件專利可同時屬技術主題與功效主題，一欄併呈不利閱讀，
        故拆兩欄。欄名由 clustering.sources 的通道常數驅動（不寫死字串）。
        本 fixture 未跑分群，故兩欄皆為 None——「尚未分群該欄留空」亦為契約。
        """
        from backend.app.clustering.sources import SOURCE_FIELD_EFFECT, SOURCE_FIELD_TECHNICAL
        from backend.app.app_layer.patent_queries import topic_label_key

        it = self.items[PID_FULL]
        for source_field in (SOURCE_FIELD_TECHNICAL, SOURCE_FIELD_EFFECT):
            key = topic_label_key(source_field)
            with self.subTest(source_field=source_field):
                self.assertIn(key, it, f"回應缺少 {source_field} 通道的分類欄 {key}")
                self.assertIsNone(it[key])
        # 兩欄必須是不同的 key（不得合併成單一欄）。
        self.assertNotEqual(
            topic_label_key(SOURCE_FIELD_TECHNICAL), topic_label_key(SOURCE_FIELD_EFFECT)
        )

    def test_topic_label_populates_from_global_workspace_run(self):
        """技術／功效欄實際取得分群 label（不是恆為 None 的空殼）。

        前一版只驗「尚未分群為 None」，恆回 None 的實作也會通過；本測試補上有值路徑：
        把 fixture workspace 標成全庫 workspace（總覽的主題來源，2026-07-24 定案），
        灌一個技術通道的 finalize run，驗證：
        - 技術欄取得該 run 的 label；
        - 功效通道無 run，該欄仍為 None（兩通道各自獨立，非共用一欄）。

        ⚠ label 的專利歸屬來自 derived_layer.topic_assignments，**不是** topic_state_json
        裡的 patent_ids（repository 以 assignments 覆寫該欄）。只灌 JSON 不灌 assignments
        會得到空的 patent_ids、整欄為 None——此為實測踩坑，故 fixture 兩者都灌。
        """
        from backend.app.clustering.sources import SOURCE_FIELD_EFFECT, SOURCE_FIELD_TECHNICAL
        from backend.app.app_layer.patent_queries import list_patents, topic_label_key

        run_id = 940301
        state = {
            "topics": [
                {
                    "topic_id": 1,
                    "topic_code": "T1",
                    "label": "阻力調節機構",
                    "status": "active",
                    "patent_ids": [PID_FULL],
                }
            ]
        }
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            conn.execute(
                "UPDATE app_layer.workspaces SET is_global = TRUE WHERE workspace_id = %s",
                (WSID,),
            )
            conn.execute(
                "INSERT INTO app_layer.workflow_runs (run_id, workspace_id, run_type, status) "
                "VALUES (%s, %s, 'clustering', 'succeeded')",
                (run_id, WSID),
            )
            conn.execute(
                "INSERT INTO derived_layer.topic_runs "
                "(run_id, workflow_run_id, source_field, topic_state_json) "
                "VALUES (%s, %s, %s, %s::jsonb)",
                (run_id, run_id, SOURCE_FIELD_TECHNICAL, json.dumps(state)),
            )
            conn.execute(
                "INSERT INTO derived_layer.topic_assignments (run_id, patent_id, topic_key) "
                "VALUES (%s, %s, 'T1')",
                (run_id, PID_FULL),
            )
            conn.commit()
        try:
            items = _by_id(list_patents(limit=200)["items"])
            it = items[PID_FULL]
            self.assertEqual(it[topic_label_key(SOURCE_FIELD_TECHNICAL)], "阻力調節機構")
            self.assertIsNone(
                it[topic_label_key(SOURCE_FIELD_EFFECT)],
                "功效通道無 run 時該欄應留空，不得沿用技術通道的 label",
            )
            # 未被指派的專利即使在同一 workspace，也不得誤掛 label。
            self.assertIsNone(items[PID_SPARSE][topic_label_key(SOURCE_FIELD_TECHNICAL)])
        finally:
            # 還原：本測試改了全庫旗標與分群 run，避免污染同檔其他測試。
            with psycopg.connect(**_kw(TEST_DB)) as conn:
                conn.execute(
                    "DELETE FROM derived_layer.topic_assignments WHERE run_id = %s", (run_id,)
                )
                conn.execute("DELETE FROM derived_layer.topic_runs WHERE run_id = %s", (run_id,))
                conn.execute("DELETE FROM app_layer.workflow_runs WHERE run_id = %s", (run_id,))
                conn.execute(
                    "UPDATE app_layer.workspaces SET is_global = FALSE WHERE workspace_id = %s",
                    (WSID,),
                )
                conn.commit()

    def test_topic_source_workspace_is_single_switch_point(self):
        """取主題的 workspace 來源集中在單一函式，使用者改決策只需改一處。

        2026-07-24 使用者尚未定案「總覽顯示全庫 workspace 主題還是各 workspace 主題」，
        故來源必須參數化：list_patents 接 topic_workspace_id，預設由
        resolve_topic_workspace_id() 決定（唯一切換點）。
        """
        import inspect

        from backend.app.app_layer import patent_queries

        self.assertTrue(
            hasattr(patent_queries, "resolve_topic_workspace_id"),
            "缺少主題 workspace 來源的單一切換點 resolve_topic_workspace_id()",
        )
        params = inspect.signature(patent_queries.list_patents).parameters
        self.assertIn("topic_workspace_id", params, "list_patents 未參數化主題 workspace 來源")

    def test_workspace_patents_share_same_display_fields(self):
        """分類區（workspace 專利清單）與專利總覽**共用同一組顯示欄位**，不做兩套。

        使用者定案：兩區顯示同一組欄位、同一份實作。故 GET /workspaces/{id}/patents
        的每筆也必須含全部顯示欄位（欄位定義來自 patent_queries 的同一份 dict）。
        """
        from backend.app.app_layer.patent_queries import display_field_keys

        resp = client.get(f"{PREFIX}/workspaces/{WSID}/patents", params={"limit": 200})
        self.assertEqual(resp.status_code, 200, resp.text)
        item = _by_id(resp.json()["items"])[PID_FULL]
        for key in display_field_keys():
            with self.subTest(field=key):
                self.assertIn(key, item, f"workspace 專利清單缺少顯示欄位 {key}")
        self.assertEqual(item["applicant"], "REXON INDUSTRIAL CORP")
        self.assertEqual(item["detail_url"], "https://wips.example/doc/1")
        self.assertEqual(item["orig_ipc_main"], "H04L-051/02")
        # 效率紅線同樣適用：清單不得帶 bytea。
        self.assertIn("has_figure", item)

    def test_workspace_patents_no_n_plus_1(self):
        """分類區清單加欄位後同樣不得 N+1（查詢次數不隨筆數成長）。"""
        from backend.app.app_layer import workspace_queries

        counts: list[int] = []
        for limit in (1, 200):
            executed: list[str] = []
            orig = psycopg.Cursor.execute

            def spy(self, query, *args, **kwargs):
                executed.append(str(query))
                return orig(self, query, *args, **kwargs)

            psycopg.Cursor.execute = spy
            try:
                workspace_queries.list_workspace_patents(
                    workspace_id=WSID, limit=limit, offset=0
                )
            finally:
                psycopg.Cursor.execute = orig
            counts.append(len(executed))
        self.assertEqual(counts[0], counts[1], f"查詢次數隨筆數成長（疑似 N+1）：{counts}")

    def test_no_figure_bytea_in_list_response(self):
        """效率紅線：清單不得回主附圖 bytea，只回 has_figure 布林。"""
        it = self.items[PID_FULL]
        self.assertIn("has_figure", it)
        self.assertIsInstance(it["has_figure"], bool)
        for key in it:
            with self.subTest(field=key):
                self.assertNotIn("主附圖", key)
                self.assertNotIn("figure_blob", key)

    def test_no_n_plus_1_after_adding_joins(self):
        """效率紅線：加入 patent_people／patent_attributes 後仍不得 N+1。

        對 limit=1 與 limit=200 各統計 SQL 執行次數，兩者必須相同。
        """
        from backend.app.app_layer import patent_queries

        counts: list[int] = []
        for limit in (1, 200):
            executed: list[str] = []
            orig = psycopg.Cursor.execute

            def spy(self, query, *args, **kwargs):
                executed.append(str(query))
                return orig(self, query, *args, **kwargs)

            psycopg.Cursor.execute = spy
            try:
                patent_queries.list_patents(limit=limit, offset=0)
            finally:
                psycopg.Cursor.execute = orig
            counts.append(len(executed))
        self.assertEqual(counts[0], counts[1], f"查詢次數隨筆數成長（疑似 N+1）：{counts}")


if __name__ == "__main__":
    unittest.main()
