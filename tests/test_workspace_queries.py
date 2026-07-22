"""workspace_queries 對齊 0021 schema 的單元驗收（拋棄式 0021 DB，絕不碰 patent_ppt）。

0021 後 app_layer.workspaces 只有 workspace_id/workspace_name/status/patent_ids_json/
settings_json：成員專利＝patent_ids_json 陣列（不再 join workspace_patents），
patent_count＝陣列長度，is_composed＝該 ws 在 legacy_0021.workspace_compose_sources 有記錄。
compose 明細與成員 join 皆改走 legacy_0021 / core_layer。本測試建拋棄式 DB → upgrade head，
直接種 app_layer.workspaces（帶 patent_ids_json）與 legacy compose source，驗證三個查詢函式。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

from backend.app.app_layer import workspace_queries


TEST_DB = "patent_ppt_wsq"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 兩個來源文本欄（鏡射 clustering.sources SOURCE_SPECS.source_column）：技術＝獨立項、功效＝效果摘要。
TECH_COL = "獨立項[KR,JP,US,CN,EP,IN]"
EFFECT_COL = "效果 摘要[US,EP,PCT,JP,KR,CN,TW]"
NUMBER_COL = "授權公告號"  # patent_number COALESCE 的第一順位來源

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組出連測試庫的 psycopg 參數。"""
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
    """關閉並清空 lazy 連線池單例，讓 get_pool() 依目前 env 重建（綁到測試庫，非 patent_ppt）。"""
    from backend.app.db import connection

    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:  # noqa: BLE001
            pass
        connection._pool = None


# 種子 patent：id 於 setUp 動態取得，這裡定義各筆的 number/title/applicant 與兩通道文本旗標期望。
_SEED_PATENTS = [
    # (number, title, applicant, tech_text, effect_text)
    ("US-AAA-111", "Solar panel mounting bracket", "Acme Robotics Inc", "claim tech A", "effect A"),
    ("US-BBB-222", "Battery thermal management", "Beta Energy Corp", "claim tech B", None),
    ("US-CCC-333", "Wireless charging coil", "Gamma Devices Ltd", None, "effect C"),
    ("US-DDD-444", "Gearbox lubrication system", "Delta Machinery Co", None, None),
]


def setUpModule():
    """建拋棄式 0021 DB → upgrade head → 種資料；admin 不可用則整組 skip。"""
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"  # Windows localhost 走 IPv6 會慢
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB  # workspace_queries 走 get_pool()/get_connection_kwargs()
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
    except Exception:  # noqa: BLE001
        pass


# 種子後由 setUpModule 填入，供各 test 斷言使用。
PIDS: list[int] = []
WS_A: int = 0  # 一般 ws（2 件、非組合）
WS_B: int = 0  # 組合 ws（3 件、在 legacy compose source 有記錄）


def _seed():
    """種 4 筆 patents（含 report_patent_base 申請人）、2 筆 workspaces（帶 patent_ids_json）、
    以及一筆 legacy_0021.workspace_compose_sources 讓 WS_B 判為組合。"""
    global PIDS, WS_A, WS_B
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        # patents：只灌 number/title/country/applicant 兩通道文本；其餘欄位留 NULL。
        for number, title, applicant, tech, effect in _SEED_PATENTS:
            pid = int(
                conn.execute(
                    f'''
                    INSERT INTO core_layer.patents
                        ("{NUMBER_COL}", title, country_code, "{TECH_COL}", "{EFFECT_COL}")
                    VALUES (%s, %s, 'US', %s, %s) RETURNING id
                    ''',
                    (number, title, tech, effect),
                ).fetchone()[0]
            )
            PIDS.append(pid)
            # report_patent_base 為 legacy 之上的 VIEW，申請人顯示名種在 legacy 表（只需 patent_id NOT NULL）。
            conn.execute(
                "INSERT INTO legacy_0021.report_patent_base (patent_id, applicant_display_name) VALUES (%s, %s)",
                (pid, applicant),
            )
        # WS_A：一般 ws，成員為前 2 件；is_composed 應為 False。
        WS_A = int(
            conn.execute(
                "INSERT INTO app_layer.workspaces (workspace_name, status, patent_ids_json) "
                "VALUES ('wsq_a', 'active', %s) RETURNING workspace_id",
                (psycopg.types.json.Jsonb(PIDS[:2]),),
            ).fetchone()[0]
        )
        # WS_B：組合 ws，成員為 3 件（含與 A 重疊 1 件，但陣列本身即成員清單）；is_composed 應為 True。
        WS_B = int(
            conn.execute(
                "INSERT INTO app_layer.workspaces (workspace_name, status, patent_ids_json) "
                "VALUES ('wsq_b', 'archived', %s) RETURNING workspace_id",
                (psycopg.types.json.Jsonb(PIDS[1:4]),),
            ).fetchone()[0]
        )
        # legacy compose source：WS_B 由 WS_A 組合而來，讓 is_composed / compose_sources 有料可驗。
        conn.execute(
            "INSERT INTO legacy_0021.workspace_compose_sources "
            "(workspace_id, source_workspace_id, source_patent_count) VALUES (%s, %s, %s)",
            (WS_B, WS_A, 2),
        )
        # WS_A 一個 wips topic run：只指派第一件（PIDS[0]）到 TA1，第二件（PIDS[1]）留未分類。
        # 供 list_workspace_patents 的所屬主題欄斷言（指派者帶 topic_key/label，未指派者 null）。
        state = {"topics": [
            {"topic_id": 1, "topic_code": "TA1", "label": "太陽能支架", "status": "active",
             "topic_kind": "model", "label_source": "model", "doc_count": 1},
        ]}
        run_id = int(conn.execute(
            "INSERT INTO app_layer.workflow_runs (workspace_id, run_type, status) "
            "VALUES (%s, 'clustering:wips_independent_claims', 'succeeded') RETURNING run_id",
            (WS_A,),
        ).fetchone()[0])
        # topic_runs.run_id 無 default（0021 為顯式 BIGINT PK），沿用 workflow_run_id 當 run_id。
        tr_id = run_id
        conn.execute(
            "INSERT INTO derived_layer.topic_runs (run_id, workflow_run_id, source_field, topic_state_json) "
            "VALUES (%s, %s, 'wips_independent_claims', %s)",
            (tr_id, run_id, psycopg.types.json.Jsonb(state)),
        )
        conn.execute(
            "INSERT INTO derived_layer.topic_assignments (run_id, patent_id, topic_key) "
            "VALUES (%s, %s, 'TA1')",
            (tr_id, PIDS[0]),
        )
        conn.commit()


