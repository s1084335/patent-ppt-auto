"""market_evidence 證據庫：0023 migration 契約＋MarketStore 行為（拋棄式 DB，絕不碰 patent_ppt）。

沿用 comparison_store 測試的拋棄式 DB 模式：建庫 → alembic upgrade head。
契約：表/8 欄/索引/唯一約束存在、可插值、downgrade 乾淨（獨立 DB 跑升降）。
行為：只存已接受（accepted_at 落款）、同 scope 同 URL 拒重、supersede 不改舊值只標記＋append、
彙總轉 aggregate.py（min–max／single_source／divergent，排除已作廢列）。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_market"
PREV_REV = "fd301dee99c3"  # 0023 的上一版（0022 SSE triggers）
PROJECT_ROOT = Path(__file__).resolve().parents[1]

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.getenv("PGPASSWORD")
    return kw


def _cfg() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


def _create_db(name: str) -> None:
    with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')


def _drop_db(name: str) -> None:
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    except Exception:  # noqa: BLE001
        pass


def setUpModule():
    """建拋棄式 DB → upgrade head；admin 不可用則整組 skip。"""
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"
    try:
        _create_db(TEST_DB)
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB
    command.upgrade(_cfg(), "head")


def tearDownModule():
    for k, v in _prev_env.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    _drop_db(TEST_DB)


def _store():
    from backend.app.market.market_store import MarketStore
    return MarketStore()


def _payload(metric_value=None, metric="market_size", **extra):
    """組出符合 aggregate.py 期待的 payload（value 內含 year／market_definition＋可選數值 metric）。"""
    value = {"year": 2024, "market_definition": "manufacturer"}
    if metric_value is not None:
        value[metric] = metric_value
    p = {"source_name": "R", "source_url": "https://example.com/r", "published_on": "2024-06-01",
         "reliability": "industry_gov_corp", "summary": "公開摘要逐字", "value": value}
    p.update(extra)
    return p


def _truncate():
    with psycopg.connect(**_kw(TEST_DB)) as c:
        c.execute("TRUNCATE derived_layer.market_evidence RESTART IDENTITY")
        c.commit()


class MigrationContractTests(unittest.TestCase):
    """0023：表/8 欄/索引/唯一約束存在、可插值、downgrade 乾淨。"""

    def test_table_has_8_columns_index_and_unique_constraint(self):
        with psycopg.connect(**_kw(TEST_DB)) as c:
            cols = c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='derived_layer' AND table_name='market_evidence' "
                "ORDER BY ordinal_position"
            ).fetchall()
            self.assertEqual(
                [r[0] for r in cols],
                ["id", "kind", "scope", "target", "payload_json", "source_url", "summary", "accepted_at"],
            )
            idx = c.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname='derived_layer' "
                "AND tablename='market_evidence' AND indexname='ix_market_evidence_kind_scope_target'"
            ).fetchone()
            self.assertIsNotNone(idx, "缺 (kind,scope,target) 索引")
            uq = c.execute(
                "SELECT 1 FROM pg_constraint WHERE conname='uq_market_evidence_scope_url' AND contype='u'"
            ).fetchone()
            self.assertIsNotNone(uq, "缺 (scope,source_url) 唯一約束")

    def test_can_insert_row(self):
        new_id = _store().save_evidence(
            "market_size", "契約測試", "US", _payload(5.0), "https://ex.com/contract", "摘要")
        self.assertIsInstance(new_id, int)

    def test_downgrade_drops_table_cleanly(self):
        db = "patent_ppt_market_down"
        _create_db(db)
        prev = os.environ.get("PGDATABASE")
        os.environ["PGDATABASE"] = db
        try:
            command.upgrade(_cfg(), "0023_market_evidence")
            with psycopg.connect(**_kw(db)) as c:
                self.assertIsNotNone(
                    c.execute("SELECT to_regclass('derived_layer.market_evidence')").fetchone()[0])
            command.downgrade(_cfg(), PREV_REV)
            with psycopg.connect(**_kw(db)) as c:
                self.assertIsNone(
                    c.execute("SELECT to_regclass('derived_layer.market_evidence')").fetchone()[0],
                    "downgrade 後表未乾淨移除")
        finally:
            os.environ["PGDATABASE"] = prev
            _drop_db(db)


class SaveAndGetTests(unittest.TestCase):
    """save_evidence 只存已接受＋拒非法 kind＋防重；get_evidence 過濾。"""

    def setUp(self):
        _truncate()

    def test_save_stamps_accepted_at(self):
        new_id = _store().save_evidence(
            "market_size", "割草機", "US", _payload(5.0), "https://ex.com/1", "摘要")
        with psycopg.connect(**_kw(TEST_DB)) as c:
            acc = c.execute(
                "SELECT accepted_at FROM derived_layer.market_evidence WHERE id=%s", (new_id,)
            ).fetchone()[0]
        self.assertIsNotNone(acc)

    def test_reject_bad_kind(self):
        with self.assertRaises(ValueError):
            _store().save_evidence("gdp", "割草機", "US", _payload(1.0), "https://ex.com/2", "x")

    def test_reject_invalid_payload_contract(self):
        from backend.app.market.evidence_model import MarketEvidenceError

        payload = _payload(1.0, reliability="blog")
        with self.assertRaises(MarketEvidenceError):
            _store().save_evidence(
                "market_size", "割草機", "US", payload, "https://ex.com/bad", "x"
            )

    def test_payload_source_url_is_normalized_to_store_url(self):
        s = _store()
        new_id = s.save_evidence(
            "market_size",
            "割草機",
            "US",
            _payload(5.0, source_url="https://payload.example/old"),
            "https://canonical.example/source",
            "摘要",
        )
        row = s.get_evidence(kind="market_size", scope="割草機", target="US")[0]
        self.assertEqual(row["id"], new_id)
        self.assertEqual(row["source_url"], "https://canonical.example/source")
        self.assertEqual(
            row["payload_json"]["source_url"], "https://canonical.example/source"
        )

    def test_reject_duplicate_scope_url(self):
        from backend.app.market.market_store import DuplicateEvidenceError
        s = _store()
        s.save_evidence("market_size", "割草機", "US", _payload(5.0), "https://dup.com/x", "摘要")
        # 同 scope 同 URL（即使 kind／target 不同）仍拒重
        with self.assertRaises(DuplicateEvidenceError):
            s.save_evidence("region_trend", "割草機", "EP", _payload(), "https://dup.com/x", "另一摘要")

    def test_get_filters_by_kind_scope_target(self):
        s = _store()
        s.save_evidence("market_size", "割草機", "US", _payload(5.0), "https://ex.com/us", "US摘要")
        s.save_evidence("customer", "割草機", "經銷商", _payload(), "https://ex.com/cust", "客群摘要")
        self.assertEqual(len(s.get_evidence(scope="割草機")), 2)
        us = s.get_evidence(kind="market_size", target="US")
        self.assertEqual(len(us), 1)
        self.assertEqual(us[0]["summary"], "US摘要")


class SupersedeTests(unittest.TestCase):
    """supersede：不改舊值、舊列標 superseded 指向新列、新列 append；old_id 不存在拒動。"""

    def setUp(self):
        _truncate()

    def test_supersede_keeps_old_value_and_appends_new(self):
        s = _store()
        old = s.save_evidence("market_size", "割草機", "US", _payload(5.0), "https://ex.com/old", "舊")
        new = s.supersede_evidence(
            old, "market_size", "割草機", "US", _payload(6.0), "https://ex.com/new", "新")
        rows = {r["id"]: r for r in s.get_evidence(scope="割草機")}
        self.assertEqual(len(rows), 2)  # append，不刪舊
        self.assertEqual(rows[old]["payload_json"]["value"]["market_size"], 5.0)  # 舊值原封不動
        self.assertTrue(rows[old]["payload_json"]["superseded"])
        self.assertEqual(rows[old]["payload_json"]["superseded_by"], new)
        self.assertEqual(rows[new]["payload_json"]["value"]["market_size"], 6.0)  # 新列為更正值
        self.assertNotIn("superseded", rows[new]["payload_json"])

    def test_supersede_missing_old_raises_and_appends_nothing(self):
        s = _store()
        with self.assertRaises(ValueError):
            s.supersede_evidence(
                99999, "market_size", "割草機", "US", _payload(1.0), "https://ex.com/z", "x")
        self.assertEqual(len(s.get_evidence(scope="割草機")), 0)


class AggregateForReportTests(unittest.TestCase):
    """aggregate_for_report 轉 aggregate.py：min–max／single_source／divergent；排除已作廢列。"""

    def setUp(self):
        _truncate()

    def test_min_max_not_average_two_sources(self):
        s = _store()
        s.save_evidence("market_size", "割草機", "US", _payload(4.0), "https://a.com/1", "a")
        s.save_evidence("market_size", "割草機", "US", _payload(5.0), "https://a.com/2", "b")
        g = s.aggregate_for_report(scope="割草機")["metrics"]["market_size"]
        self.assertEqual(len(g), 1)
        self.assertEqual((g[0]["min"], g[0]["max"]), (4.0, 5.0))
        self.assertFalse(g[0]["single_source"])
        self.assertFalse(g[0]["divergent"])

    def test_divergent_flag_two_active_sources(self):
        s = _store()
        s.save_evidence("market_size", "割草機", "US", _payload(4.0), "https://a.com/1", "a")
        s.save_evidence("market_size", "割草機", "US", _payload(10.0), "https://a.com/2", "b")  # >50%
        self.assertTrue(s.aggregate_for_report(scope="割草機")["metrics"]["market_size"][0]["divergent"])

    def test_excludes_superseded_row(self):
        s = _store()
        old = s.save_evidence("market_size", "割草機", "US", _payload(4.0), "https://a.com/1", "a")
        # 作廢舊列、append 新列 10；若計入舊(4) 會 divergent，排除後只剩新列 → single_source
        s.supersede_evidence(old, "market_size", "割草機", "US", _payload(10.0), "https://a.com/2", "b")
        g = s.aggregate_for_report(scope="割草機")["metrics"]["market_size"]
        self.assertEqual(len(g), 1)
        self.assertTrue(g[0]["single_source"])

    def test_region_trends_and_customers(self):
        s = _store()
        pt = _payload(); pt["value"]["trend"] = "上升"
        s.save_evidence("region_trend", "割草機", "US", pt, "https://a.com/rt", "rt")
        pc = _payload(); pc["value"]["share"] = 0.3
        s.save_evidence("customer", "割草機", "經銷商", pc, "https://a.com/cu", "cu")
        rep = s.aggregate_for_report(scope="割草機")
        self.assertEqual(rep["region_trends"]["US"][0]["trend"], "上升")
        self.assertEqual(rep["customers"]["經銷商"][0]["share"], 0.3)


if __name__ == "__main__":
    unittest.main()
