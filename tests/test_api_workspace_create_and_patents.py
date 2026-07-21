"""驗收：POST /api/v1/workspaces（建立一般 workspace）與
GET /api/v1/workspaces/{id}/patents（分頁列成員、可選 keyword）。

每個 test 以 UUID 產生跨執行唯一的 workspace_name 與 created_by，tearDown 只依「本次
執行的 created_by」清理自建資料，不依賴固定 marker、不動其他執行留下的資料。
覆蓋：建立成功／去重／缺專利 422／撞名 409／失敗不留半成品、成員 shape（含
applicant_display_name 與完整度旗標）、分頁、patent_number 與 applicant 搜尋、404、
非法參數 422，以及「成員寫入拋錯不得 commit」的 mock 單元測試。
"""
from __future__ import annotations

import unittest
import uuid
from pathlib import Path
from unittest import mock

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from backend.app.app_layer import workspace_create
from backend.app.main import app


PREFIX = "/api/v1"
client = TestClient(app)

# 兩個來源文本欄（鏡射 clustering.sources SOURCE_SPECS.source_column），供旗標期望值查核。
TECH_COL = "獨立項[KR,JP,US,CN,EP,IN]"
EFFECT_COL = "效果 摘要[US,EP,PCT,JP,KR,CN,TW]"


def _connect():
    """開一條測試用直連（灌前置與清理用；查詢走 API/池）。"""
    import psycopg

    from backend.app.db.connection import get_connection_kwargs

    return psycopg.connect(**get_connection_kwargs())


class WorkspaceCreateAndPatentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """載入 .env、取既有 patent id 供建立成員；DB 不可用則 skip。"""
        import psycopg

        from backend.app.db.connection import get_connection_kwargs

        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
        try:
            with psycopg.connect(**get_connection_kwargs(), connect_timeout=3) as conn:
                cls.pids = [
                    int(r[0])
                    for r in conn.execute(
                        "SELECT id FROM core_layer.patents ORDER BY id LIMIT 6"
                    ).fetchall()
                ]
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"DB unreachable: {exc}")
        if len(cls.pids) < 6:
            raise unittest.SkipTest("need at least 6 patents")

    def setUp(self):
        # 每個 test 一組 UUID：workspace_name 與 created_by 皆嵌入，跨執行唯一。
        self.run = uuid.uuid4().hex
        self.created_by = f"vc_{self.run}"
        self._seq = 0

    def tearDown(self):
        """只刪本次執行 created_by 的 workspace；workspace_patents 隨 workspace CASCADE。"""
        with _connect() as conn:
            conn.execute("DELETE FROM app_layer.workspaces WHERE created_by = %s", (self.created_by,))
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
        with _connect() as conn:
            n = conn.execute(
                "SELECT count(*) FROM app_layer.workspaces WHERE workspace_name = %s", (name,)
            ).fetchone()[0]
        self.assertEqual(n, 0)

    # ── 成員 shape（含新欄位）────────────────────────────
    def test_list_patents_shape(self):
        """成員回 items/total/limit/offset；每筆含既有欄位與完整度旗標，依 patent_id 升冪。"""
        wid = self._create_ws(self.pids[:6])
        body = self._patents(wid, limit=200).json()
        self.assertEqual(body["total"], 6)
        self.assertEqual((body["limit"], body["offset"]), (200, 0))
        self.assertEqual(len(body["items"]), 6)
        first = body["items"][0]
        self.assertEqual(
            set(first.keys()),
            {
                "patent_id",
                "patent_number",
                "title",
                "country_code",
                "applicant_display_name",
                "has_technical_text",
                "has_effect_text",
            },
        )
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
        with _connect() as conn:
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
    """mock 單元測試（不連 DB）：workspace 已 INSERT 後成員寫入拋錯，不得 commit。"""

    def test_member_insert_error_does_not_commit(self):
        """驗證：驗證與 workspace INSERT 皆過、executemany 成員寫入拋錯 → 例外外拋且 commit 未被呼叫。"""
        fake_cur = mock.MagicMock()
        # 驗證專利存在：兩個 id 都在。
        fake_cur.fetchall.return_value = [{"id": 1}, {"id": 2}]
        # workspace INSERT ... RETURNING：回一個 workspace_id（代表 workspace 已 INSERT）。
        fake_cur.fetchone.return_value = {"workspace_id": 12345}
        # 成員寫入拋錯。
        fake_cur.executemany.side_effect = RuntimeError("member insert boom")

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

        fake_cur.executemany.assert_called_once()  # 確有嘗試寫成員
        fake_conn.commit.assert_not_called()        # 但整筆未 commit


if __name__ == "__main__":
    unittest.main()