class ListWorkspacesTests(unittest.TestCase):
    """list_workspaces：patent_count＝陣列長度、is_composed、排序 workspace_id DESC、status 過濾。"""

    def test_shape_and_patent_count_and_is_composed(self):
        """回 items/total/limit/offset；A 2 件非組合、B 3 件為組合。"""
        result = workspace_queries.list_workspaces(limit=200, offset=0)
        self.assertEqual((result["limit"], result["offset"]), (200, 0))
        self.assertGreaterEqual(result["total"], 2)
        items = {it["workspace_id"]: it for it in result["items"]}
        self.assertEqual(items[WS_A]["patent_count"], 2)
        self.assertFalse(items[WS_A]["is_composed"])
        self.assertEqual(items[WS_B]["patent_count"], 3)
        self.assertTrue(items[WS_B]["is_composed"])
        # 0021 已無 created_at 欄，投影不得再帶。
        self.assertNotIn("created_at", items[WS_A])

    def test_order_by_workspace_id_desc(self):
        """穩定排序改 workspace_id DESC：WS_B（較晚建、id 較大）排在 WS_A 之前。"""
        ids = [it["workspace_id"] for it in workspace_queries.list_workspaces(limit=200)["items"]]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertLess(ids.index(WS_B), ids.index(WS_A))

    def test_status_filter(self):
        """status 過濾仍有效：archived 只回 WS_B，active 不含 WS_B。"""
        arch = workspace_queries.list_workspaces(limit=200, status="archived")
        arch_ids = [it["workspace_id"] for it in arch["items"]]
        self.assertIn(WS_B, arch_ids)
        self.assertNotIn(WS_A, arch_ids)
        self.assertTrue(all(it["status"] == "archived" for it in arch["items"]))
        active_ids = [it["workspace_id"] for it in workspace_queries.list_workspaces(limit=200, status="active")["items"]]
        self.assertIn(WS_A, active_ids)
        self.assertNotIn(WS_B, active_ids)


class WorkspaceDetailTests(unittest.TestCase):
    """get_workspace_detail：不存在回 None；存在回投影＋compose_sources（走 legacy_0021）。"""

    def test_not_found_returns_none(self):
        self.assertIsNone(workspace_queries.get_workspace_detail(999_999_999))

    def test_general_detail_empty_sources(self):
        """一般 ws：is_composed=False、patent_count=2、compose_sources 空陣列。"""
        detail = workspace_queries.get_workspace_detail(WS_A)
        self.assertEqual(detail["workspace_id"], WS_A)
        self.assertEqual(detail["status"], "active")
        self.assertEqual(detail["patent_count"], 2)
        self.assertFalse(detail["is_composed"])
        self.assertEqual(detail["compose_sources"], [])

    def test_composed_detail_sources(self):
        """組合 ws：is_composed=True，compose_sources 走 legacy_0021，帶來源名稱/狀態/件數。"""
        detail = workspace_queries.get_workspace_detail(WS_B)
        self.assertTrue(detail["is_composed"])
        self.assertEqual(detail["patent_count"], 3)
        srcs = detail["compose_sources"]
        self.assertEqual(len(srcs), 1)
        src = srcs[0]
        self.assertEqual(src["source_workspace_id"], WS_A)
        self.assertEqual(src["workspace_name"], "wsq_a")
        self.assertEqual(src["status"], "active")
        self.assertEqual(src["source_patent_count"], 2)


