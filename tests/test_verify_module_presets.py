"""scripts.verify_module 的目標測試 preset 接線測試。"""
from __future__ import annotations

import unittest

from scripts import verify_module


class VerifyModulePresetTests(unittest.TestCase):
    """report professionalism preset 必須涵蓋四個交付面向。"""

    def test_report_professionalism_preset_wires_target_groups(self):
        preset = verify_module.VERIFY_PRESETS["report-professionalism"]
        self.assertEqual(set(preset["groups"]), {"report", "transform", "renderer", "narrative"})
        self.assertIn("tests/test_report_catalog_removals.py", preset["tests"])
        self.assertIn("tests/test_annual_trend_four_columns.py", preset["tests"])
        self.assertIn("tests/test_ppt_reader_facing_output.py", preset["tests"])
        self.assertIn("tests/test_narrative_contract_v4.py", preset["tests"])

    def test_resolve_preset_args_fills_missing_cli_targets(self):
        args = verify_module.parse_args(["--preset", "report-professionalism"])
        resolved = verify_module.resolve_preset_args(args)
        self.assertGreaterEqual(len(resolved.tests), 4)
        self.assertIn("backend/app", resolved.paths)
        self.assertIn("backend.app", resolved.source)
        self.assertEqual(resolved.regression_filter, "report or transform or renderer or narrative")


if __name__ == "__main__":
    unittest.main()
