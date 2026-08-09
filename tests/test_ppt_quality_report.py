"""PptQualityReport schema 與 warning 決策契約測試。"""
from __future__ import annotations

import unittest

from backend.app.reports import planning_contracts as pc


def _base_manifest(warnings=None, slides=None, page_count=2):
    return {
        "report_version_id": "rv-1",
        "pptx_path": "output/report.pptx",
        "page_count": page_count,
        "warnings": warnings or [],
        "slides": slides or [
            {
                "slide_id": "s1",
                "chart_identities": ["owner_ranking:default"],
                "evidence_refs": ["e1"],
                "required_slots": ["title", "narrative"],
                "filled_slots": ["title", "narrative"],
            },
            {
                "slide_id": "s2",
                "chart_identities": [],
                "evidence_refs": [],
                "required_slots": ["title"],
                "filled_slots": ["title"],
            },
        ],
    }


def _render_manifest(success=True, page_count=2):
    return {
        "render_success": success,
        "page_count": page_count,
        "pages": [
            {"page": 1, "png_path": "render/page-1.png", "sha256": "a", "rendered": success},
            {"page": 2, "png_path": "render/page-2.png", "sha256": "b", "rendered": success},
        ],
    }


class PptQualitySchemaTests(unittest.TestCase):
    """四個 JSON schema 必須集中定義且可被 runner/CLI 共用。"""

    def test_quality_json_schemas_define_decision_retry_and_blocked_defects(self):
        schemas = pc.PPT_QUALITY_JSON_SCHEMAS
        self.assertEqual(
            set(schemas),
            {"PptQualityReport", "RenderedPngManifest", "RegenerationPlan", "ScopeLock"},
        )
        self.assertEqual(
            schemas["PptQualityReport"]["properties"]["decision"]["enum"],
            ["pass", "regenerate_partial", "regenerate_report_version", "blocked_defect"],
        )
        self.assertEqual(
            schemas["RegenerationPlan"]["properties"]["retry_limit"]["maximum"],
            pc.PPT_QUALITY_RETRY_LIMIT,
        )
        self.assertEqual(
            schemas["RegenerationPlan"]["properties"]["blocked_defect_type"]["enum"],
            ["blocked_content_defect", "blocked_layout_defect"],
        )


class PptQualityDecisionTests(unittest.TestCase):
    """warning 到 quality decision 的契約不可漂移。"""

    def _report_for_warning(self, warning_type, **warning):
        warning.setdefault("type", warning_type)
        warning.setdefault("slide_id", "s1")
        return pc.build_ppt_quality_report(
            pptx_manifest=_base_manifest(warnings=[warning]),
            rendered_png_manifest=_render_manifest(),
            selected_chart_identities=["owner_ranking:default"],
            evidence_manifest={"e1": {"snapshot_id": "snap-1"}},
        )

    def test_content_warnings_request_partial_regeneration(self):
        for warning_type in (
            "narrative_missing",
            "narrative_fallback",
            "chart_missing_degraded",
            "missing_slots",
            "text_overflow_estimated",
        ):
            with self.subTest(warning_type=warning_type):
                report = self._report_for_warning(warning_type)
                self.assertEqual(report["decision"], "regenerate_partial")
                self.assertEqual(report["regeneration_plan"]["retry_limit"], 2)
                self.assertEqual(report["regeneration_plan"]["targets"][0]["warning_type"], warning_type)

    def test_artifact_manifest_missing_regenerates_report_version(self):
        report = self._report_for_warning("artifact_manifest_missing")
        self.assertEqual(report["decision"], "regenerate_report_version")
        self.assertEqual(report["regeneration_plan"]["targets"][0]["scope"], "report_version")

    def test_layout_warnings_block_as_layout_defect(self):
        for warning_type in ("text_overlap", "out_of_bounds"):
            with self.subTest(warning_type=warning_type):
                report = self._report_for_warning(warning_type)
                self.assertEqual(report["decision"], "blocked_defect")
                self.assertEqual(report["blocked_defect_type"], "blocked_layout_defect")

    def test_overflow_after_retry_limit_blocks_as_content_defect(self):
        report = pc.build_ppt_quality_report(
            pptx_manifest=_base_manifest(warnings=[{"type": "text_overflow_estimated", "slide_id": "s1"}]),
            rendered_png_manifest=_render_manifest(),
            selected_chart_identities=["owner_ranking:default"],
            evidence_manifest={"e1": {"snapshot_id": "snap-1"}},
            retry_counts={"s1:text_overflow_estimated": 2},
        )
        self.assertEqual(report["decision"], "blocked_defect")
        self.assertEqual(report["blocked_defect_type"], "blocked_content_defect")

    def test_png_render_failure_blocks_as_layout_defect(self):
        report = pc.build_ppt_quality_report(
            pptx_manifest=_base_manifest(),
            rendered_png_manifest=_render_manifest(success=False),
            selected_chart_identities=["owner_ranking:default"],
            evidence_manifest={"e1": {"snapshot_id": "snap-1"}},
        )
        self.assertEqual(report["decision"], "blocked_defect")
        self.assertEqual(report["blocked_defect_type"], "blocked_layout_defect")
        self.assertTrue(any(issue["type"] == "png_render_failed" for issue in report["issues"]))

    def test_page_count_mismatch_blocks_as_layout_defect(self):
        report = pc.build_ppt_quality_report(
            pptx_manifest=_base_manifest(page_count=3),
            rendered_png_manifest=_render_manifest(page_count=2),
            selected_chart_identities=["owner_ranking:default"],
            evidence_manifest={"e1": {"snapshot_id": "snap-1"}},
        )
        self.assertEqual(report["decision"], "blocked_defect")
        self.assertEqual(report["blocked_defect_type"], "blocked_layout_defect")
        self.assertTrue(any(issue["type"] == "page_count_mismatch" for issue in report["issues"]))


class PptQualityReportGeneratorTests(unittest.TestCase):
    """產生器需彙整 manifest、選圖覆蓋、evidence coverage、slot 與版面 warnings。"""

    def test_report_summarizes_coverage_and_synthesizes_required_slot_warnings(self):
        manifest = _base_manifest(
            slides=[
                {
                    "slide_id": "s1",
                    "chart_identities": ["owner_ranking:default"],
                    "evidence_refs": ["e1", "missing-evidence"],
                    "required_slots": ["title", "narrative", "chart"],
                    "filled_slots": ["title"],
                }
            ]
        )
        report = pc.build_ppt_quality_report(
            pptx_manifest=manifest,
            rendered_png_manifest=_render_manifest(),
            selected_chart_identities=["owner_ranking:default", "lifecycle:default"],
            evidence_manifest={"e1": {"snapshot_id": "snap-1"}},
        )
        self.assertEqual(report["decision"], "regenerate_partial")
        self.assertEqual(report["selected_chart_coverage"]["missing"], ["lifecycle:default"])
        self.assertEqual(report["evidence_coverage"]["missing"], ["missing-evidence"])
        self.assertEqual(report["required_slot_coverage"]["missing"], {"s1": ["chart", "narrative"]})
        self.assertTrue(any(issue["type"] == "missing_slots" for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
