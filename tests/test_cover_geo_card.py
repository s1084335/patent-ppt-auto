"""J-4（2026-08-04）：封面「地域分布」卡不得只列前二國。

## 症狀（第五輪實機 p1）

卡片印「39 ｜ 9」「CN ｜ TW」——39+9=48，但專利總數 60（US 9、EP 3 被丟）。
而且 TW 與 US 同為 9 件，挑 TW 不挑 US 是**任意的**。

## 修法

≤4 局全列；>4 局取前 3 ＋「其他」合計——**件數總和恆等於專利總數**，
排序以（件數 desc, 代碼 asc）決定，同數不再任意。
"""
import unittest

import sys
from pathlib import Path

_SKILL = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt" / "scripts"
sys.path.insert(0, str(_SKILL))
import build_ppt as bp  # noqa: E402


def _report(rows_by_key):
    return {"reports": {k: {"rows": v} for k, v in rows_by_key.items()}}


def _geo_card(stats):
    return next(s for s in stats if s[2].startswith("地域分布"))


class CoverGeoCardTests(unittest.TestCase):
    def test_all_four_offices_listed_and_sum_matches(self):
        data = _report({
            "application_trend": [{"year": 2024, "patent_count": 60}],
            "country_distribution": [
                {"country_code": "CN", "patent_count": 39},
                {"country_code": "TW", "patent_count": 9},
                {"country_code": "US", "patent_count": 9},
                {"country_code": "EP", "patent_count": 3},
            ],
        })
        value, unit, _ = _geo_card(bp._cover_stats(data))
        nums = [int(x) for x in value.split("｜")]
        self.assertEqual(sum(nums), 60, f"件數總和 {sum(nums)} ≠ 60：{value!r}")
        for code in ("CN", "TW", "US", "EP"):
            self.assertIn(code, unit)

    def test_tie_order_is_deterministic(self):
        """同件數依代碼排序——TW/US 同 9 件時 TW 在前不是挑掉 US。"""
        data = _report({
            "country_distribution": [
                {"country_code": "US", "patent_count": 9},
                {"country_code": "TW", "patent_count": 9},
            ],
        })
        _, unit, _ = _geo_card(bp._cover_stats(data))
        self.assertLess(unit.index("TW"), unit.index("US"))

    def test_many_offices_collapse_to_top3_plus_other(self):
        rows = [{"country_code": c, "patent_count": n} for c, n in
                [("CN", 30), ("US", 12), ("TW", 8), ("EP", 5), ("JP", 3), ("KR", 2)]]
        value, unit, _ = _geo_card(bp._cover_stats(_report({"country_distribution": rows})))
        nums = [int(x) for x in value.split("｜")]
        self.assertEqual(sum(nums), 60)
        self.assertIn("其他", unit)
        self.assertEqual(len(nums), 4, "前 3 ＋ 其他")


if __name__ == "__main__":
    unittest.main()
