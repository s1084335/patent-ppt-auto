"""封面三層漏斗併 1 格（Q3，2026-08-05 定案；2026-08-07 動工）。

`原始 55 件 → 同族合併後 48 件 → 技術主題 5 群` 併成封面**一格**：
遞減關係本來就該連著看，拆格反而讓前兩層同為「件」被誤讀成兩個獨立指標。
⚠ 單位與標籤一律走 unit／label 欄，不得併進 value（value 變長會讓四張卡
一起降級——`_cover_stat_size` 取同一級、由最長值決定）。
技術群數用**技術通道**（功效通道不上封面）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt" / "scripts"))
import build_ppt as bp  # noqa: E402


def _report_data(*, family_total=48, topic_rows=None):
    rows = topic_rows if topic_rows is not None else [
        {"topic_code": f"T00{i}", "source_field": "wips_independent_claims"} for i in range(1, 6)
    ] + [
        {"topic_code": f"E00{i}", "source_field": "effect_summary"} for i in range(1, 9)
    ]
    return {
        "reports": {
            "application_trend": {"rows": [{"application_year": 2024, "patent_count": 55}]},
            "country_distribution": {"rows": [
                {"country_code": "CN", "patent_count": 38},
                {"country_code": "TW", "patent_count": 9},
            ]},
        },
        "family_reports": {
            "family_country_layout": {"rows": [
                {"country_code": "CN", "patent_count": 30},
                {"country_code": "TW", "patent_count": family_total - 30},
            ]},
        },
        "chart_rows": {"cluster_topic_table": rows},
        "parameters": {"family_total": family_total},
    }


class FunnelCellTests(unittest.TestCase):
    def _funnel(self, data):
        stats = bp._cover_stats(data)
        hits = [s for s in stats if "→" in s[0]]
        return hits[0] if hits else None

    def test_funnel_cell_present(self):
        cell = self._funnel(_report_data())
        self.assertIsNotNone(cell, "封面沒有漏斗格")
        value, unit, label = cell
        self.assertEqual(value, "55→48→5")
        self.assertEqual(unit, "件→件→群")
        self.assertEqual(label, "原始→同族合併→技術主題")

    def test_units_not_inside_value(self):
        """單位進 value 會讓四張卡一起降級（規格明列的字級風險）。"""
        value = self._funnel(_report_data())[0]
        for token in ("件", "群"):
            self.assertNotIn(token, value)

    def test_topic_count_uses_technical_channel_only(self):
        """技術群數用技術通道（5），不得把功效 8 群算進來或加總成 13。"""
        value = self._funnel(_report_data())[0]
        self.assertTrue(value.endswith("→5"), f"技術群數不對：{value}")

    def test_cover_keeps_four_cells_max(self):
        stats = bp._cover_stats(_report_data())
        self.assertLessEqual(len(stats), 4)

    def test_missing_family_or_topics_degrades_quietly(self):
        """缺同族或分群資料時不出漏斗格（不硬湊、不寫 0）。"""
        data = _report_data()
        data["family_reports"] = {}
        data["chart_rows"] = {}
        self.assertIsNone(self._funnel(data))


if __name__ == "__main__":
    unittest.main()
