"""市場資料線純邏輯契約：evidence_model 契約各態＋aggregate 彙總鐵律。"""
from __future__ import annotations

import unittest


def _scope():
    return {"product_definition": "自走式割草機", "includes": ["robotic mower"], "excludes": ["手推式"],
            "regions": ["US", "EP"], "base_year": 2024, "forecast_period": "2025-2030", "currency": "USD"}


def _ev(kind="market_size", market="US", subject=None, reliability="industry_gov_corp",
        url="https://example.com/r", metric_value=None, year=2024, market_def="manufacturer",
        publisher="Frost & Sullivan", **payload_extra):
    value = {"year": year, "market_definition": market_def}
    if metric_value is not None:
        value["market_size"] = metric_value
    payload = {"source_name": "R", "source_url": url, "published_on": "2024-06-01",
               "reliability": reliability, "summary": "公開摘要逐字", "publisher": publisher,
               "value": value, **payload_extra}
    e = {"kind": kind, "scope": "自走式割草機", "payload_json": payload}
    if market is not None:
        e["market"] = market
    if subject is not None:
        e["subject"] = subject
    return e


class ScopeTests(unittest.TestCase):
    def test_valid_scope(self):
        from backend.app.market.evidence_model import validate_scope
        validate_scope(_scope())

    def test_missing_field_named(self):
        from backend.app.market.evidence_model import validate_scope, MarketEvidenceError
        s = _scope(); del s["currency"]
        with self.assertRaises(MarketEvidenceError) as ctx:
            validate_scope(s)
        self.assertIn("currency", str(ctx.exception))


class EvidenceContractTests(unittest.TestCase):
    def _v(self, e):
        from backend.app.market.evidence_model import validate_evidence
        return validate_evidence(e)

    def _err(self):
        from backend.app.market.evidence_model import MarketEvidenceError
        return MarketEvidenceError

    def test_valid_market_size(self):
        self._v(_ev(metric_value=5.0))

    def test_bad_kind(self):
        with self.assertRaises(self._err()):
            self._v(_ev(kind="gdp"))

    def test_market_size_requires_market(self):
        e = _ev(); e.pop("market", None)
        with self.assertRaises(self._err()):
            self._v(e)

    def test_pain_point_requires_subject_topic_code(self):
        # pain_point 需 subject=topic_code；缺 subject 拒收
        with self.assertRaises(self._err()):
            self._v(_ev(kind="pain_point", market=None))
        self._v(_ev(kind="pain_point", market=None, subject="T01"))

    def test_missing_payload_field_named(self):
        e = _ev(metric_value=5.0); del e["payload_json"]["source_name"]
        with self.assertRaises(self._err()) as ctx:
            self._v(e)
        self.assertIn("source_name", str(ctx.exception))

    def test_bad_reliability_enum(self):
        with self.assertRaises(self._err()):
            self._v(_ev(reliability="blog"))

    def test_news_requires_publisher(self):
        e = _ev(reliability="news"); e["payload_json"]["publisher"] = ""
        with self.assertRaises(self._err()):
            self._v(e)

    def test_bad_source_url(self):
        with self.assertRaises(self._err()):
            self._v(_ev(url="ftp://x"))


class ComparabilityTests(unittest.TestCase):
    def test_different_key_not_mixed(self):
        from backend.app.market.aggregate import aggregate_metric
        a = _ev(metric_value=5.0, market_def="manufacturer")
        b = _ev(metric_value=9.0, market_def="retail")  # 不同市場定義 → 不同 key
        groups = aggregate_metric([a, b], "market_size")
        self.assertEqual(len(groups), 2)
        for g in groups:
            self.assertTrue(g["single_source"])

    def test_min_max_not_average(self):
        from backend.app.market.aggregate import aggregate_metric
        evs = [_ev(metric_value=4.0), _ev(metric_value=5.0, url="https://a.com/2")]
        g = aggregate_metric(evs, "market_size")[0]
        self.assertEqual((g["min"], g["max"]), (4.0, 5.0))
        self.assertFalse(g["single_source"])
        self.assertEqual(len(g["evidence"]), 2)

    def test_divergent_flag(self):
        from backend.app.market.aggregate import aggregate_metric
        evs = [_ev(metric_value=4.0), _ev(metric_value=10.0, url="https://a.com/2")]  # 全距 150% >50%
        self.assertTrue(aggregate_metric(evs, "market_size")[0]["divergent"])


class AidsTests(unittest.TestCase):
    def test_dedup_same_url_metric(self):
        from backend.app.market.aggregate import dedup
        evs = [_ev(metric_value=4.0, url="https://same.com"),
               _ev(metric_value=9.0, url="https://same.com")]
        self.assertEqual(len(dedup(evs, "market_size")), 1)

    def test_staleness(self):
        from backend.app.market.evidence_model import staleness
        e = _ev()
        self.assertTrue(staleness(e, "2025-06-01")["fresh"])
        old = staleness(e, "2028-06-01")
        self.assertFalse(old["fresh"])
        self.assertGreaterEqual(old["years_diff"], 3.9)

    def test_reliability_sort(self):
        from backend.app.market.aggregate import sort_by_reliability
        evs = [_ev(reliability="forum"), _ev(reliability="industry_gov_corp"), _ev(reliability="news")]
        ranked = [e["payload_json"]["reliability"] for e in sort_by_reliability(evs)]
        self.assertEqual(ranked, ["industry_gov_corp", "news", "forum"])


if __name__ == "__main__":
    unittest.main()
