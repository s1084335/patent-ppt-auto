"""PPT builder 動態頁與覆寫契約。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pptx")
from pptx import Presentation  # noqa: E402
from pptx.util import Inches  # noqa: E402


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt"
BUILDER_PATH = SKILL_DIR / "scripts" / "build_ppt.py"


def _load_builder():
    """載入 skill 內的 build_ppt.py。"""
    spec = importlib.util.spec_from_file_location("build_ppt_dynamic", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_ppt_dynamic"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def report_dir(tmp_path: Path) -> Path:
    """建立含基礎資料與一個額外報表的版本。"""
    version = "report_trial_20260723_000000"
    path = tmp_path / "output" / version
    path.mkdir(parents=True)
    data = {
        "parameters": {"version": version},
        "reports": {
            "application_trend": {
                "report_name": "application_trend",
                "label": "Application Trend",
                "label_zh": "申請趨勢",
                "report_type": "trend",
                "rows": [{"year": 2025, "patent_count": 3}],
            },
            "country_distribution": {
                "report_name": "country_distribution",
                "label": "Country Distribution",
                "label_zh": "地域分布",
                "report_type": "distribution",
                "rows": [{"country": "TW", "patent_count": 3}],
            },
            "cluster_topic_table": {
                "report_name": "cluster_topic_table",
                "label": "Cluster Topic Table",
                "label_zh": "全分類技術指標總表",
                "report_type": "table",
                "rows": [{"label": "收繩機構", "patent_count": 3}],
            },
            "owner_year_matrix": {
                "report_name": "owner_year_matrix",
                "label": "Owner Year Matrix",
                "label_zh": "專利權人年份矩陣",
                "report_type": "matrix",
                "rows": [{"company": "Rexon", "2025": 3}],
            },
        },
        "family_reports": {},
    }
    (path / "report_data.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    (path / "narratives.json").write_text(
        json.dumps({"reports": {}}, ensure_ascii=False), encoding="utf-8"
    )
    return path


def test_build_ppt_expands_extra_report_pages_before_appendix_anchor(report_dir, tmp_path):
    """額外報表要插在第一個 is_appendix 頁之前，不用頁碼魔術數字。"""
    builder = _load_builder()
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps({"report_version": "report_trial_20260723_000000", "slots": {}}),
        encoding="utf-8",
    )

    result = builder.build_ppt(
        report_dir=report_dir, approvals_path=approvals, output_dir=tmp_path / "ppt"
    )
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    owner_index = next(
        index for index, page in enumerate(manifest["pages"])
        if page["report_keys"] == ["owner_year_matrix"]
    )
    first_appendix_index = next(
        index for index, page in enumerate(manifest["pages"]) if page["is_appendix"]
    )

    assert owner_index < first_appendix_index
    assert manifest["pages"][first_appendix_index]["report_keys"] == ["cluster_topic_table"]


def test_build_ppt_applies_layout_and_position_overrides(report_dir, tmp_path):
    """仍保留既有版型與座標覆寫相容性。"""
    builder = _load_builder()
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "report_version": "report_trial_20260723_000000",
                "slots": {"cover.title": "manual title"},
                "layout_overrides": {"3": "table"},
                "position_overrides": {
                    "3.table": {
                        "left_in": 1.25,
                        "top_in": 1.5,
                        "width_in": 6.0,
                        "height_in": 3.0,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = builder.build_ppt(
        report_dir=report_dir, approvals_path=approvals, output_dir=tmp_path / "ppt"
    )
    prs = Presentation(result["pptx_path"])
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    page3 = next(page for page in manifest["pages"] if page["page"] == 3)
    assert page3["kind"] == "table"
    assert page3["position_overrides_applied"] == ["3.table"]

    tables = [shape for shape in prs.slides[2].shapes if shape.has_table]
    assert tables
    assert tables[0].left == Inches(1.25)
    assert tables[0].top == Inches(1.5)
