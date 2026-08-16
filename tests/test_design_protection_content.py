from __future__ import annotations

import unittest


class DesignProtectionContentTests(unittest.TestCase):
    """外觀保護策略與技術交叉素材。"""

    def _rows(self):
        return [
            {
                "patent_id": 1,
                "patent_type": "P",
                "document_kind": "S",
                "application_year": 2023,
                "legal_status": "授權",
                "applicant_display_name": "只外觀",
                "title": "Ski machine",
                "文獻備註": "外觀設計",
                "has_main_figure": True,
            },
            {
                "patent_id": 2,
                "patent_type": "P",
                "document_kind": "S1",
                "application_year": 2024,
                "legal_status": "授權",
                "applicant_display_name": "雙軸公司",
                "title": "Mower",
                "has_main_figure": True,
            },
            {
                "patent_id": 3,
                "patent_type": "P",
                "document_kind": "A",
                "application_year": 2022,
                "legal_status": "審查中",
                "applicant_display_name": "雙軸公司",
                "title": "Walk-behind working machine",
                "分類標籤": "速度控制與人機介面",
                "文獻備註": "顯示器設於把手，整合周邊開關。",
                "has_main_figure": False,
            },
            {
                "patent_id": 4,
                "patent_type": "U",
                "document_kind": "U",
                "application_year": 2021,
                "legal_status": "授權",
                "applicant_display_name": "只技術",
                "分類標籤": "傳動機構",
            },
        ]

    def test_strategy_splits_design_only_and_design_plus_tech(self):
        from backend.app.reports.content_blocks import design_protection_strategy

        rows = design_protection_strategy(self._rows())
        by_name = {row["applicant"]: row for row in rows}

        self.assertEqual(by_name["只外觀"]["strategy_type"], "只走外觀")
        self.assertEqual(by_name["只外觀"]["design_count"], 1)
        self.assertEqual(by_name["只外觀"]["tech_count"], 0)
        self.assertEqual(by_name["雙軸公司"]["strategy_type"], "技術+外觀")
        self.assertEqual(by_name["雙軸公司"]["design_count"], 1)
        self.assertEqual(by_name["雙軸公司"]["tech_count"], 1)
        self.assertNotIn("只技術", by_name, "外觀策略表只列有外觀設計的主體")

    def test_strategy_rows_include_visual_reference_without_pdf(self):
        from backend.app.reports.content_blocks import design_protection_strategy

        first = design_protection_strategy(self._rows())[0]
        self.assertIn("representative_design_patent_id", first)
        self.assertIn("has_figure", first)
        self.assertNotIn("pdf", first)
        self.assertNotIn("url", first)

    def test_intersections_include_tech_labels_and_evidence(self):
        from backend.app.reports.content_blocks import design_tech_intersections

        rows = design_tech_intersections(self._rows())

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["applicant"], "雙軸公司")
        self.assertEqual(row["strategy_type"], "技術+外觀")
        self.assertEqual(row["tech_labels"], ["速度控制與人機介面"])
        self.assertEqual(row["representative_design_patent_id"], 2)
        self.assertEqual(row["representative_tech_patent_id"], 3)
        self.assertIn("顯示器", row["tech_evidence"])


    def test_report_registry_and_section_are_wired(self):
        from backend.app.reports import chart_runner
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        self.assertIn("design_protection_detail", REPORT_DEFINITIONS)
        covered = {name for spec in chart_runner.SECTION_SPECS for name in spec.reports}
        self.assertIn("design_protection_detail", covered)

    def test_strategy_chart_rows_are_aggregate_not_detail_links(self):
        from backend.app.reports.chart_runner import design_strategy_chart_rows
        from backend.app.reports.content_blocks import design_protection_strategy

        rows = design_strategy_chart_rows(design_protection_strategy(self._rows()))

        self.assertEqual(sum(row["patent_count"] for row in rows), 2)
        self.assertFalse(any("pdf" in row or "url" in row for row in rows))


if __name__ == "__main__":
    unittest.main()
