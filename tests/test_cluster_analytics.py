"""cluster_analytics 純邏輯模組單元測試。

純函式層 — 不碰 DB、不碰 I/O。測試輸入 topics / assignments /
normalized_applicants 組合，驗證統計與象限計算結果。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.reports import cluster_analytics as ca
from backend.app.clustering.sources import SOURCE_FIELD_EFFECT, SOURCE_FIELD_TECHNICAL
from backend.app.reports.cluster_analytics import (
    build_opportunity_matrix,
    build_topic_effect_table,
)


class TopicEffectTableTests(unittest.TestCase):
    """build_topic_effect_table: 主題／功效統計表正確性。"""

    def test_basic_table(self):
        topics = [
            {"topic_code": "T01", "label": "半導體製程", "source_field": "independent_claims"},
            {"topic_code": "T02", "label": "面板驅動", "source_field": "effect_summary"},
        ]
        assignments = [
            {"topic_code": "T01", "patent_id": 101},
            {"topic_code": "T01", "patent_id": 102},
            {"topic_code": "T02", "patent_id": 103},
        ]
        applicants = [
            {"patent_id": 101, "applicant_name": "TSMC"},
            {"patent_id": 102, "applicant_name": "TSMC"},
            {"patent_id": 103, "applicant_name": "Samsung"},
        ]
        rows = build_topic_effect_table(topics, assignments, applicants)
        self.assertEqual(len(rows), 2)
        t01 = next(r for r in rows if r["topic_code"] == "T01")
        t02 = next(r for r in rows if r["topic_code"] == "T02")
        self.assertEqual(t01["patent_count"], 2)
        self.assertEqual(t01["applicant_count"], 1)
        self.assertEqual(t01["label"], "半導體製程")
        self.assertEqual(t01["source_field"], "independent_claims")
        self.assertEqual(t01["top_applicants"], [{"name": "TSMC", "count": 2}])
        self.assertEqual(t02["patent_count"], 1)
        self.assertEqual(t02["applicant_count"], 1)
        self.assertEqual(t02["top_applicants"], [{"name": "Samsung", "count": 1}])

    def test_same_patent_two_applicants(self):
        """同一專利有兩個申請人時，雙方各計一次。"""
        topics = [{"topic_code": "T01", "label": "通訊", "source_field": "independent_claims"}]
        assignments = [{"topic_code": "T01", "patent_id": 101}]
        applicants = [
            {"patent_id": 101, "applicant_name": "TSMC"},
            {"patent_id": 101, "applicant_name": "Qualcomm"},
        ]
        rows = build_topic_effect_table(topics, assignments, applicants)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["patent_count"], 1)
        self.assertEqual(row["applicant_count"], 2)
        self.assertEqual(
            sorted(a["name"] for a in row["top_applicants"]),
            ["Qualcomm", "TSMC"],
        )

    def test_same_company_on_multiple_patents(self):
        """同公司不同專利只在申請人總數計一次，但 top_applicants count 累加。"""
        topics = [{"topic_code": "T01", "label": "AI", "source_field": "independent_claims"}]
        assignments = [
            {"topic_code": "T01", "patent_id": 101},
            {"topic_code": "T01", "patent_id": 102},
            {"topic_code": "T01", "patent_id": 103},
        ]
        applicants = [
            {"patent_id": 101, "applicant_name": "TSMC"},
            {"patent_id": 102, "applicant_name": "TSMC"},
            {"patent_id": 103, "applicant_name": "TSMC"},
        ]
        rows = build_topic_effect_table(topics, assignments, applicants)
        row = rows[0]
        self.assertEqual(row["patent_count"], 3)
        self.assertEqual(row["applicant_count"], 1)
        self.assertEqual(row["top_applicants"], [{"name": "TSMC", "count": 3}])

    def test_unclassified_topic_included(self):
        """未分類主題仍然輸出，patent_count 可能為 0。"""
        topics = [
            {"topic_code": "T01", "label": "分類A", "source_field": "independent_claims"},
            {"topic_code": "UNCLASSIFIED", "label": "未分類", "source_field": ""},
        ]
        assignments = [
            {"topic_code": "UNCLASSIFIED", "patent_id": 999},
        ]
        applicants = [
            {"patent_id": 999, "applicant_name": "Others"},
        ]
        rows = build_topic_effect_table(topics, assignments, applicants)
        self.assertEqual(len(rows), 2)
        unc = next(r for r in rows if r["topic_code"] == "UNCLASSIFIED")
        self.assertEqual(unc["patent_count"], 1)
        self.assertEqual(unc["label"], "未分類")

    def test_topic_with_no_assignments(self):
        """正式主題無對應專利時仍輸出，patent_count = 0。"""
        topics = [
            {"topic_code": "T01", "label": "有案", "source_field": "claims"},
            {"topic_code": "T02", "label": "無案", "source_field": "claims"},
        ]
        assignments = [{"topic_code": "T01", "patent_id": 1}]
        applicants = [{"patent_id": 1, "applicant_name": "ACME"}]
        rows = build_topic_effect_table(topics, assignments, applicants)
        self.assertEqual(len(rows), 2)
        t02 = next(r for r in rows if r["topic_code"] == "T02")
        self.assertEqual(t02["patent_count"], 0)
        self.assertEqual(t02["applicant_count"], 0)
        self.assertEqual(t02["top_applicants"], [])

    def test_top_applicants_selects_top_three(self):
        """frontend 只顯示前三大的申請人。"""
        topics = [{"topic_code": "T01", "label": "藥品", "source_field": "claims"}]
        assignments = [
            {"topic_code": "T01", "patent_id": p}
            for p in range(1, 11)
        ]
        companies = ["Pfizer", "Merck", "Bayer", "Roche", "Novartis"]
        applicants = []
        for pid in range(1, 11):
            for company in companies:
                applicants.append({"patent_id": pid, "applicant_name": company})
        rows = build_topic_effect_table(topics, assignments, applicants)
        row = rows[0]
        self.assertEqual(len(row["top_applicants"]), 3)
        self.assertEqual([a["count"] for a in row["top_applicants"]], [10, 10, 10])


class OpportunityMatrixTests(unittest.TestCase):
    """build_opportunity_matrix: 機會四象限。"""

    def test_median_thresholds(self):
        topic_rows = [
            {"topic_code": "T01", "patent_count": 10, "applicant_count": 5,
             "top_applicants": [{"name": "TSMC", "count": 8}]},
            {"topic_code": "T02", "patent_count": 20, "applicant_count": 3,
             "top_applicants": [{"name": "Samsung", "count": 2}]},
            {"topic_code": "T03", "patent_count": 5, "applicant_count": 8,
             "top_applicants": [{"name": "LG", "count": 5}]},
        ]
        result = build_opportunity_matrix(topic_rows, ["TSMC", "Samsung"])
        self.assertEqual(result["patent_count_median"], 10.0)
        self.assertEqual(result["applicant_count_median"], 5.0)
        self.assertEqual(len(result["rows"]), 3)

    def test_leading_applicant_involvement(self):
        topic_rows = [
            {"topic_code": "T01", "patent_count": 10, "applicant_count": 5,
             "top_applicants": [
                 {"name": "TSMC", "count": 8},
                 {"name": "Samsung", "count": 2},
             ]},
            {"topic_code": "T02", "patent_count": 20, "applicant_count": 3,
             "top_applicants": [{"name": "Intel", "count": 3}]},
        ]
        result = build_opportunity_matrix(topic_rows, ["TSMC", "Samsung", "Intel"])
        t01 = next(r for r in result["rows"] if r["topic_code"] == "T01")
        t02 = next(r for r in result["rows"] if r["topic_code"] == "T02")
        self.assertIn("TSMC", t01["leading_applicants_involved"])
        self.assertIn("Samsung", t01["leading_applicants_involved"])
        self.assertEqual(t01["leading_applicant_count"], 2)
        self.assertIn("Intel", t02["leading_applicants_involved"])
        self.assertEqual(t02["leading_applicant_count"], 1)

    def test_empty_input(self):
        result = build_opportunity_matrix([], ["TSMC"])
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["patent_count_median"], 0.0)

    def test_label_passthrough(self):
        """regression（2026-07-21）：rows 未帶 label 導致 SVG 點標籤退回 topic code。"""
        topic_rows = [
            {"topic_code": "T01", "label": "散熱防塵", "patent_count": 11, "applicant_count": 6,
             "top_applicants": [{"name": "TSMC", "count": 3}]},
        ]
        result = build_opportunity_matrix(topic_rows, ["TSMC"])
        self.assertEqual(result["rows"][0].get("label"), "散熱防塵",
                         "opportunity rows 必須帶 label 供圖表顯示中文主題名")

    def test_single_topic_median(self):
        topic_rows = [
            {"topic_code": "T01", "patent_count": 7, "applicant_count": 4,
             "top_applicants": []},
        ]
        result = build_opportunity_matrix(topic_rows, ["TSMC"])
        self.assertEqual(result["patent_count_median"], 7.0)
        self.assertEqual(result["applicant_count_median"], 4.0)


# 🔴 2026-08-04：PainPointMatrixTests 已刪除——痛點板整個移除（使用者定案），
# 規格沒了測試就失去存在理由。


class TopicStatusClassificationTests(unittest.TestCase):
    """C-2／Q2：技術狀態五類分類（2026-08-02 使用者定案）。

    | 狀態 | 判斷條件 | 意義 |
    |---|---|---|
    | 新興技術 | 專利量低，但成長率高、Topic 占比上升 | 剛開始受到關注 |
    | 成長技術 | 專利量增加，申請人數同步增加 | 技術快速擴散 |
    | 成熟技術 | 專利量高，但成長停滯 | 技術方向逐漸穩定 |
    | 競爭集中技術 | 專利量高，申請人數下降，集中度提高 | 技術成熟後由少數玩家掌握 |
    | 衰退／轉型技術 | 專利量下降，Topic 占比下降，申請人減少 | 技術熱度降低或被新技術取代 |

    時間一律用**申請年**；近期窗 2020–2024、早期窗 2011–2019（由實際資料切出：
    近期 38 件、早期 17 件、2025–2026 僅 5 件屬資料截止效應故排除）。
    """

    BASE = {
        "patent_count": 10, "recent_count": 5, "early_count": 5,
        "recent_applicants": 4, "early_applicants": 4,
        "share_recent": 0.20, "share_early": 0.20,
        "concentration_recent": 50, "concentration_early": 50,
    }

    def _classify(self, median=8.0, **overrides):
        return ca.classify_topic_status({**self.BASE, **overrides}, median_count=median)

    def test_insufficient_sample_is_not_classified(self):
        """🔴 件數 <5 不判狀態。切窗後是「近期 2 件 vs 早期 1 件」，判成長是噪音。

        本案 13 個 topic 有 3 個落此（捲軸雙繩回收 3 件、提升效率降成本 4 件、
        提升運轉穩定度 2 件）。
        """
        self.assertEqual(self._classify(patent_count=4), ca.TOPIC_STATUS_INSUFFICIENT)
        self.assertEqual(self._classify(patent_count=2), ca.TOPIC_STATUS_INSUFFICIENT)

    def test_emerging(self):
        """量低（<中位數）＋成長率高（R≥0.7）＋占比上升。"""
        status = self._classify(patent_count=6, recent_count=5, early_count=1,
                                share_recent=0.30, share_early=0.10)
        self.assertEqual(status, ca.TOPIC_STATUS_EMERGING)

    def test_growing(self):
        """件數增加且申請人同步增加。"""
        status = self._classify(patent_count=12, recent_count=9, early_count=3,
                                recent_applicants=7, early_applicants=3,
                                share_recent=0.22, share_early=0.20)
        self.assertEqual(status, ca.TOPIC_STATUS_GROWING)

    def test_mature(self):
        """量高但成長停滯（R 落在全庫基準 ±0.1）。"""
        status = self._classify(patent_count=15, recent_count=10, early_count=5,
                                recent_applicants=5, early_applicants=5)
        self.assertEqual(status, ca.TOPIC_STATUS_MATURE)

    def test_concentrated(self):
        """量高＋申請人下降＋集中度提高。"""
        status = self._classify(patent_count=15, recent_count=10, early_count=5,
                                recent_applicants=2, early_applicants=6,
                                concentration_recent=80, concentration_early=40)
        self.assertEqual(status, ca.TOPIC_STATUS_CONCENTRATED)

    def test_declining(self):
        """件數下降＋占比下降＋申請人減少。"""
        status = self._classify(patent_count=12, recent_count=3, early_count=9,
                                recent_applicants=2, early_applicants=6,
                                share_recent=0.08, share_early=0.30)
        self.assertEqual(status, ca.TOPIC_STATUS_DECLINING)

    def test_declining_wins_over_concentrated(self):
        """⚠ 優先序：衰退／轉型 → 競爭集中 → 成長 → 成熟 → 新興。

        件數下降時就算集中度也提高了，主訊號仍是「熱度在退」，不該報成
        「成熟後由少數玩家掌握」——後者暗示技術還活著。
        """
        status = self._classify(patent_count=15, recent_count=3, early_count=12,
                                recent_applicants=1, early_applicants=5,
                                share_recent=0.05, share_early=0.30,
                                concentration_recent=90, concentration_early=40)
        self.assertEqual(status, ca.TOPIC_STATUS_DECLINING)

    def test_every_status_has_a_meaning(self):
        """五類都要有「意義」文字——狀態名本身不解釋為什麼重要（C-6）。"""
        for status in (ca.TOPIC_STATUS_EMERGING, ca.TOPIC_STATUS_GROWING,
                       ca.TOPIC_STATUS_MATURE, ca.TOPIC_STATUS_CONCENTRATED,
                       ca.TOPIC_STATUS_DECLINING):
            self.assertTrue(ca.TOPIC_STATUS_MEANINGS.get(status), status)


class TopicTableWithPatentsTests(unittest.TestCase):
    """前置缺口：`build_topic_effect_table` 原本三個輸入只有 topic 與申請人，

    既算不出狀態（缺申請年），也指不出代表專利（缺專利號與名稱）。
    加**一個** `patents`（patent_id → 該專利屬性）供這兩件事共用——
    ⚠ 不開兩個參數：兩者都是「依 patent_id 查該專利的什麼」，
    拆成 patent_years＋patent_meta 就是同一件事兩個入口。
    不給時維持原行為，既有呼叫端零修改。
    """

    TOPICS = [{"topic_code": "T001", "label": "拉繩捲輪回收機構",
               "source_field": "wips_independent_claims"}]

    def _rows(self, patents=None):
        assignments = [{"topic_code": "T001", "patent_id": pid,
                        "source_field": "wips_independent_claims"} for pid in range(1, 11)]
        applicants = [{"patent_id": pid, "applicant_name": f"A{pid % 4}"} for pid in range(1, 11)]
        return build_topic_effect_table(self.TOPICS, assignments, applicants, patents=patents)

    @staticmethod
    def _patents(years):
        return {pid: {"application_year": year, "number": f"CN{pid:06d}",
                      "note": f"備註{pid}"} for pid, year in years.items()}

    def test_without_patents_keeps_old_shape(self):
        row = self._rows()[0]
        self.assertEqual(row["patent_count"], 10)
        self.assertNotIn("status", row)
        self.assertNotIn("representative", row)

    def test_with_patents_emits_status(self):
        row = self._rows(self._patents({pid: (2022 if pid > 3 else 2015)
                                        for pid in range(1, 11)}))[0]
        self.assertIn("status", row)
        self.assertEqual(row["recent_count"], 7)
        self.assertEqual(row["early_count"], 3)

    def test_effect_channel_has_no_status_column(self):
        """🔴 2026-08-03 使用者定案：**功效通道不放狀態欄**。

        使用者實機看到「提升訓練成效 → 成長技術」後指出：
        「功效通道的我說這樣做?這是技術通道的吧」——他 08-02 定的五類分類講的是
        **技術**，我一次算完全部 topic 才分通道，等於自行把它套到功效上。
        功效通道的用途是**跟技術主題對照**，不是自己判演進狀態。

        ⚠ 做法是「該列不寫 status 鍵」，不是寫了再由顯示層排除：
        PPT `_ordered_columns` 與網頁 `Object.keys(rows[0])` 都只看第一列的鍵，
        兩邊又都已先依 source_field 過濾——上游不給，下游自然不顯示，
        不必在兩個顯示層各加一份排除規則（那就是同一資訊第三、四處落點）。
        """
        topics = [{"topic_code": "E001", "label": "提升訓練成效",
                   "source_field": SOURCE_FIELD_EFFECT}]
        assignments = [{"topic_code": "E001", "patent_id": pid,
                        "source_field": SOURCE_FIELD_EFFECT} for pid in range(1, 11)]
        applicants = [{"patent_id": pid, "applicant_name": f"A{pid % 4}"}
                      for pid in range(1, 11)]
        row = build_topic_effect_table(
            topics, assignments, applicants,
            patents=self._patents({pid: (2022 if pid > 3 else 2015)
                                   for pid in range(1, 11)}))[0]
        self.assertNotIn("status", row, "功效列不該有技術狀態欄")
        self.assertNotIn("status_meaning", row)
        # ⚠ 其餘欄位一律保留——使用者只否決狀態欄，沒有否決代表專利與占比。
        self.assertIn("representative", row)
        self.assertIn("top3_share", row)

    def test_effect_rows_carry_technical_means(self):
        """🔴 功效列要答「這個功效可以用哪些技術手段達成」（2026-08-03 使用者定案）。

        使用者原話：「技術對照是指我要知道功效可以用那些技術手段達成」。
        資料本來就夠——同一批專利同時有技術通道與功效通道的分派，
        取交集即可，不需要 AI、也不需要新的資料來源。

        ⚠ 只給功效列。技術列不加反向欄（使用者：「技術通道的現在先維持這樣」）。
        ⚠ 取**前三**：實機 8 個功效主題各命中 1–4 種技術手段，取三覆蓋絕大多數，
        且與既有「前三大申請人」同口徑。
        """
        topics = [
            {"topic_code": "T001", "label": "馬達捲繩回收機構",
             "source_field": SOURCE_FIELD_TECHNICAL},
            {"topic_code": "T002", "label": "磁阻調節機構",
             "source_field": SOURCE_FIELD_TECHNICAL},
            {"topic_code": "T001", "label": "提升訓練成效",
             "source_field": SOURCE_FIELD_EFFECT},
        ]
        assignments = (
            [{"topic_code": "T001", "patent_id": p, "source_field": SOURCE_FIELD_TECHNICAL}
             for p in (1, 2, 3)]
            + [{"topic_code": "T002", "patent_id": p, "source_field": SOURCE_FIELD_TECHNICAL}
               for p in (4,)]
            + [{"topic_code": "T001", "patent_id": p, "source_field": SOURCE_FIELD_EFFECT}
               for p in (1, 2, 3, 4)]
        )
        applicants = [{"patent_id": p, "applicant_name": "A"} for p in range(1, 5)]
        rows = build_topic_effect_table(topics, assignments, applicants)
        by_source = {r["source_field"]: r for r in rows}

        effect = by_source[SOURCE_FIELD_EFFECT]
        self.assertEqual(effect["tech_means"], "馬達捲繩回收機構 3、磁阻調節機構 1",
                         "功效列要列出達成它的技術手段（依件數由多到少）")
        self.assertNotIn("tech_means", by_source[SOURCE_FIELD_TECHNICAL],
                         "技術列不加反向對照——使用者要維持現狀")

    def test_years_outside_window_are_excluded(self):
        """2025–2026 屬資料截止效應，不計入任何一窗。"""
        row = self._rows(self._patents({pid: (2026 if pid > 8 else 2022)
                                        for pid in range(1, 11)}))[0]
        self.assertEqual(row["recent_count"], 8)
        self.assertEqual(row["early_count"], 0)

    def test_representative_takes_one_patent_per_top_applicant(self):
        """代表專利＝**前三大申請人各一件**（2026-08-03 使用者：專利號可以取多個）。

        🔴 使用者：「分類有了，但缺證據」。代表專利就是證據。
        ⚠ 取同一家的三件只代表那一家；各取一件才代表這個主題的主要玩家分別在做什麼，
        解讀端才有素材講「A 做 X、B 做 Y」的布局差異。
        ⚠ 表格欄只放專利號——文獻備註 60–100 字放不進欄位（每列要 4–7 行、
        表格區只有 2.88 in），內容由判讀要點講。
        ⚠ 選法必須確定性可重現：每家取申請年最新 → patent_id 最小。
        """
        patents = self._patents({pid: (2020 + pid % 5) for pid in range(1, 11)})
        row = self._rows(patents)[0]
        numbers = row["representative"].split("、")
        self.assertEqual(len(numbers), 3, f"應取前三大申請人各一件，實得 {numbers}")
        self.assertEqual(len(set(numbers)), 3, "同一件不得重複出現")
        self.assertTrue(all(n.startswith("CN") for n in numbers), numbers)

    def test_representative_is_stable_across_runs(self):
        patents = self._patents({pid: 2022 for pid in range(1, 11)})
        first = self._rows(patents)[0]["representative"]
        for _ in range(3):
            self.assertEqual(self._rows(patents)[0]["representative"], first)

    def test_missing_patent_meta_degrades_quietly(self):
        """只有年份、沒有專利號與備註時不得炸——代表專利留空即可。

        ⚠ 「文獻備註」在 `core_layer.patents`，不在 report_patent_base，
        靠 LEFT JOIN 取回；JOIN 不到時要安靜降級，不能讓整張報表產不出來。
        """
        patents = {pid: {"application_year": 2022} for pid in range(1, 11)}
        row = self._rows(patents)[0]
        self.assertIn("status", row)
        self.assertEqual(row["representative"], "")


if __name__ == "__main__":
    unittest.main()
