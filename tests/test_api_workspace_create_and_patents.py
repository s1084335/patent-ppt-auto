"""驗收：POST /api/v1/workspaces（建立一般 workspace）與
GET /api/v1/workspaces/{id}/patents（分頁列成員、可選 keyword）。

0021 對齊：以拋棄式 DB patent_ppt_apiwscreate（upgrade head）驗證，絕不碰正式庫
patent_ppt。成員專利存 app_layer.workspaces.patent_ids_json（bigint 陣列），不再有
workspace_patents 明細表。模組層自建可控 core_layer.patents fixture（含技術/功效文本、
patent_number 與一筆 report_patent_base 申請人），供成員 shape、旗標與 keyword 斷言。
覆蓋：建立成功／去重／缺專利 422／撞名 409／失敗不留半成品、成員 shape（含
applicant_display_name 與完整度旗標）、分頁、patent_number 與 applicant 搜尋、404、
非法參數 422，以及「workspace INSERT 拋錯不得 commit」的 mock 單元測試。
"""
from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path
from unittest import mock

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from backend.app.app_layer import workspace_create
from backend.app.main import app


PREFIX = "/api/v1"
TEST_DB = "patent_ppt_apiwscreate"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

# 兩個來源文本欄（鏡射 clustering.sources SOURCE_SPECS.source_column），供旗標期望值查核。
TECH_COL = "獨立項[KR,JP,US,CN,EP,IN]"
EFFECT_COL = "效果 摘要[US,EP,PCT,JP,KR,CN,TW]"

