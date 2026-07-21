"""Claim 全文解析器契約：切條、type/parent 判定、雙語引用格式（純邏輯 fixtures＋gated smoke）。

真實 fixtures 取自拋棄式 patent_ppt_importcheck（407 件全為 US 英文全文，唯讀）。
importcheck 無中文全文，故中文格式以合成 fixture 驗證（檔頭已註記）。
smoke（RUN_DB_TESTS=1）唯讀掃 407 件，斷言成功解析率 ≥ 90%。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "claims"


def _parse_file(name):
    from backend.app.comparison.claim_parser import parse_claims
    text = FIX.joinpath(name).read_text(encoding="utf-8")
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    return parse_claims(body)


class FixtureTests(unittest.TestCase):
    def test_us_838_single_plus_canceled(self):
        by = {c["claim_number"]: c for c in _parse_file("us_838_single.txt")}
        self.assertEqual(by["1"]["type"], "independent")
        self.assertEqual(by["2-40"]["type"], "unknown")  # canceled range：保留不猜

    def test_us_770_dependent_chain_to_1(self):
        claims = _parse_file("us_770_dependent.txt")
        self.assertEqual(len(claims), 4)
        by = {c["claim_number"]: c for c in claims}
        self.assertEqual(by["1"]["type"], "independent")
        for n in ("2", "3", "4"):
            self.assertEqual(by[n]["type"], "dependent")
            self.assertEqual(by[n]["parent"], "1")

    def test_us_873_multi_independent_and_dependent_chain(self):
        # 真實案：14 條、兩個獨立項（1、6）＋從屬鏈；語料無真正 multiple-dependent
        # （873 的 "or claim 11" 實為 "of claim 11" 來源錯字），multiple-dependent 由合成中文 fixture 覆蓋
        claims = _parse_file("us_873_multidep.txt")
        by = {c["claim_number"]: c for c in claims}
        self.assertEqual(len(claims), 14)
        self.assertEqual(by["1"]["type"], "independent")
        self.assertEqual(by["6"]["type"], "independent")
        self.assertGreaterEqual(sum(1 for c in claims if c["type"] == "independent"), 2)
        self.assertEqual(by["12"]["parent"], "11")  # 從屬鏈

    def test_zh_synthetic_chain_and_multiple(self):
        by = {c["claim_number"]: c for c in _parse_file("zh_synthetic_multidep.txt")}
        self.assertEqual(by["1"]["type"], "independent")
        self.assertEqual(by["2"]["parent"], "1")
        self.assertEqual(by["3"]["parent"], "2")  # 中文從屬鏈 3→2
        self.assertEqual(by["4"]["type"], "dependent")
        self.assertTrue(by["4"]["multiple_dependent"])
        self.assertEqual(set(by["4"]["parents"]), {"1", "3"})

    def test_forward_reference_marked_unknown_not_guessed(self):
        from backend.app.comparison.claim_parser import parse_claims
        by = {c["claim_number"]: c for c in parse_claims(
            "1. A device. | 2. The device according to claim 99, wherein x.")}
        self.assertEqual(by["2"]["type"], "unknown")  # 99>2 前向引用不猜 parent
        self.assertIn("claim 99", by["2"]["text"])    # 不丟棄原文

    def test_skeleton_alignment_with_claim_model(self):
        from backend.app.comparison.claim_parser import parse_claims, to_understanding_skeleton
        sk = to_understanding_skeleton(
            parse_claims("1. A device. | 2. The device of claim 1, wherein x."),
            ["所有權利要求"])
        self.assertEqual(sk["source_fields"], ["所有權利要求"])
        self.assertEqual(sk["independent_claims"][0]["claim_number"], "1")
        self.assertEqual(sk["dependent_claims"][0]["parent"], "1")


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "需 RUN_DB_TESTS=1 與 patent_ppt_importcheck")
class SmokeTests(unittest.TestCase):
    def test_parse_all_407_success_rate(self):
        import psycopg
        from backend.app.comparison.claim_parser import parse_claims
        kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
                  user=os.getenv("PGUSER", "postgres"), dbname="patent_ppt_importcheck")
        if os.getenv("PGPASSWORD"):
            kw["password"] = os.getenv("PGPASSWORD")
        with psycopg.connect(**kw) as c:  # 唯讀，不 DROP
            rows = c.execute(
                'SELECT id, "所有權利要求[JP,KR,CN]" FROM core_layer.patents '
                'WHERE "所有權利要求[JP,KR,CN]" IS NOT NULL').fetchall()
        total = len(rows)
        ok = unknown_entries = patents_with_unknown = 0
        fails: dict[str, int] = {}
        for pid, text in rows:
            try:
                claims = parse_claims(text)
            except Exception as exc:  # noqa: BLE001
                fails[type(exc).__name__] = fails.get(type(exc).__name__, 0) + 1
                continue
            u = sum(1 for cl in claims if cl["type"] == "unknown")
            unknown_entries += u
            patents_with_unknown += 1 if u else 0
            first = claims[0] if claims else None
            if first and first["claim_number"] == "1" and first["type"] == "independent":
                ok += 1
            else:
                key = ("no_claims" if not claims else
                       "claim1_missing" if first["claim_number"] != "1" else "claim1_not_independent")
                fails[key] = fails.get(key, 0) + 1
        rate = ok / total * 100 if total else 0
        top3 = sorted(fails.items(), key=lambda x: -x[1])[:3]
        print(f"\n[SMOKE] total={total} ok={ok} rate={rate:.1f}% "
              f"unknown_entries={unknown_entries} patents_with_unknown={patents_with_unknown} top3_fail={top3}")
        self.assertGreaterEqual(rate, 90.0)


if __name__ == "__main__":
    unittest.main()
