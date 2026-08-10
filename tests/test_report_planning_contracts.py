"""P2 第 1 節：goal-driven 規劃的資料契約（openspec
enable-goal-driven-readonly-report-planning tasks 1.2）。

五個結構的**唯一定義處**——runner、CLI validator、builder 與前端共用同一份
schema，不各自定義（散開就是同一份知識多個落點）。

契約重點（design.md）：
- ReportBrief：north_star_goal／audience／chapter constraints／directions／
  page budget／snapshot／選圖 identity。
- SelectedChartBundle：**圖片與數據必須成對**，並以 checksum/version 阻止錯配
  （只給圖失去精確數字，只給 JSON 失去視覺判讀）。
- ReportStrategy／SlidePlan：CLI 只產內容與版型**意圖**，不得出現幾何座標。
- EvidenceManifest：每個敘述與建議都要能追到選圖數據或唯讀查詢。
"""
from __future__ import annotations

import unittest

from backend.app.reports import planning_contracts as pc


def _bundle(**over):
    base = dict(chart_identity="applicant_ranking:default", report_key="applicant_ranking",
                variant_key="default", image_path="charts/applicant_ranking.png",
                data_rows=[{"applicant_display_name": "A", "patent_count": 3}],
                population_note="母體 55/55 件", version="v1", checksum="abc123")
    base.update(over)
    return base


def _brief(**over):
    base = dict(north_star_goal="找出可切入的技術空白", audience="研發主管",
                page_budget=12, workspace_id=3, snapshot_id="report_trial_x",
                selected_charts=[_bundle()])
    base.update(over)
    return base


class ReportBriefTests(unittest.TestCase):
    def test_valid_brief_passes(self):
        self.assertEqual(pc.validate_report_brief(_brief()), [])

    def test_goal_is_required(self):
        errors = pc.validate_report_brief(_brief(north_star_goal="  "))
        self.assertTrue(any("north_star_goal" in e for e in errors))

    def test_empty_selection_rejected(self):
        """未選圖不得自動進 PPT（Non-goals：CLI 不得自行選圖）。"""
        errors = pc.validate_report_brief(_brief(selected_charts=[]))
        self.assertTrue(any("selected_charts" in e for e in errors))

    def test_page_budget_must_be_positive(self):
        self.assertTrue(any("page_budget" in e
                            for e in pc.validate_report_brief(_brief(page_budget=0))))

    def test_duplicate_chart_identity_rejected(self):
        errors = pc.validate_report_brief(_brief(selected_charts=[_bundle(), _bundle()]))
        self.assertTrue(any("重複" in e for e in errors))


class SelectedChartBundleTests(unittest.TestCase):
    def test_image_and_data_must_pair(self):
        """只給圖或只給數據都不合格——兩者成對是契約核心。"""
        self.assertTrue(any("data_rows" in e
                            for e in pc.validate_chart_bundle(_bundle(data_rows=[]))))
        self.assertTrue(any("image_path" in e
                            for e in pc.validate_chart_bundle(_bundle(image_path=""))))

    def test_checksum_and_version_required(self):
        for field in ("checksum", "version"):
            with self.subTest(field=field):
                errors = pc.validate_chart_bundle(_bundle(**{field: ""}))
                self.assertTrue(any(field in e for e in errors))


