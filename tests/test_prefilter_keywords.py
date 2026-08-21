"""初階篩選：負面關鍵字治理（切片 A，PRE-001）。

## 本檔守的三件事

1. **關鍵字以 workspace 為單位**——A 的關鍵字不得作用於 B。
2. **停用者不參與比對**——保留紀錄但不生效。
3. **全庫 workspace 不得建立關鍵字**——沿用 `CLU-007` 既有限制。

⚠ 「確認狀態」欄在本切片只驗**預設為未確認**；它生效與否的行為在切片 B／C。
🔴 AI 寫入時一律為未確認，只有使用者操作能改——與 `store_ai_verdicts` 只能寫
`pending` 同一個設計。

## ⚠ 本檔的建庫寫法刻意與其他 DB 測試不同

其他測試是「**先改 `os.environ` 再嘗試連線**，失敗就 `raise SkipTest`」——而
`setUpClass` 拋 `SkipTest` 時 `tearDownClass` **不會被呼叫**，環境永久壞掉
（2026-08-21 D-4 的根因，53 個檔同型）。

本檔改成「**連得上才改 env**」：連不上就直接 skip，一個環境變數都沒動過。
⇒ 即使沒有 `conftest.py` 的還原 fixture，本檔也不可能污染後續模組。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_prefilter_kw"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _kw(dbname: str) -> dict:
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    pwd = os.getenv("PGPASSWORD")
    if pwd:
        kw["password"] = pwd
    return kw


def _alembic_cfg() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


def _reset_pool() -> None:
    """丟掉連線池單例，讓下次取用重讀環境變數。

    🔴 **不重設就會連上正式庫**：`connection._pool` 是模組層單例，第一次使用時
    就把連線字串快取住了。測試改了 `PGDATABASE` 也沒用——池還握著舊的。
    2026-08-21 實測：漏了這步，`TestClient` 打的 API **實際連到 Supabase 正式庫**
    （`current_database()` 回 `postgres`、看得到正式的三個 workspace）。
    """
    from backend.app.db import connection

    if getattr(connection, "_pool", None) is not None:
        try:
            connection._pool.close()
        except Exception:  # noqa: BLE001
            pass
        connection._pool = None


def _assert_pool_targets_test_db() -> None:
    """護欄：連線池必須**正好**指向本檔的拋棄式測試庫。

    ⚠ 只驗「不是正式庫」不夠——池也可能指到 `conftest.py` 釘的
    `patent_ppt_test`，症狀是「表在、資料不在」，看起來像程式邏輯錯誤，
    很容易一路往下修錯方向。這裡直接比對 `current_database()`。

    🔴 2026-08-21 實測：漏了 `_reset_pool()` 時，`TestClient` 打的 API
    **實際連到 Supabase 正式庫**（`current_database()` 回 `postgres`、
    看得到正式的三個 workspace）。
    """
    from backend.app.db.connection import get_pool

    with get_pool().connection() as c:
        with c.cursor() as cur:
            cur.execute("SELECT current_database()")
            actual = cur.fetchone()[0]
    if actual != TEST_DB:
        raise AssertionError(
            f"連線池指向 {actual!r}，不是本檔的測試庫 {TEST_DB!r}。"
            "檢查 PGDATABASE／DATABASE_URL 與 _reset_pool() 是否漏呼叫。")


class PrefilterKeywordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 🔴 先連、後改 env——順序就是護欄，見模組 docstring。
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True,
                                 connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")

        cls._prev = {k: os.environ.get(k)
                     for k in ("PGHOST", "PGPORT", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = _kw("postgres")["host"]
        os.environ["PGPORT"] = str(_kw("postgres")["port"])
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB
        _reset_pool()          # 🔴 改完 env 一定要重設池，否則池還握著正式庫
        command.upgrade(_alembic_cfg(), "head")
        _assert_pool_targets_test_db()

    @classmethod
    def tearDownClass(cls):
        _reset_pool()          # 還原 env 前先丟掉指向測試庫的池
        for key, value in getattr(cls, "_prev", {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True,
                                 connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:  # noqa: BLE001
            pass

    def setUp(self):
        self.conn = psycopg.connect(**_kw(TEST_DB), autocommit=True)
        self.addCleanup(self.conn.close)
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM derived_layer.workspace_negative_keywords")
            cur.execute("DELETE FROM app_layer.workspaces")
            # ⚠ 欄名是 workspace_name 不是 name（實查 information_schema，
            #   不要憑印象寫——猜錯的症狀是 UndefinedColumn，還算好認）。
            cur.execute(
                "INSERT INTO app_layer.workspaces "
                "(workspace_id, workspace_name, is_global) "
                "VALUES (901, 'WS-A', false), (902, 'WS-B', false), "
                "(903, '全庫', true)")

    # ── A.1 ───────────────────────────────────────────────
    def test_keyword_belongs_to_one_workspace_only(self):
        """🔴 PRE-001 核心：A 建的關鍵字，B 看不到也用不到。"""
        from backend.app.prefilter import keywords as kw

        kw.create_keyword(901, "割草", conn=self.conn)
        self.assertEqual(
            [r["original_term"] for r in kw.list_keywords(901, conn=self.conn)],
            ["割草"])
        self.assertEqual(
            kw.list_keywords(902, conn=self.conn), [],
            "workspace B 看得到 A 的關鍵字——範圍守門破了")

    def test_active_match_terms_are_workspace_scoped(self):
        """比對詞的取用同樣以 workspace 為界。"""
        from backend.app.prefilter import keywords as kw

        row = kw.create_keyword(901, "割草", conn=self.conn)
        kw.update_keyword(row["keyword_id"], match_terms=["mow"],
                          terms_confirmed=True, conn=self.conn)
        self.assertEqual(kw.active_match_terms(901, conn=self.conn), ["mow"])
        self.assertEqual(kw.active_match_terms(902, conn=self.conn), [])

    # ── A.2 ───────────────────────────────────────────────
    def test_disabled_keyword_does_not_match(self):
        """停用者保留紀錄但不參與比對。"""
        from backend.app.prefilter import keywords as kw

        row = kw.create_keyword(901, "割草", conn=self.conn)
        kw.update_keyword(row["keyword_id"], match_terms=["mow"],
                          terms_confirmed=True, conn=self.conn)
        self.assertEqual(kw.active_match_terms(901, conn=self.conn), ["mow"])

        kw.update_keyword(row["keyword_id"], enabled=False, conn=self.conn)
        self.assertEqual(
            kw.active_match_terms(901, conn=self.conn), [],
            "停用後仍取得比對詞——停用旗標沒生效")
        self.assertEqual(
            len(kw.list_keywords(901, conn=self.conn)), 1,
            "停用不該刪除紀錄")

    # ── 確認狀態預設值（B 的護欄落點，A 先驗預設）─────────
    def test_new_keyword_starts_unconfirmed(self):
        """🔴 未確認的比對詞不得用於比對（PRE-002 的落點在 schema 預設）。"""
        from backend.app.prefilter import keywords as kw

        row = kw.create_keyword(901, "割草", conn=self.conn)
        self.assertFalse(row["terms_confirmed"], "新建關鍵字預設就是已確認——護欄破了")

    def test_unconfirmed_terms_are_not_active(self):
        from backend.app.prefilter import keywords as kw

        row = kw.create_keyword(901, "割草", conn=self.conn)
        kw.update_keyword(row["keyword_id"], match_terms=["mow"], conn=self.conn)
        self.assertEqual(
            kw.active_match_terms(901, conn=self.conn), [],
            "未確認的比對詞被拿去比對了")

    # ── A.5 ───────────────────────────────────────────────
    def test_global_workspace_rejected(self):
        """全庫 workspace 不得建立關鍵字（沿用 CLU-007）。"""
        from backend.app.prefilter import keywords as kw

        with self.assertRaises(kw.PrefilterScopeError):
            kw.create_keyword(903, "割草", conn=self.conn)

    def test_global_check_delegates_to_existing_helper(self):
        """🔴 全庫判定只能有一個定義處——沿用 clustering.exclusions。"""
        import inspect

        from backend.app.prefilter import keywords as kw

        src = inspect.getsource(kw)
        self.assertIn(
            "is_global_workspace", src,
            "沒有沿用既有的 is_global_workspace——全庫判定又多了一份定義")
        self.assertNotIn(
            "is_global\"", src.replace("is_global_workspace", ""),
            "自己查了 is_global 欄——應委派 clustering.exclusions.is_global_workspace")

    # ── 重跑可重現（PRE-001 第三個 scenario）────────────
    def test_repeated_reads_are_stable(self):
        from backend.app.prefilter import keywords as kw

        row = kw.create_keyword(901, "割草", conn=self.conn)
        kw.update_keyword(row["keyword_id"], match_terms=["mow", "lawn"],
                          terms_confirmed=True, conn=self.conn)
        first = kw.active_match_terms(901, conn=self.conn)
        second = kw.active_match_terms(901, conn=self.conn)
        self.assertEqual(first, second, "兩次讀取結果不同——順序不確定")


class PrefilterScopeTests(PrefilterKeywordTests):
    """C2.1：整批專利的範圍描述（PRE-008 的判讀依據）。

    ⚠ 與關鍵字共用 fixture：兩者都是 workspace 級的初階篩選設定，
    同一組 workspace 就夠用——另開測試庫要多花一次建庫時間，換不到隔離價值。

    ## 🔴 為什麼需要這個欄位

    PRE-008 要 AI 判斷「這件跟整批專利的範圍有沒有關係」，但 workspace 只有名稱
    （`description` 欄在 0021 已移除）。「自走式割草機」五個字，AI 判斷不了
    「刀片結構算不算範圍內」。使用者 2026-08-21 裁決：由使用者填一句。

    ## 落點與長度上限

    落 `workspaces.settings_json`。0024／0027／0035 都否決過往這類欄位塞東西，
    判準是「**熱路徑欄位不放不定量資料**」——settings_json 每次查 workspace 都會
    整包拉回。一句話是定量小資料，不違反該判準；但**必須用 code 擋長度**，
    否則「一句話」會變成貼一整份說明書，那就正好踩進去了。
    """

    def test_scope_defaults_to_empty(self):
        """沒設定時回空字串——呼叫端只要判真假，不用各自處理 None。"""
        from backend.app.prefilter import scope

        self.assertEqual(scope.get_scope_description(901, conn=self.conn), "")

    def test_scope_round_trip(self):
        from backend.app.prefilter import scope

        text = "自走式割草機的驅動與刀盤機構，不含手推式與園藝工具"
        scope.set_scope_description(901, text, conn=self.conn)
        self.assertEqual(scope.get_scope_description(901, conn=self.conn), text)

    def test_scope_is_workspace_scoped(self):
        from backend.app.prefilter import scope

        scope.set_scope_description(901, "A 的範圍", conn=self.conn)
        self.assertEqual(scope.get_scope_description(902, conn=self.conn), "")

    def test_scope_strips_whitespace(self):
        from backend.app.prefilter import scope

        scope.set_scope_description(901, "  割草機  \n", conn=self.conn)
        self.assertEqual(scope.get_scope_description(901, conn=self.conn), "割草機")

    def test_scope_can_be_cleared(self):
        """清空要能清乾淨——不得留下空白字串以外的殘留。"""
        from backend.app.prefilter import scope

        scope.set_scope_description(901, "割草機", conn=self.conn)
        scope.set_scope_description(901, "", conn=self.conn)
        self.assertEqual(scope.get_scope_description(901, conn=self.conn), "")

    def test_scope_rejects_overlong(self):
        """🔴 長度上限用 code 擋，不靠約定。

        ⚠ `settings_json` 是熱路徑欄位（每次查 workspace 都整包拉回）。
        0024 否決 `request_json`、0027 否決存 PDF、0035 否決存排除清單，
        同一條判準：**熱路徑欄位不放不定量資料**。
        沒有上限的話「一句話」會變成整份說明書，這欄就從定量變不定量。
        """
        from backend.app.prefilter import scope

        with self.assertRaises(ValueError):
            scope.set_scope_description(
                901, "割" * (scope.MAX_SCOPE_LENGTH + 1), conn=self.conn)

    def test_scope_write_preserves_other_settings(self):
        """🔴 寫入範圍描述不得清掉 settings_json 裡的其他鍵。

        ⚠ `SET settings_json = %s` 會整包覆蓋——別人存在裡面的設定會**靜默消失**，
        而且不會報錯，要等到那個功能壞掉才會發現。必須是合併寫入。
        """
        from psycopg.types.json import Jsonb

        from backend.app.prefilter import scope

        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE app_layer.workspaces SET settings_json = %s "
                "WHERE workspace_id = 901", (Jsonb({"other_feature": {"k": 1}}),))
        scope.set_scope_description(901, "割草機", conn=self.conn)
        with self.conn.cursor() as cur:
            cur.execute("SELECT settings_json FROM app_layer.workspaces "
                        "WHERE workspace_id = 901")
            settings = cur.fetchone()[0]
        self.assertEqual(settings.get("other_feature"), {"k": 1},
                         "寫入範圍描述時把 settings_json 的其他鍵覆蓋掉了")

    def test_scope_rejects_global_workspace(self):
        """全庫不做初階篩選——與關鍵字同一守門。"""
        from backend.app.prefilter import scope

        with self.assertRaises(ValueError):
            scope.set_scope_description(903, "全庫", conn=self.conn)


class PrefilterScopeApiTests(PrefilterKeywordTests):
    """C2.1 的端點。"""

    def _client(self):
        from fastapi.testclient import TestClient

        from backend.app.main import app

        return TestClient(app)

    def test_scope_get_and_put(self):
        c = self._client()
        r = c.get("/api/v1/workspaces/901/prefilter/scope")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["scope_description"], "")

        r = c.put("/api/v1/workspaces/901/prefilter/scope",
                  json={"scope_description": "自走式割草機的驅動與刀盤機構"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(
            c.get("/api/v1/workspaces/901/prefilter/scope").json()["scope_description"],
            "自走式割草機的驅動與刀盤機構")

    def test_scope_overlong_returns_422(self):
        from backend.app.prefilter import scope

        c = self._client()
        r = c.put("/api/v1/workspaces/901/prefilter/scope",
                  json={"scope_description": "割" * (scope.MAX_SCOPE_LENGTH + 1)})
        self.assertEqual(r.status_code, 422, r.text)

    def test_scope_global_workspace_returns_400(self):
        c = self._client()
        r = c.put("/api/v1/workspaces/903/prefilter/scope",
                  json={"scope_description": "全庫"})
        self.assertEqual(r.status_code, 400, r.text)


class PrefilterKeywordApiTests(PrefilterKeywordTests):
    """A.4／A.6：CRUD 端點與 workspace 範圍守門。

    ⚠ 路徑帶了 `workspace_id` 不等於守住了——若 PATCH／DELETE 不驗歸屬，
    知道 `keyword_id` 就能改別的 workspace 的關鍵字。本組專門驗這件事。
    """

    def _client(self):
        from fastapi.testclient import TestClient

        from backend.app.main import app

        return TestClient(app)

    def test_create_and_list_via_api(self):
        c = self._client()
        r = c.post("/api/v1/workspaces/901/negative-keywords",
                   json={"original_term": "割草"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["keyword"]["terms_confirmed"],
                         "端點建出來就是已確認——護欄破了")

        r = c.get("/api/v1/workspaces/901/negative-keywords")
        self.assertEqual([i["original_term"] for i in r.json()["items"]], ["割草"])

    def test_list_is_workspace_scoped(self):
        c = self._client()
        c.post("/api/v1/workspaces/901/negative-keywords",
               json={"original_term": "割草"})
        r = c.get("/api/v1/workspaces/902/negative-keywords")
        self.assertEqual(r.json()["items"], [], "B 的清單看得到 A 的關鍵字")

    def test_global_workspace_rejected_with_400(self):
        c = self._client()
        r = c.post("/api/v1/workspaces/903/negative-keywords",
                   json={"original_term": "割草"})
        self.assertEqual(r.status_code, 400, r.text)

    def test_patch_rejects_cross_workspace(self):
        """🔴 知道 keyword_id 也不能改別的 workspace 的關鍵字。"""
        c = self._client()
        kid = c.post("/api/v1/workspaces/901/negative-keywords",
                     json={"original_term": "割草"}).json()["keyword"]["keyword_id"]
        r = c.patch(f"/api/v1/workspaces/902/negative-keywords/{kid}",
                    json={"enabled": False})
        self.assertEqual(r.status_code, 404, "跨 workspace 更新沒被擋")

    def test_delete_rejects_cross_workspace(self):
        c = self._client()
        kid = c.post("/api/v1/workspaces/901/negative-keywords",
                     json={"original_term": "割草"}).json()["keyword"]["keyword_id"]
        r = c.delete(f"/api/v1/workspaces/902/negative-keywords/{kid}")
        self.assertEqual(r.status_code, 404, "跨 workspace 刪除沒被擋")
        self.assertEqual(
            len(c.get("/api/v1/workspaces/901/negative-keywords").json()["items"]), 1,
            "被跨庫刪掉了")

    def test_manual_terms_path_works_without_ai(self):
        """B.7／PRE-002：AI 轉換失敗或不可用時，使用者仍可自行輸入英文比對詞完成篩選。

        ⚠ 這條路徑**完全不經過 AI job**——所以 AI 掛掉不影響它。
        測試的意義是確保這條路真的通，而不是只在規格裡寫「不阻斷」。
        """
        from backend.app.prefilter import keywords as kw

        c = self._client()
        kid = c.post("/api/v1/workspaces/901/negative-keywords",
                     json={"original_term": "割草"}).json()["keyword"]["keyword_id"]

        r = c.patch(f"/api/v1/workspaces/901/negative-keywords/{kid}",
                    json={"match_terms": ["mow", "lawn"], "terms_confirmed": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(
            kw.active_match_terms(901, conn=self.conn), ["lawn", "mow"],
            "自行輸入並確認後仍取不到比對詞——AI 掛掉就等於整條線不能用")

    def test_create_does_not_accept_confirmed_terms(self):
        """建立端點不得收 match_terms／terms_confirmed——否則可繞過確認流程。"""
        c = self._client()
        r = c.post("/api/v1/workspaces/901/negative-keywords",
                   json={"original_term": "割草", "match_terms": ["mow"],
                         "terms_confirmed": True})
        self.assertEqual(r.status_code, 200, r.text)
        kwrow = r.json()["keyword"]
        self.assertEqual(kwrow["match_terms"], [], "建立時就帶進了比對詞")
        self.assertFalse(kwrow["terms_confirmed"], "建立時就帶進了確認狀態")


if __name__ == "__main__":
    unittest.main()
