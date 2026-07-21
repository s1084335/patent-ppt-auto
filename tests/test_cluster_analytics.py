"""cluster_analytics 純邏輯模組單元測試。

純函式層 — 不碰 DB、不碰 I/O。測試輸入 topics / assignments /
normalized_applicants 組合，驗證統計與象限計算結果。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.reports.cluster_analytics import (
    build_opportunity_matrix,
    build_pain_point_matrix,
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


class PainPointMatrixTests(unittest.TestCase):
    """build_pain_point_matrix: 痛點四象限，共用機會 X 中位數。"""

    def test_basic_pain_mapping(self):
        topic_rows = [
            {"topic_code": "T01", "patent_count": 10, "applicant_count": 5,
             "top_applicants": []},
            {"topic_code": "T02", "patent_count": 3, "applicant_count": 2,
             "top_applicants": []},
        ]
        pain_data = [
            {"topic_code": "T01", "severity": "high", "basis": "訴訟數量上升",
             "source": "docket"},
        ]
        result = build_pain_point_matrix(topic_rows, pain_data, x_median=6.5)
        self.assertEqual(len(result["rows"]), 2)
        t01 = next(r for r in result["rows"] if r["topic_code"] == "T01")
        t02 = next(r for r in result["rows"] if r["topic_code"] == "T02")
        self.assertEqual(t01["severity"], "high")
        self.assertEqual(t01["basis"], "訴訟數量上升")
        self.assertEqual(t01["source"], "docket")
        self.assertEqual(t02["severity"], "unknown")
        self.assertIsNone(t02["basis"])
        self.assertIsNone(t02["source"])

    def test_label_passthrough(self):
        """regression（2026-07-21）：同機會矩陣，痛點 rows 也必須帶 label。"""
        topic_rows = [
            {"topic_code": "T01", "label": "散熱防塵", "patent_count": 11,
             "applicant_count": 6, "top_applicants": []},
        ]
        result = build_pain_point_matrix(topic_rows, [], x_median=6.5)
        self.assertEqual(result["rows"][0].get("label"), "散熱防塵",
                         "pain rows 必須帶 label 供圖表顯示中文主題名")

    def test_severity_distribution(self):
        topic_rows = [
            {"topic_code": f"T{i:02d}", "patent_count": i * 5,
             "applicant_count": i, "top_applicants": []}
            for i in range(1, 6)
        ]
        pain_data = [
            {"topic_code": "T01", "severity": "high", "basis": "x", "source": "a"},
            {"topic_code": "T03", "severity": "medium", "basis": "y", "source": "b"},
            {"topic_code": "T05", "severity": "low", "basis": "z", "source": "c"},
        ]
        result = build_pain_point_matrix(topic_rows, pain_data, x_median=12.0)
        sevs = {r["topic_code"]: r["severity"] for r in result["rows"]}
        self.assertEqual(sevs["T01"], "high")
        self.assertEqual(sevs["T03"], "medium")
        self.assertEqual(sevs["T05"], "low")
        self.assertEqual(sevs["T02"], "unknown")
        self.assertEqual(sevs["T04"], "unknown")
        self.assertEqual(result["x_median"], 12.0)

    def test_empty_pain_data(self):
        topic_rows = [
            {"topic_code": "T01", "patent_count": 5, "applicant_count": 3,
             "top_applicants": []},
        ]
        result = build_pain_point_matrix(topic_rows, [], x_median=5.0)
        self.assertEqual(result["rows"][0]["severity"], "unknown")


if __name__ == "__main__":
    unittest.main()
