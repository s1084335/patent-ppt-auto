"""初階篩選：裁決與封存（切片 D，PRE-005／PRE-006／CLU-017）。

## 🔴 本檔守的核心：兩條線的「保留」語意刻意不同

使用者 2026-08-21 裁決「分開：初篩記住、AI 線維持重判」。理由是**判斷依據不同**：

| | AI 線（`ai:irrelevant_filter`） | 初階篩選 |
|---|---|---|
| 判斷依據 | 主題結構（分群結果） | 關鍵字比對 |
| 重跑後依據會變嗎 | **會**（重新分群，主題全變） | **不會**（PRE-001 明訂重跑可重現） |
| 重新列出有意義嗎 | 有 | 沒有——同樣的詞、同樣的資料，答案必定一樣 |

⇒ **儲存統一**（`keep_patents` 一律寫 `status='kept'`），
**寫入端各自決定要不要尊重它**：初篩跳過 `kept`，AI 可覆蓋 `kept`。

⚠ 這個「誰決定要不要重問」寫在**寫入端**而不是保留端，是刻意的：
理由屬於「這條線的判讀依據會不會變」，那是寫入端的知識。

## ⚠ 推翻 0036 的「保留＝刪列」

0036 反對第三種狀態，理由是「每個查排除清單的地方都要多一個過濾條件」。
動工前窮舉全庫 11 個查詢，**每一個都明確指定 status**，故該擔憂不成立。
🔴 但這個性質要用結構測試守住——見 `test_every_exclusion_query_filters_status`。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.types.json import Jsonb

TEST_DB = "patent_ppt_prefilter_dec"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLAIM_COL = "獨立項[KR,JP,US,CN,EP,IN]"

CORPUS = [
    (2001, "Lawn mower blade", None, None),
    (2002, None, "mowing apparatus", None),
    (2003, None, None, "the mower deck"),
    (2004, "Internal combustion engine", None, None),   # 不該被 mow 命中
    (2005, "Blade holder", None, None),
]
MEMBERS = [pid for pid, *_ in CORPUS]


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
    from backend.app.db import connection

    if getattr(connection, "_pool", None) is not None:
        try:
            connection._pool.close()
        except Exception:  # noqa: BLE001
            pass
        connection._pool = None


def _assert_pool_targets_test_db() -> None:
    """護欄：連線池必須正好指向本檔的拋棄式測試庫。

    🔴 這條同時有兩個作用，缺一不可：
    ① **驗證**——池指錯地方（正式庫或 `conftest` 釘的 `patent_ppt_test`）當場紅
    ② **強制在此刻建池**——env 此時是對的；不建的話池會在第一次 API 呼叫時
       才建，而那時已經隔了幾層框架，指錯了也看不出是誰造成的

    ⚠ 本檔第一版漏了它，症狀是端點回
    `UndefinedTable: workspace_negative_keywords does not exist`
    ——看起來像 migration 沒跑，實際是池連到別的庫。
    """
    from backend.app.db.connection import get_pool

    with get_pool().connection() as c:
        with c.cursor() as cur:
            cur.execute("SELECT current_database()")
            actual = cur.fetchone()[0]
    if actual != TEST_DB:
        raise AssertionError(
            f"連線池指向 {actual!r}，不是本檔的測試庫 {TEST_DB!r}")


class PrefilterDecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        _reset_pool()
        command.upgrade(_alembic_cfg(), "head")
        _assert_pool_targets_test_db()

    @classmethod
    def tearDownClass(cls):
        _reset_pool()
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
            cur.execute("DELETE FROM derived_layer.workspace_excluded_patents")
            cur.execute("DELETE FROM derived_layer.workspace_negative_keywords")
            cur.execute("DELETE FROM derived_layer.report_patent_base")
            cur.execute("DELETE FROM app_layer.workspaces")
            cur.execute("DELETE FROM core_layer.patents")
            cur.execute(
                "INSERT INTO app_layer.workspaces "
                "(workspace_id, workspace_name, is_global, patent_ids_json) "
                "VALUES (901, 'WS-A', false, %s)", (Jsonb(MEMBERS),))
            cur.executemany("INSERT INTO core_layer.patents (id) VALUES (%s)",
                            [(p,) for p in MEMBERS])
            cur.executemany(
                f'INSERT INTO derived_layer.report_patent_base '
                f'(patent_id, title, abstract, "{CLAIM_COL}") VALUES (%s,%s,%s,%s)',
                CORPUS)
        self._add_keyword("割草", ["mow"])

    def _add_keyword(self, term, terms):
        from backend.app.prefilter import keywords as kw

        row = kw.create_keyword(901, term, conn=self.conn)
        kw.update_keyword(row["keyword_id"], match_terms=terms,
                          terms_confirmed=True, conn=self.conn)
        return row["keyword_id"]

    def _statuses(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT patent_id, status, source, reason "
                "FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id = 901 ORDER BY patent_id")
            return {r[0]: {"status": r[1], "source": r[2], "reason": r[3]}
                    for r in cur.fetchall()}

    # ── D.5 寫入 pending ────────────────────────────────
    def test_apply_writes_pending_with_reason(self):
        """命中寫 pending，reason 記錄命中的關鍵字與比對詞（PRE-005 可追溯）。"""
        from backend.app.prefilter import decisions

        n = decisions.apply_prefilter(901, conn=self.conn)
        self.assertEqual(n, 3, "mow 應命中 2001／2002／2003")
        rows = self._statuses()
        self.assertEqual(sorted(rows), [2001, 2002, 2003])
        for pid in (2001, 2002, 2003):
            self.assertEqual(rows[pid]["status"], "pending")
            self.assertEqual(rows[pid]["source"], "prefilter")
            self.assertIn("割草", rows[pid]["reason"], "reason 沒記關鍵字")
            self.assertIn("mow", rows[pid]["reason"], "reason 沒記比對詞")

    # ── D.1 pending 不影響分群母體 ──────────────────────
    def test_pending_does_not_affect_analysis_members(self):
        from backend.app.clustering.exclusions import analysis_member_patent_ids
        from backend.app.prefilter import decisions

        decisions.apply_prefilter(901, conn=self.conn)
        self.assertEqual(
            analysis_member_patent_ids(901, conn=self.conn), MEMBERS,
            "待裁決狀態影響了分析母體——pending 不該扣除")

    # ── D.2 封存後母體 = 成員 − 已封存 ──────────────────
    def test_confirmed_exclusion_reduces_analysis_members(self):
        from backend.app.clustering.exclusions import (
            analysis_member_patent_ids,
            confirm_exclusions,
        )
        from backend.app.prefilter import decisions

        decisions.apply_prefilter(901, conn=self.conn)
        confirm_exclusions(901, [2001, 2002], conn=self.conn)
        self.assertEqual(
            analysis_member_patent_ids(901, conn=self.conn),
            [2003, 2004, 2005],
            "封存後分析母體不等於 成員 − 已封存")

    # ── D.3 封存後不在瀏覽清單、在剔除名單可見 ──────────
    def test_archived_hidden_from_browse_but_listed(self):
        from backend.app.clustering.exclusions import (
            confirm_exclusions,
            excluded_patent_rows,
        )
        from backend.app.prefilter import decisions

        decisions.apply_prefilter(901, conn=self.conn)
        confirm_exclusions(901, [2001], conn=self.conn)

        visible = decisions.browsable_patent_ids(901, conn=self.conn)
        self.assertNotIn(2001, visible, "已封存者仍出現在瀏覽清單")
        self.assertEqual(visible, [2002, 2003, 2004, 2005])

        listed = {r["patent_id"] for r in excluded_patent_rows(901, conn=self.conn)}
        self.assertIn(2001, listed, "已封存者不在剔除名單裡——就找不回來了")

    def test_browsable_does_not_change_member_function_contract(self):
        """🔴 D.7：排除疊在呼叫端，不得改 `display_member_patent_ids` 的語意。"""
        import inspect

        from backend.app.clustering.exclusions import display_member_patent_ids
        from backend.app.prefilter import decisions

        self.assertIn(
            "display_member_patent_ids", inspect.getsource(decisions),
            "沒有沿用成員唯一來源")
        # 該函式的契約是「永遠回全部成員」——不得因本 change 而改變
        from backend.app.clustering.exclusions import confirm_exclusions

        decisions.apply_prefilter(901, conn=self.conn)
        confirm_exclusions(901, [2001], conn=self.conn)
        self.assertEqual(
            display_member_patent_ids(901, conn=self.conn), MEMBERS,
            "display_member_patent_ids 的語意被改了——它的契約是回全部成員")

    # ── D.4 不重複、已保留者不重列（初篩）────────────────
    def test_apply_twice_produces_no_duplicates(self):
        from backend.app.prefilter import decisions

        decisions.apply_prefilter(901, conn=self.conn)
        decisions.apply_prefilter(901, conn=self.conn)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM derived_layer.workspace_excluded_patents "
                "WHERE workspace_id = 901")
            self.assertEqual(cur.fetchone()[0], 3, "重跑產生了重複列")

    def test_kept_is_remembered_not_deleted(self):
        """🔴 推翻 0036：保留改為記住（`status='kept'`），不再刪列。"""
        from backend.app.clustering.exclusions import keep_patents
        from backend.app.prefilter import decisions

        decisions.apply_prefilter(901, conn=self.conn)
        keep_patents(901, [2001], conn=self.conn)
        rows = self._statuses()
        self.assertIn(2001, rows, "保留仍是刪列——記不住誰被保留過")
        self.assertEqual(rows[2001]["status"], "kept")

    def test_prefilter_skips_kept(self):
        """🔴 CLU-017：初篩重跑不得把已保留者重新列為待裁決。"""
        from backend.app.clustering.exclusions import keep_patents
        from backend.app.prefilter import decisions

        decisions.apply_prefilter(901, conn=self.conn)
        keep_patents(901, [2001], conn=self.conn)
        decisions.apply_prefilter(901, conn=self.conn)
        self.assertEqual(
            self._statuses()[2001]["status"], "kept",
            "初篩重跑把已保留者打回 pending 了")

    def test_prefilter_skips_already_excluded(self):
        from backend.app.clustering.exclusions import confirm_exclusions
        from backend.app.prefilter import decisions

        decisions.apply_prefilter(901, conn=self.conn)
        confirm_exclusions(901, [2001], conn=self.conn)
        decisions.apply_prefilter(901, conn=self.conn)
        self.assertEqual(
            self._statuses()[2001]["status"], "excluded",
            "初篩重跑把已封存者打回 pending 了")

    # ── AI 線維持重判（使用者 2026-08-21 裁決）──────────
    def test_ai_line_still_relists_kept(self):
        """⚠ 刻意與初篩不同：AI 判讀依據是主題結構，重新分群後依據已變。"""
        from backend.app.clustering.exclusions import keep_patents, store_ai_verdicts

        store_ai_verdicts(901, [{"patent_id": 2004, "verdict": "不相干",
                                 "reason": "測試"}], conn=self.conn)
        keep_patents(901, [2004], conn=self.conn)
        self.assertEqual(self._statuses()[2004]["status"], "kept")

        store_ai_verdicts(901, [{"patent_id": 2004, "verdict": "不相干",
                                 "reason": "重跑"}], conn=self.conn)
        self.assertEqual(
            self._statuses()[2004]["status"], "pending",
            "AI 線重跑沒有重新列出已保留者——與 2026-08-21 裁決不符")

    def test_ai_line_still_respects_excluded(self):
        """已確定排除者，AI 重跑不得打回 pending（0036 既有護欄不變）。"""
        from backend.app.clustering.exclusions import (
            confirm_exclusions,
            store_ai_verdicts,
        )

        store_ai_verdicts(901, [{"patent_id": 2004, "verdict": "不相干"}],
                          conn=self.conn)
        confirm_exclusions(901, [2004], conn=self.conn)
        store_ai_verdicts(901, [{"patent_id": 2004, "verdict": "不相干"}],
                          conn=self.conn)
        self.assertEqual(self._statuses()[2004]["status"], "excluded")

    # ── 結構守門：0036 真正擔心的事 ─────────────────────
    def test_kept_never_leaks_into_any_public_list(self):
        """🔴 已保留者不得出現在任何對外清單。

        0036 反對第三種狀態的理由是「每個查排除清單的地方都要多一個過濾條件」。
        ⚠ 第一版我用正規式掃原始碼驗這件事，結果抓到 `runner.py` 的 docstring
        ——**結構猜測不如行為驗證**。這裡直接列舉全部對外函式，逐一確認。

        日後新增一個不帶 status 條件的查詢，只要它有對外函式，這裡就會紅。
        """
        from backend.app.clustering.exclusions import (
            analysis_member_patent_ids,
            excluded_patent_ids,
            excluded_patent_rows,
            keep_patents,
            pending_reviews,
        )
        from backend.app.prefilter import decisions

        decisions.apply_prefilter(901, conn=self.conn)
        keep_patents(901, [2001], conn=self.conn)
        self.assertEqual(self._statuses()[2001]["status"], "kept")

        checks = {
            "excluded_patent_ids（分析扣除用）":
                excluded_patent_ids(901, conn=self.conn),
            "excluded_patent_rows（剔除名單）":
                [r["patent_id"] for r in excluded_patent_rows(901, conn=self.conn)],
            "pending_reviews（待裁決清單）":
                [r["patent_id"] for r in pending_reviews(901, conn=self.conn)],
        }
        for name, ids in checks.items():
            with self.subTest(fn=name):
                self.assertNotIn(2001, list(ids), f"已保留者出現在 {name}")

        # 反向：它**應該**仍在分析母體與瀏覽清單裡（保留＝留著用）
        self.assertIn(2001, analysis_member_patent_ids(901, conn=self.conn),
                      "已保留者被踢出分析母體了——保留的語意是留著")
        self.assertIn(2001, decisions.browsable_patent_ids(901, conn=self.conn),
                      "已保留者不在瀏覽清單裡")


class PrefilterApiTests(PrefilterDecisionTests):
    """初階篩選的三支端點（預覽／待辦數／套用）。

    ⚠ 端點會呼叫已測過的函式，但**接線本身要驗**——「函式對了」不等於
    「端點接對了」。2026-08-21 已經踩過一次：路徑帶了 workspace_id 卻不用。
    """

    def _client(self):
        from fastapi.testclient import TestClient

        from backend.app.main import app

        return TestClient(app)

    def test_preview_endpoint_lists_zero_hits(self):
        """🔴 零命中要回 0 而不省略（PRE-004）。"""
        self._add_keyword("完全沒有", ["zzzznotfound"])
        c = self._client()
        r = c.get("/api/v1/workspaces/901/prefilter/preview")
        self.assertEqual(r.status_code, 200, r.text)
        by_term = {i["original_term"]: i["patent_count"] for i in r.json()["items"]}
        self.assertEqual(by_term["割草"], 3)
        self.assertEqual(by_term["完全沒有"], 0, "零命中被省略了")

    def test_summary_counts_come_from_backend(self):
        """待辦數由後端算——前端自數會變成第二份計數邏輯。"""
        from backend.app.prefilter import keywords as kw

        kw.create_keyword(901, "未確認的詞", conn=self.conn)   # 未確認 → 算待辦
        c = self._client()
        body = c.get("/api/v1/workspaces/901/prefilter/summary").json()
        self.assertEqual(body["keyword_count"], 2)
        self.assertEqual(body["unconfirmed_count"], 1)
        self.assertEqual(body["pending_count"], 0)
        self.assertEqual(body["todo_count"], 1)

        c.post("/api/v1/workspaces/901/prefilter/apply")
        body = c.get("/api/v1/workspaces/901/prefilter/summary").json()
        self.assertEqual(body["pending_count"], 3, "套用後待裁決數沒更新")
        self.assertEqual(body["todo_count"], 4, "todo_count 不是兩者之和")

    def test_apply_endpoint_writes_pending_only(self):
        c = self._client()
        r = c.post("/api/v1/workspaces/901/prefilter/apply")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["matched_count"], 3)
        self.assertEqual(
            {v["status"] for v in self._statuses().values()}, {"pending"},
            "套用端點寫出了非 pending 的狀態——初篩不決定正式資料")

    def test_apply_is_idempotent_via_endpoint(self):
        c = self._client()
        c.post("/api/v1/workspaces/901/prefilter/apply")
        c.post("/api/v1/workspaces/901/prefilter/apply")
        self.assertEqual(len(self._statuses()), 3, "重複套用產生重複列")


if __name__ == "__main__":
    unittest.main()