# fixture 專利 id（避開正式資料範圍；本測試只在拋棄式 DB 內灌這些）。
PIDS = [910001, 910002, 910003, 910004, 910005, 910006]

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組出連測試庫的 psycopg 參數（與其他 0021 API 測試同源）。"""
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
    """關閉並清空 lazy 連線池單例，讓 get_pool() 依目前 env 重建（避免綁到別庫）。"""
    from backend.app.db import connection

    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:  # noqa: BLE001
            pass
        connection._pool = None


def _seed_patents():
    """灌可控 core_layer.patents fixture：每筆有技術/功效文本與 patent_number，
    第一筆另補 report_patent_base 申請人供 applicant keyword 斷言。"""
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        for i, pid in enumerate(PIDS):
            conn.execute(
                f'''
                INSERT INTO core_layer.patents
                    (id, title, country_code, "授權公告號", "{TECH_COL}", "{EFFECT_COL}")
                VALUES (%s, %s, 'TW', %s, %s, %s)
                ''',
                (
                    pid,
                    f"fixture patent {i}",
                    f"TW{pid}B",
                    f"technical claim text {i}",
                    f"effect summary text {i}",
                ),
            )
        conn.execute(
            "INSERT INTO derived_layer.report_patent_base (patent_id, applicant_display_name) "
            "VALUES (%s, %s)",
            (PIDS[0], "REXON INDUSTRIAL"),
        )
        conn.commit()


def setUpModule():
    """建拋棄式 DB → upgrade head → 灌 patent fixture；admin 不可用則整組 skip。"""
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
    os.environ["PGDATABASE"] = TEST_DB
    _reset_pool()

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    _seed_patents()


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


class WorkspaceCreateAndPatentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pids = PIDS

    def setUp(self):
        # 每個 test 一組 UUID：workspace_name 與 created_by 皆嵌入，跨執行唯一。
        self.run = uuid.uuid4().hex
        self.created_by = f"vc_{self.run}"
        self._seq = 0

    def tearDown(self):
        """只刪本次執行 created_by 的 workspace（0021 審計 created_by 存 settings_json）。"""
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            conn.execute(
                "DELETE FROM app_layer.workspaces WHERE settings_json->>'created_by' = %s",
                (self.created_by,),
            )
            conn.commit()

    def _name(self, tag: str) -> str:
        self._seq += 1
        return f"vc_{self.run}_{tag}_{self._seq}"

    def _create(self, patent_ids, name=None, description=None):
        body = {
            "workspace_name": name or self._name("ws"),
            "patent_ids": patent_ids,
            "created_by": self.created_by,
        }
        if description is not None:
            body["description"] = description
        return client.post(f"{PREFIX}/workspaces", json=body)

    def _patents(self, workspace_id, **params):
        return client.get(f"{PREFIX}/workspaces/{workspace_id}/patents", params=params)

    def _create_ws(self, patent_ids) -> int:
        resp = self._create(patent_ids)
        self.assertEqual(resp.status_code, 200)
        return resp.json()["workspace_id"]

    # ── 建立成功 ─────────────────────────────────────────
    def test_create_success(self):
        """建立成功回 workspace_id／name／patent_count，成員數一致。"""
        name = self._name("ok")
        resp = self._create([self.pids[0], self.pids[1], self.pids[2]], name=name)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["workspace_name"], name)
        self.assertEqual(body["patent_count"], 3)
        self.assertIsInstance(body["workspace_id"], int)
        self.assertEqual(self._patents(body["workspace_id"], limit=200).json()["total"], 3)

    def test_create_dedup(self):
        """重複 patent_id 去重後 patent_count 與實際成員數一致。"""
        resp = self._create([self.pids[0], self.pids[0], self.pids[1]])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["patent_count"], 2)
        self.assertEqual(self._patents(resp.json()["workspace_id"], limit=200).json()["total"], 2)

    def test_create_persists_bigint_patent_ids_json(self):
        """成員以 bigint 陣列存 patent_ids_json，讀寫形狀一致（jsonb_array_elements→::bigint）。"""
        wid = self._create_ws([self.pids[0], self.pids[1]])
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            ids = conn.execute(
                "SELECT patent_ids_json FROM app_layer.workspaces WHERE workspace_id = %s",
                (wid,),
            ).fetchone()[0]
        self.assertEqual(sorted(int(v) for v in ids), sorted([self.pids[0], self.pids[1]]))

    # ── 輸入錯誤 422 ─────────────────────────────────────
    def test_create_missing_patent_422(self):
        """有不存在的 patent_id → 422。"""
        self.assertEqual(self._create([self.pids[0], 999_999_999]).status_code, 422)

    def test_create_empty_patent_ids_422(self):
        """patent_ids 為空 → 422（pydantic min_length=1）。"""
        resp = client.post(
            f"{PREFIX}/workspaces",
            json={"workspace_name": self._name("empty"), "patent_ids": [], "created_by": self.created_by},
        )
        self.assertEqual(resp.status_code, 422)

    # ── 撞名 409 ─────────────────────────────────────────
    def test_create_name_conflict_409(self):
        """同名再建 → 409。"""
        name = self._name("dup")
        self.assertEqual(self._create([self.pids[0]], name=name).status_code, 200)
        self.assertEqual(self._create([self.pids[1]], name=name).status_code, 409)

    # ── 失敗不留半成品 ───────────────────────────────────
    def test_create_missing_patent_leaves_no_workspace(self):
        """缺專利導致 422 時該名稱 workspace 不應被建立（整筆 rollback）。"""
        name = self._name("partial")
        self.assertEqual(self._create([self.pids[0], 999_999_999], name=name).status_code, 422)
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            n = conn.execute(
                "SELECT count(*) FROM app_layer.workspaces WHERE workspace_name = %s", (name,)
            ).fetchone()[0]
        self.assertEqual(n, 0)

    # ── 成員 shape（含新欄位）────────────────────────────
    def test_list_patents_shape(self):
        """成員回 items/total/limit/offset；每筆含既有欄位與完整度旗標，依 patent_id 升冪。

        2026-07-24 起回應另含 2026-07-23 定案的顯示欄位（分類區與專利總覽共用同一組欄位，
        見 test_api_patents_display_fields），故改驗「必含這組基本欄」而非精確等於——
        顯示欄位增減由該檔負責，兩邊不重複維護同一份欄位清單。
        """
        wid = self._create_ws(self.pids[:6])
        body = self._patents(wid, limit=200).json()
        self.assertEqual(body["total"], 6)
        self.assertEqual((body["limit"], body["offset"]), (200, 0))
        self.assertEqual(len(body["items"]), 6)
        first = body["items"][0]
        self.assertLessEqual(
            {
                "patent_id",
                "patent_number",
                "title",
                "country_code",
                "applicant_display_name",
                "has_technical_text",
                "has_effect_text",
                "topic_key",
                "topic_label",
            },
            set(first.keys()),
        )
        # 無分群 workspace：所屬主題欄應為 None（未分類）。
        for it in body["items"]:
            self.assertIsNone(it["topic_key"])
            self.assertIsNone(it["topic_label"])
        # 旗標須為 bool。
        for it in body["items"]:
            self.assertIsInstance(it["has_technical_text"], bool)
            self.assertIsInstance(it["has_effect_text"], bool)
        ids = [it["patent_id"] for it in body["items"]]
        self.assertEqual(ids, sorted(ids))

    def test_list_patents_pagination(self):
        """分頁切片與全量一致，total 不受分頁影響。"""
        wid = self._create_ws(self.pids[:6])

        def page(**params):
            r = self._patents(wid, **params).json()
            return r["total"], [it["patent_id"] for it in r["items"]]

        total_full, full = page(limit=200)
        self.assertEqual(total_full, 6)
        t0, p0 = page(limit=3, offset=0)
        t1, p1 = page(limit=3, offset=3)
        self.assertEqual((t0, t1), (6, 6))
        self.assertEqual(p0, full[0:3])
        self.assertEqual(p1, full[3:6])

    # ── keyword：patent_number 與 applicant ─────────────
    def test_list_patents_keyword_patent_number(self):
        """keyword 命中 patent_number 時只回符合成員；查無時 total=0。"""
        wid = self._create_ws(self.pids[:6])
        rows = self._patents(wid, limit=200).json()["items"]
        sample = next((it for it in rows if it["patent_number"]), None)
        self.assertIsNotNone(sample, "測試資料需至少一筆有 patent_number")
        token = sample["patent_number"][:4]
        hit = self._patents(wid, keyword=token, limit=200).json()
        self.assertGreaterEqual(hit["total"], 1)
        self.assertIn(sample["patent_id"], [it["patent_id"] for it in hit["items"]])
        miss = self._patents(wid, keyword="zzz_no_match_xyz").json()
        self.assertEqual(miss["total"], 0)
        self.assertEqual(miss["items"], [])

    def test_list_patents_keyword_applicant(self):
        """keyword 亦搜尋 applicant_display_name：用申請人子字串可命中對應專利。"""
        wid = self._create_ws(self.pids[:6])
        rows = self._patents(wid, limit=200).json()["items"]
        sample = next((it for it in rows if it["applicant_display_name"]), None)
        if sample is None:
            self.skipTest("no patent with applicant_display_name in test data")
        appl = sample["applicant_display_name"]
        token = appl[: max(2, min(4, len(appl)))]
        hit = self._patents(wid, keyword=token, limit=200).json()
        self.assertGreaterEqual(hit["total"], 1)
        self.assertIn(sample["patent_id"], [it["patent_id"] for it in hit["items"]])
        # 命中專利確以 applicant 參與匹配（該筆 applicant 含 token）。
        by_id = {it["patent_id"]: it for it in hit["items"]}
        self.assertIn(token.lower(), (by_id[sample["patent_id"]]["applicant_display_name"] or "").lower())

    # ── 完整度旗標對 DB 真值 ─────────────────────────────
    def test_list_patents_completeness_flags_match_db(self):
        """has_technical_text／has_effect_text 與 DB 來源文本非空狀態一致。"""
        wid = self._create_ws(self.pids[:6])
        items = {it["patent_id"]: it for it in self._patents(wid, limit=200).json()["items"]}
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            expected = {
                int(r[0]): (bool(r[1]), bool(r[2]))
                for r in conn.execute(
                    f'''
                    SELECT id,
                           (NULLIF(BTRIM(p."{TECH_COL}"), '') IS NOT NULL),
                           (NULLIF(BTRIM(p."{EFFECT_COL}"), '') IS NOT NULL)
                    FROM core_layer.patents p
                    WHERE id = ANY(%s)
                    ''',
                    (list(items.keys()),),
                ).fetchall()
            }
        for pid, (exp_tech, exp_eff) in expected.items():
            self.assertEqual(items[pid]["has_technical_text"], exp_tech)
            self.assertEqual(items[pid]["has_effect_text"], exp_eff)

    # ── 404 與非法參數 422 ───────────────────────────────
    def test_list_patents_workspace_not_found_404(self):
        """不存在的 workspace 回 404。"""
        self.assertEqual(self._patents(999_999_999).status_code, 404)

    def test_list_patents_invalid_params_422(self):
        """limit/offset 越界回 422。"""
        wid = self._create_ws(self.pids[:2])
        self.assertEqual(self._patents(wid, limit=0).status_code, 422)
        self.assertEqual(self._patents(wid, limit=201).status_code, 422)
        self.assertEqual(self._patents(wid, offset=-1).status_code, 422)


class WorkspaceCreateCommitGuardTests(unittest.TestCase):
    """mock 單元測試（不連 DB）：workspace INSERT 拋錯時不得 commit（0021 成員已併入
    單筆 INSERT 的 patent_ids_json，不再有獨立成員 executemany）。"""

    def test_workspace_insert_error_does_not_commit(self):
        """驗證：專利存在驗證過、workspace INSERT 拋錯 → 例外外拋且 commit 未被呼叫。"""
        fake_cur = mock.MagicMock()
        # 驗證專利存在：兩個 id 都在。
        fake_cur.fetchall.return_value = [{"id": 1}, {"id": 2}]
        # workspace INSERT 拋錯（0021 建 workspace 即單筆寫入成員與 settings）。
        fake_cur.execute.side_effect = [None, RuntimeError("workspace insert boom")]

        fake_conn = mock.MagicMock()
        cur_cm = fake_conn.cursor.return_value
        cur_cm.__enter__.return_value = fake_cur
        cur_cm.__exit__.return_value = False  # 不吞例外

        fake_pool = mock.MagicMock()
        conn_cm = fake_pool.connection.return_value
        conn_cm.__enter__.return_value = fake_conn
        conn_cm.__exit__.return_value = False  # 不吞例外

        with mock.patch.object(workspace_create, "get_pool", return_value=fake_pool):
            with self.assertRaises(RuntimeError):
                workspace_create.create_workspace(
                    workspace_name="mock_ws", patent_ids=[1, 2], created_by="mock"
                )

        fake_conn.commit.assert_not_called()  # 整筆未 commit


if __name__ == "__main__":
    unittest.main()
