"""卡片變體 → 解讀掛點的對照，必須由**引擎輸出**（2026-08-03 使用者實機指出）。

## 症狀

網頁報表頁三張卡顯示「AI 解讀尚未產生」，但 PPT 同一張卡有解讀。

## 根因：兩個 key 空間不同名，而橋接只寫在 PPT 端

實測 `report_trial_20260803_075213`，19 個變體中 3 個對不到：

| 卡片變體 | narratives 實際鍵 |
|---|---|
| `annual_trend:default` | `application_trend:default` |
| `cluster_topic_table:opportunity_tech` | `opportunity_quadrant:tech` |
| `cluster_topic_table:opportunity_effect` | `opportunity_quadrant:effect` |

PPT 端靠 `build_ppt.NARRATIVE_ALIASES` 與圖檔主檔名推導接得起來；
網頁端 `main.py` 只用 `narratives.get(report_key)`，兩條線索都沒有。

⚠ 這不是「解讀沒產」——ai:narrative 三段都產了，只是查不到
（也正是 #168 回報「16/19 變體」的真相）。

## 為什麼修在引擎

**誰把解讀掛上這張卡，誰才知道掛在哪**。對照表放在任何一個消費端，
另一個消費端就會漏——已經漏了一次。引擎把解出來的 `narrative_key`
直接寫進 report_data.json 的每個 variant，消費端讀就好，不各自推導。
"""
from __future__ import annotations

import unittest

from backend.app.reports import chart_runner as cr


class VariantNarrativeRefTests(unittest.TestCase):
    def test_same_name_passes_through(self):
        self.assertEqual(cr.variant_narrative_ref("applicant_ranking", "default"),
                         "applicant_ranking:default")

    def test_level_suffix_falls_back_to_base(self):
        """IPC 卡的 report_key 由檔名 fallback 帶 _L4，narratives 契約鍵不帶層級。"""
        self.assertEqual(cr.variant_narrative_ref("ipc_main_distribution_L4", "L5"),
                         "ipc_main_distribution:L5")

    def test_annual_trend_maps_to_application_trend(self):
        self.assertEqual(cr.variant_narrative_ref("annual_trend", "default"),
                         "application_trend:default")

    def test_opportunity_variants_map_to_quadrant(self):
        self.assertEqual(cr.variant_narrative_ref("cluster_topic_table", "opportunity_tech"),
                         "opportunity_quadrant:tech")
        self.assertEqual(cr.variant_narrative_ref("cluster_topic_table", "opportunity_effect"),
                         "opportunity_quadrant:effect")

    def test_sibling_variants_of_same_card_unaffected(self):
        """⚠ 同一張卡的其他變體不能被機會板的對照波及。"""
        self.assertEqual(cr.variant_narrative_ref("cluster_topic_table", "topic_table_tech"),
                         "cluster_topic_table:topic_table_tech")


class PersistedSectionsCarryRefTests(unittest.TestCase):
    def test_persistable_sections_write_narrative_key(self):
        """引擎輸出的 sections 每個變體都要帶 narrative_key（含 more_variants）。"""
        sections = [{
            "title": "分群分析",
            "report_key": "cluster_topic_table",
            "variants": [
                {"label": "技術", "file": "a.svg", "variant_key": "topic_table_tech"},
                {"label": "機會技術", "file": "b.svg", "variant_key": "opportunity_tech"},
            ],
            "more_variants": [
                {"label": "11-20", "file": "c.svg", "variant_key": "more"},
            ],
        }]
        out = cr.persistable_sections(sections)[0]
        self.assertEqual([v["narrative_key"] for v in out["variants"]],
                         ["cluster_topic_table:topic_table_tech", "opportunity_quadrant:tech"])
        self.assertEqual(out["more_variants"][0]["narrative_key"], "cluster_topic_table:more")

    def test_report_key_is_filled_in_even_when_absent(self):
        """沒寫 report_key 的 section 也要把 fallback 結果寫實，消費端不必再推導。"""
        sections = [{"title": "申請人排名",
                     "variants": [{"label": "A", "file": "applicant_ranking.svg",
                                   "variant_key": "default"}]}]
        out = cr.persistable_sections(sections)[0]
        self.assertEqual(out["report_key"], "applicant_ranking")


if __name__ == "__main__":
    unittest.main()
