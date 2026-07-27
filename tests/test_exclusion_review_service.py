"""排除清單複核層契約測試（2026-07-27 定案：AI 判讀 → 人工保留／確定）。

規格：AI 只寫得進 pending，寫不進 excluded——AI 不決定正式資料。

鎖住的紅線：
- store_ai_verdicts：AI 判讀一律落 status='pending'、source='ai'，帶 ai_verdict 與理由。
- **pending 不影響分析**：analysis_member_patent_ids 只扣 status='excluded'，
  pending 列照常參與分群與統計（這是 AI 不碰正式資料的關鍵護欄）。
- confirm_exclusions（確定）：pending → excluded，並移除 topic_assignments（歸到「不相干」）。
- keep_patents（保留）：直接刪列——保留＝不在排除清單上，不留第三種 status。
- 人工剔除（exclude_patents）預設寫 status='excluded'、source='manual'，
  與 AI 確定者同在「不相干」桶（使用者定案：兩種來源最終都要出現在不相干標籤）。
- 重跑 AI 判讀不覆蓋已裁決者：已 excluded 的列不被打回 pending。
- pending_reviews：列出待複核清單，帶 ai_verdict／reason 供前端逐筆呈現。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_exclreviewsvc"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


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


class ExclusionReviewServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        command.upgrade(_alembic_cfg(), "head")

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:
            pass

    def setUp(self):
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            c.execute("DELETE FROM derived_layer.workspace_excluded_patents")
            c.execute("DELETE FROM app_layer.workspaces")

    def _workspace(self, conn, name: str, patent_ids: list[int], *, is_global: bool = False) -> int:
        import json

        return conn.execute(
            "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json, is_global) "
            "VALUES (%s, %s, %s) RETURNING workspace_id",
            (name, json.dumps(patent_ids), is_global),
        ).fetchone()[0]

    def _rows(self, conn, workspace_id: int) -> dict[int, tuple[str, str, str | None]]:
        return {
            int(r[0]): (r[1], r[2], r[3])
            for r in conn.execute(
                "SELECT patent_id, status, source, ai_verdict "
                "FROM derived_layer.workspace_excluded_patents WHERE workspace_id=%s",
                (workspace_id,),
            ).fetchall()
        }

    def test_store_ai_verdicts_writes_pending(self):
        """AI 判讀落 status='pending'、source='ai'，帶 ai_verdict 與理由。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._workspace(c, "ws-ai", [11, 12, 13])
            written = exclusions.store_ai_verdicts(
                ws,
                [
                    {"patent_id": 11, "verdict": "不相干", "reason": "與主題無關"},
                    {"patent_id": 12, "verdict": "不相干", "reason": "屬其他技術領域"},
                ],
                conn=c,
            )
            c.commit()
            rows = self._rows(c, ws)
        self.assertEqual(written, 2)
        self.assertEqual(rows[11], ("pending", "ai", "不相干"))
        self.assertEqual(rows[12], ("pending", "ai", "不相干"))

    def test_pending_does_not_affect_analysis(self):
        """pending 不扣除：AI 判讀不影響分群與統計（AI 不決定正式資料）。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._workspace(c, "ws-pending-analysis", [21, 22, 23])
            exclusions.store_ai_verdicts(
                ws, [{"patent_id": 22, "verdict": "不相干", "reason": "AI 認為不相干"}],
                conn=c)
            c.commit()
            members = exclusions.analysis_member_patent_ids(ws, conn=c)
            excluded = exclusions.excluded_patent_ids(ws, conn=c)
        self.assertEqual(members, [21, 22, 23], "pending 不得被分析路徑扣除")
        self.assertEqual(excluded, set(), "excluded_patent_ids 只回已確定排除者")

    def test_confirm_moves_pending_to_excluded(self):
        """確定：pending → excluded，之後分析路徑扣除該筆。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._workspace(c, "ws-confirm", [31, 32, 33])
            exclusions.store_ai_verdicts(
                ws, [{"patent_id": 32, "verdict": "不相干", "reason": "AI 判定"}], conn=c)
            c.commit()
            confirmed = exclusions.confirm_exclusions(ws, [32], conn=c)
            c.commit()
            rows = self._rows(c, ws)
            members = exclusions.analysis_member_patent_ids(ws, conn=c)
        self.assertEqual(confirmed, 1)
        self.assertEqual(rows[32][0], "excluded")
        self.assertEqual(rows[32][1], "ai", "確定後保留原始來源，供追溯是 AI 建議還是人工發起")
        self.assertEqual(members, [31, 33])

    def test_keep_removes_row_entirely(self):
        """保留：直接刪列——保留＝不在排除清單上，不留第三種 status。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._workspace(c, "ws-keep", [41, 42])
            exclusions.store_ai_verdicts(
                ws, [{"patent_id": 42, "verdict": "不相干", "reason": "AI 判定"}], conn=c)
            c.commit()
            kept = exclusions.keep_patents(ws, [42], conn=c)
            c.commit()
            rows = self._rows(c, ws)
            members = exclusions.analysis_member_patent_ids(ws, conn=c)
        self.assertEqual(kept, 1)
        self.assertEqual(rows, {}, "保留後該列應完全移除")
        self.assertEqual(members, [41, 42])

    def test_manual_exclude_defaults_to_excluded_and_manual(self):
        """人工剔除預設 status='excluded'、source='manual'，與 AI 確定者同桶。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._workspace(c, "ws-manual", [51, 52])
            exclusions.exclude_patents(ws, [(51, "人工判定不相干")], conn=c)
            c.commit()
            rows = self._rows(c, ws)
            members = exclusions.analysis_member_patent_ids(ws, conn=c)
        self.assertEqual(rows[51], ("excluded", "manual", None))
        self.assertEqual(members, [52])

    def test_rerun_does_not_overwrite_decided(self):
        """重跑 AI 判讀不覆蓋已裁決者：已 excluded 的列不被打回 pending。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._workspace(c, "ws-rerun", [61, 62])
            exclusions.exclude_patents(ws, [(61, "人工先剔除")], conn=c)
            c.commit()
            exclusions.store_ai_verdicts(
                ws, [{"patent_id": 61, "verdict": "不相干", "reason": "AI 又判一次"}], conn=c)
            c.commit()
            rows = self._rows(c, ws)
        self.assertEqual(
            rows[61][0], "excluded",
            "已確定排除者不得被 AI 重跑打回 pending")
        self.assertEqual(rows[61][1], "manual", "來源仍為人工")

    def test_pending_reviews_lists_for_frontend(self):
        """pending_reviews 回待複核清單，帶 ai_verdict／reason 供逐筆呈現。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._workspace(c, "ws-list", [71, 72, 73])
            exclusions.store_ai_verdicts(
                ws,
                [
                    {"patent_id": 71, "verdict": "不相干", "reason": "理由一"},
                    {"patent_id": 72, "verdict": "不相干", "reason": "理由二"},
                ],
                conn=c,
            )
            exclusions.exclude_patents(ws, [(73, "人工剔除")], conn=c)
            c.commit()
            reviews = exclusions.pending_reviews(ws, conn=c)
        self.assertEqual([r["patent_id"] for r in reviews], [71, 72],
                         "只列 pending，不含已確定排除者")
        self.assertEqual(reviews[0]["reason"], "理由一")
        self.assertEqual(reviews[0]["ai_verdict"], "不相干")

    def test_excluded_patent_ids_only_counts_excluded(self):
        """excluded_patent_ids 只回 status='excluded'，pending 不算。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._workspace(c, "ws-ids", [81, 82])
            exclusions.store_ai_verdicts(
                ws, [{"patent_id": 81, "verdict": "不相干", "reason": "AI"}], conn=c)
            exclusions.exclude_patents(ws, [(82, "人工")], conn=c)
            c.commit()
            ids = exclusions.excluded_patent_ids(ws, conn=c)
        self.assertEqual(ids, {82})

    def test_excluded_patents_lists_both_sources(self):
        """excluded_patent_rows 列「不相干」桶：人工剔除與 AI 確定者都在，pending 不在。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._workspace(c, "ws-bucket", [91, 92, 93])
            exclusions.exclude_patents(ws, [(91, "人工剔除")], conn=c)
            exclusions.store_ai_verdicts(
                ws,
                [
                    {"patent_id": 92, "verdict": "不相干", "reason": "AI 判定"},
                    {"patent_id": 93, "verdict": "可疑", "reason": "AI 存疑"},
                ],
                conn=c,
            )
            exclusions.confirm_exclusions(ws, [92], conn=c)
            c.commit()
            items = exclusions.excluded_patent_rows(ws, conn=c)
        self.assertEqual([it["patent_id"] for it in items], [91, 92],
                         "只列已確定排除者；93 仍為 pending 不在桶內")
        by_id = {it["patent_id"]: it for it in items}
        self.assertEqual(by_id[91]["source"], "manual")
        self.assertEqual(by_id[92]["source"], "ai")
        self.assertEqual(by_id[92]["ai_verdict"], "不相干")

    def test_global_workspace_still_not_deducted(self):
        """全庫 workspace 照舊不扣除（0035 既有紅線不因複核狀態改變）。"""
        from backend.app.clustering import exclusions

        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._workspace(c, "ws-global", [91, 92], is_global=True)
            exclusions.exclude_patents(ws, [(91, "人工")], conn=c)
            c.commit()
            members = exclusions.analysis_member_patent_ids(ws, conn=c)
        self.assertEqual(members, [91, 92])


if __name__ == "__main__":
    unittest.main()