class ListWorkspacePatentsTests(unittest.TestCase):
    """list_workspace_patents：成員來源改 patent_ids_json（unnest → join core_layer.patents）。"""

    def test_not_found_returns_none(self):
        self.assertIsNone(workspace_queries.list_workspace_patents(workspace_id=999_999_999))

    def test_members_from_patent_ids_json(self):
        """WS_A 兩件成員；shape 與旗標正確、依 patent_id 升冪。"""
        result = workspace_queries.list_workspace_patents(workspace_id=WS_A, limit=200)
        self.assertEqual(result["total"], 2)
        self.assertEqual((result["limit"], result["offset"]), (200, 0))
        items = result["items"]
        self.assertEqual(
            set(items[0].keys()),
            {"patent_id", "patent_number", "title", "country_code",
             "applicant_display_name", "has_technical_text", "has_effect_text",
             "topic_key", "topic_label"},
        )
        ids = [it["patent_id"] for it in items]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids, sorted(PIDS[:2]))
        by_id = {it["patent_id"]: it for it in items}
        # 第 1 筆種子有 tech+effect；第 2 筆只有 tech。
        self.assertTrue(by_id[PIDS[0]]["has_technical_text"])
        self.assertTrue(by_id[PIDS[0]]["has_effect_text"])
        self.assertTrue(by_id[PIDS[1]]["has_technical_text"])
        self.assertFalse(by_id[PIDS[1]]["has_effect_text"])

    def test_keyword_matches_title_number_applicant(self):
        """keyword 對 title/patent_number/applicant_display_name ILIKE；查無回空。"""
        # WS_B 三件：BBB/CCC/DDD。以 title 子字串命中。
        by_title = workspace_queries.list_workspace_patents(workspace_id=WS_B, keyword="Wireless", limit=200)
        self.assertEqual([it["patent_id"] for it in by_title["items"]], [PIDS[2]])
        # 以 patent_number 子字串命中。
        by_number = workspace_queries.list_workspace_patents(workspace_id=WS_B, keyword="DDD", limit=200)
        self.assertEqual([it["patent_id"] for it in by_number["items"]], [PIDS[3]])
        # 以 applicant 子字串命中。
        by_appl = workspace_queries.list_workspace_patents(workspace_id=WS_B, keyword="Gamma", limit=200)
        self.assertEqual([it["patent_id"] for it in by_appl["items"]], [PIDS[2]])
        # 查無。
        miss = workspace_queries.list_workspace_patents(workspace_id=WS_B, keyword="zzz_no_match")
        self.assertEqual(miss["total"], 0)
        self.assertEqual(miss["items"], [])

    def test_pagination(self):
        """分頁切片與全量一致，total 不受分頁影響。"""
        full = workspace_queries.list_workspace_patents(workspace_id=WS_B, limit=200)
        full_ids = [it["patent_id"] for it in full["items"]]
        self.assertEqual(full["total"], 3)
        p0 = workspace_queries.list_workspace_patents(workspace_id=WS_B, limit=2, offset=0)
        p1 = workspace_queries.list_workspace_patents(workspace_id=WS_B, limit=2, offset=2)
        self.assertEqual([it["patent_id"] for it in p0["items"]], full_ids[0:2])
        self.assertEqual([it["patent_id"] for it in p1["items"]], full_ids[2:4])


class ListWorkspacePatentsTopicColumnTests(unittest.TestCase):
    """每筆專利加所屬主題欄：指派者帶 topic_key/topic_label，未指派者為 null。"""

    def test_assigned_patent_has_topic(self):
        """WS_A 第一件（PIDS[0]）指派 TA1，帶 topic_key/topic_label。"""
        items = {it["patent_id"]: it for it in
                 workspace_queries.list_workspace_patents(workspace_id=WS_A, limit=200)["items"]}
        self.assertEqual(items[PIDS[0]]["topic_key"], "TA1")
        self.assertEqual(items[PIDS[0]]["topic_label"], "太陽能支架")

    def test_unassigned_patent_topic_is_null(self):
        """WS_A 第二件（PIDS[1]）未指派，topic_key/topic_label 皆為 None。"""
        items = {it["patent_id"]: it for it in
                 workspace_queries.list_workspace_patents(workspace_id=WS_A, limit=200)["items"]}
        self.assertIsNone(items[PIDS[1]]["topic_key"])
        self.assertIsNone(items[PIDS[1]]["topic_label"])

    def test_workspace_without_clustering_all_null(self):
        """無分群的 WS_B：所有成員 topic_key/topic_label 皆 None（總覽照常顯示）。"""
        items = workspace_queries.list_workspace_patents(workspace_id=WS_B, limit=200)["items"]
        self.assertEqual(len(items), 3)
        self.assertTrue(all(it["topic_key"] is None and it["topic_label"] is None for it in items))


if __name__ == "__main__":
    unittest.main()