class SlidePlanTests(unittest.TestCase):
    def _plan(self, **over):
        base = dict(
            plan_id="plan-1",
            slides=[{"slide_id": "s1", "layout_preset": "chart_hero",
                     "purpose": "呈現競爭定位",
                     "chart_identities": ["applicant_ranking:default"],
                     "narrative": [{"text": "前二名合計 27 件", "evidence_ref": "e1"}]}],
        )
        base.update(over)
        return base

    def test_valid_plan_passes(self):
        self.assertEqual(pc.validate_slide_plan(self._plan(), {"applicant_ranking:default"}), [])

    def test_unselected_chart_rejected(self):
        """CLI 不得自行加入未選圖表。"""
        errors = pc.validate_slide_plan(self._plan(), {"other:default"})
        self.assertTrue(any("未選" in e for e in errors))

    def test_all_selected_charts_must_appear(self):
        """全部選圖至少出現一次——不得遺漏使用者選的圖。"""
        errors = pc.validate_slide_plan(
            self._plan(), {"applicant_ranking:default", "lifecycle:default"})
        self.assertTrue(any("未使用" in e for e in errors))

    def test_geometry_fields_rejected(self):
        """CLI 只給意圖，不得輸出座標／字級／色彩（deterministic builder 專責）。"""
        plan = self._plan()
        plan["slides"][0]["left_in"] = 1.2
        errors = pc.validate_slide_plan(plan, {"applicant_ranking:default"})
        self.assertTrue(any("幾何" in e for e in errors))

    def test_unknown_layout_preset_rejected(self):
        plan = self._plan()
        plan["slides"][0]["layout_preset"] = "my_custom_layout"
        errors = pc.validate_slide_plan(plan, {"applicant_ranking:default"})
        self.assertTrue(any("layout_preset" in e for e in errors))

    def test_page_budget_enforced(self):
        plan = self._plan(slides=[
            {"slide_id": f"s{i}", "layout_preset": "chart_hero", "purpose": "p",
             "chart_identities": ["applicant_ranking:default"],
             "narrative": [{"text": "t", "evidence_ref": "e1"}]}
            for i in range(5)])
        errors = pc.validate_slide_plan(plan, {"applicant_ranking:default"}, page_budget=3)
        self.assertTrue(any("page_budget" in e for e in errors))


class EvidenceManifestTests(unittest.TestCase):
    MANIFEST = {"e1": {"source": "selected_chart", "chart_identity": "applicant_ranking:default",
                       "snapshot_id": "report_trial_x"}}

    def test_every_narrative_ref_must_resolve(self):
        plan = {"plan_id": "p", "slides": [{
            "slide_id": "s1", "layout_preset": "chart_hero", "purpose": "x",
            "chart_identities": ["applicant_ranking:default"],
            "narrative": [{"text": "t", "evidence_ref": "missing"}]}]}
        errors = pc.validate_evidence(plan, self.MANIFEST, snapshot_id="report_trial_x")
        self.assertTrue(any("evidence_ref" in e for e in errors))

    def test_stale_snapshot_rejected(self):
        errors = pc.validate_evidence(
            {"plan_id": "p", "slides": []}, self.MANIFEST, snapshot_id="other_snapshot")
        self.assertTrue(any("snapshot" in e for e in errors))

    def test_numeric_claim_without_evidence_is_not_blocked(self):
        """帶數字的敘述沒有 evidence_ref → **不阻擋**（2026-08-10 契約變更）。

        🔴 原本擋。`_NUMBER_PATTERN` 是「任何數字」，**連年份都算**——實跑 job 270
        被「2025後回落多屬公開遲延、非真衰退」擋下，而那是判讀句不是統計主張。

        ⚠ 要精準區分「統計數字」與「年份／序號」需要語意判斷，正規表示式做不到；
        誤擋的代價是整份規劃失敗、使用者完全拿不到成品，不成比例。

        防造假改由仍然硬擋的兩道承擔：evidence 的 `source` 標記（narrative 溯源）
        與 `query_audit` 的可追溯性（強制查證）。
        """
        plan = {"plan_id": "p", "slides": [{
            "slide_id": "s1", "layout_preset": "chart_hero", "purpose": "x",
            "chart_identities": ["applicant_ranking:default"],
            "narrative": [{"text": "前二名合計 27 件"}]}]}
        errors = pc.validate_evidence(plan, self.MANIFEST, snapshot_id="report_trial_x")
        self.assertEqual(errors, [], "帶數字但沒 ref 不得阻擋整份規劃")


if __name__ == "__main__":
    unittest.main()
