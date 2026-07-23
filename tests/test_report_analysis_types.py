"""第 5 輪契約：run_chart_trial 輸出必須涵蓋六大分析類型（前端/PPT Skill 消費入口）。

Red 階段：驗證當 cluster_data 提供時，report_data.json 的 sections 與 chart_rows
應包含所有六大分析類型的反應（sections 條目、chart_rows 鍵、SVG/HTML 檔案）。

六大分析類型（2026-07-23 報表定案）：
  #1 申請人分析          → applicant_ranking report + section
  #2 專利權人分析        → owner_ranking + recent_assignee_ranking reports + sections
  #3 公司 × 國家分       → applicant_country_distribution report + section
  #4 主題 × 公司交叉     → cluster_analytics → chart_rows["cluster_topic_table"] + HTML
  #5 專利佈局 × 競爭者結構 → cluster_analytics → chart_rows["opportunity_quadrant*"] + SVG
  #6 多點 × 專利強度     → cluster_analytics → chart_rows["pain_point_quadrant*"] + SVG
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.reports import chart_runner
from backend.app.reports.report_definitions import REPORT_DEFINITIONS


# ── 標準報表型（#1–#3）：REPORT_DEFINITIONS 中的正式 report key ──
STANDARD_ANALYSIS_REPORTS: dict[str, str] = {
    "申請人分析": "applicant_ranking",
    "專利權人分析:專利權人": "owner_ranking",
    "專利權人分析:最新受讓人": "recent_assignee_ranking",
    "公司×國家分": "applicant_country_distribution",
}

# ── 分群分析型（#4–#6）：chart_rows 鍵（由 cluster_data 驅動）──
CLUSTER_ANALYSIS_CHART_KEYS: dict[str, str] = {
    "主題×公司交叉": "cluster_topic_table",
    "專利佈局×競爭者結構": "opportunity_quadrant",
    "多點×專利強度": "pain_point_quadrant",
}

# ── 分群分析變體檔案 ──
CLUSTER_ANALYSIS_FILES: dict[str, tuple[str, ...]] = {
    "主題×公司交叉 (HTML)": ("cluster_topic_table.html",),
    "專利佈局×競爭者結構 (SVG, 多來源)": ("opportunity_quadrant.svg", "opportunity_quadrant_tech.svg", "opportunity_quadrant_effect.svg"),
    "多點×專利強度 (SVG, 多來源)": ("pain_point_quadrant.svg", "pain_point_quadrant_tech.svg", "pain_point_quadrant_effect.svg"),
}


def _stub_run_report(name: str, **kwargs) -> dict:
    """fake report rows — 避免真 DB，回傳 shape 與報告定義一致。

    年份報表：2010-2019（每列含 group_by 欄 + patent_count + aggregate 欄）。
    排名報表：25 筆依序（每列含 group_by 欄 + patent_count + aggregate 欄）。
    detail 報表：family_quality_detail 給真實整數值。
    """
    definition = REPORT_DEFINITIONS.get(name)
    if not definition:
        return {"report_name": name, "rows": [], "row_count": 0}

    if definition.report_type == "detail":
        rows = []
        for i in range(3):
            row = {}
            for col in definition.columns:
                if col in ("family_id",):
                    row[col] = 1000 + i
                elif col in ("member_rows", "expected_counts_raw", "unknown_status_count",
                             "pending_status_count", "ep_in_transition_count",
                             "ep_missing_epc_count", "non_country_row_count"):
                    row[col] = i * 5
                elif col in ("is_surrogate_family", "family_incomplete"):
                    row[col] = i == 0
                else:
                    row[col] = f"val_{i}"
            rows.append(row)
    else:
        is_time_series = any("year" in col.lower() for col in definition.group_by)
        if is_time_series:
            rows = []
            for y in range(2010, 2020):
                row = {col: y for col in definition.group_by}
                row["patent_count"] = max(5, (y - 2000) * 3)
                for _func, _col, alias in definition.aggregates:
                    row[alias] = max(1, y - 2005)
                rows.append(row)
        else:
            rows = []
            for i in range(25):
                row = {}
                for col in definition.group_by:
                    if col in ("applicant_display_name", "current_assignee_display_name",
                               "recent_assignee_display_name"):
                        row[col] = f"Company_{chr(65 + i % 26)}"
                    elif col == "country_code":
                        row[col] = ["US", "CN", "JP", "KR", "TW", "EP"][i % 6]
                    else:
                        row[col] = f"val_{i}"
                row["patent_count"] = (25 - i) * 2
                for _func, _col, alias in definition.aggregates:
                    row[alias] = max(1, 10 - i)
                rows.append(row)

    return {
        "report_name": name,
        "label_zh": definition.label_zh,
        "label": definition.label,
        "report_type": definition.report_type,
        "row_count": len(rows),
        "rows": rows,
    }


_SAMPLE_CLUSTER_DATA = {
    "topics": [
        {"topic_code": "T001", "label": "散熱防塵", "source_field": "wips_independent_claims"},
        {"topic_code": "T002", "label": "速度控制", "source_field": "wips_independent_claims"},
        {"topic_code": "E001", "label": "降噪效果", "source_field": "effect_summary"},
    ],
    "assignments": [
        {"topic_code": "T001", "patent_id": 1},
        {"topic_code": "T002", "patent_id": 2},
        {"topic_code": "E001", "patent_id": 3},
    ],
    "normalized_applicants": [
        {"patent_id": 1, "applicant_name": "TSMC"},
        {"patent_id": 2, "applicant_name": "UMC"},
        {"patent_id": 3, "applicant_name": "TSMC"},
    ],
    "top_applicants_ws": ["TSMC"],
}


class ReportDataContractTests(unittest.TestCase):
    """report_data.json 輸出結構契約：涵蓋六大分析類型。"""

    def test_standard_analysis_reports_exist_in_output(self):
        """#1–#3 標準報表分析型必須出現在 report_data.json.reports 中。"""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", _stub_run_report):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp), report_names=list(STANDARD_ANALYSIS_REPORTS.values()),
                )
            rd_path = Path(result["output_dir"]) / "report_data.json"
            self.assertTrue(rd_path.exists(), "report_data.json 必須存在")
            rd = json.loads(rd_path.read_text(encoding="utf-8"))
        for label, key in STANDARD_ANALYSIS_REPORTS.items():
            self.assertIn(key, rd.get("reports", {}),
                          f"[{label}] 報表 {key} 不在 report_data.json.reports 中")

    def test_cluster_analytics_keys_in_chart_rows(self):
        """#4–#6 分群分析型必須出現在 chart_rows（cluster_data 提供時）。"""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", _stub_run_report):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp),
                    cluster_data=_SAMPLE_CLUSTER_DATA,
                )
            rd_path = Path(result["output_dir"]) / "report_data.json"
            self.assertTrue(rd_path.exists())
            rd = json.loads(rd_path.read_text(encoding="utf-8"))
        chart_keys = rd.get("chart_rows", {})
        for label, key in CLUSTER_ANALYSIS_CHART_KEYS.items():
            matching = [k for k in chart_keys if k.startswith(key)]
            self.assertTrue(matching,
                            f"[{label}] chart_rows 缺少以「{key}」開頭的鍵（現有鍵：{list(chart_keys)}）")
            for matched_key in matching:
                self.assertTrue(chart_keys[matched_key],
                                f"[{label}] chart_rows.{matched_key} 不應為空")

    def test_cluster_analytics_files_on_disk(self):
        """#4–#6 分群分析 SVG/HTML 檔案必須存在於輸出目錄。"""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", _stub_run_report):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp),
                    cluster_data=_SAMPLE_CLUSTER_DATA,
                )
            run_dir = Path(result["output_dir"])
            for label, filenames in CLUSTER_ANALYSIS_FILES.items():
                found = [f for f in filenames if (run_dir / f).exists()]
                self.assertTrue(found,
                                f"[{label}] 輸出目錄缺少這些檔案：{filenames}（現有：{os.listdir(run_dir)}）")

    def test_cluster_analytics_section_in_sections(self):
        """分群分析 (cluster_analytics) 必須在 sections 中。"""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", _stub_run_report):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp),
                    cluster_data=_SAMPLE_CLUSTER_DATA,
                )
            rd_path = Path(result["output_dir"]) / "report_data.json"
            rd = json.loads(rd_path.read_text(encoding="utf-8"))
        sections = rd.get("sections", [])
        cluster_sections = [s for s in sections if s.get("report_key") == "cluster_topic_table"]
        self.assertEqual(len(cluster_sections), 1,
                         "report_data.json.sections 必須包含一個 report_key=cluster_topic_table 的分群分析區塊")
        cs = cluster_sections[0]
        variant_files = [v["file"] for v in cs.get("variants", [])]
        for required in ("cluster_topic_table.html", "opportunity_quadrant", "pain_point_quadrant"):
            self.assertTrue(
                any(required in f for f in variant_files),
                f"分群分析 section variants 必須包含 {required}（現有：{variant_files}）")

    def test_no_cluster_data_skips_cluster_section(self):
        """cluster_data 未提供時，分群分析 sections 不應出現。"""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", _stub_run_report):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp),
                )
            rd_path = Path(result["output_dir"]) / "report_data.json"
            rd = json.loads(rd_path.read_text(encoding="utf-8"))
        cluster_sections = [s for s in rd.get("sections", [])
                            if s.get("report_key") == "cluster_topic_table"]
        self.assertEqual(len(cluster_sections), 0,
                         "cluster_data 未提供時不應出現分群分析 section")


class SixAnalysisTypesContractTests(unittest.TestCase):
    """六大分析類型的完整覆蓋檢查（不跑渲染，只檢查定義與註冊表完整性）。"""

    def test_all_six_types_referenced_by_section_specs(self):
        """六大分析類型必須在 SECTION_SPECS 中有對應條目。"""
        spec_report_names: set[str] = set()
        for spec in chart_runner.SECTION_SPECS:
            spec_report_names.update(spec.reports)

        # #1–#3 必須有 section spec 提供圖表渲染
        for label, key in STANDARD_ANALYSIS_REPORTS.items():
            self.assertIn(key, spec_report_names,
                          f"[{label}] 報表 {key} 在 SECTION_SPECS 中無對應條目")

        # #4–#6 透過 cluster_analytics section 處理
        self.assertIn("cluster_analytics", spec_report_names,
                      "分群分析 (cluster_analytics) 在 SECTION_SPECS 中無對應條目")


if __name__ == "__main__":
    unittest.main()
