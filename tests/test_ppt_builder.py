"""PPT builder v2.3 契約測試。

批次 1 重點：基礎 8 頁、選擇驅動出頁、缺漏只寫 manifest、
不再把「待確認」浮水印印進 PPT。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pptx")
from pptx import Presentation  # noqa: E402


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt"
BUILDER_PATH = SKILL_DIR / "scripts" / "build_ppt.py"


def _load_builder():
    """直接載入 skill 內的 deterministic PPT builder。"""
    spec = importlib.util.spec_from_file_location("build_ppt", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_ppt"] = module
    spec.loader.exec_module(module)
    return module


MINIMAL_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">'
    '<rect width="400" height="300" fill="#1F5C3D"/>'
    '<text x="20" y="40" fill="#FFFFFF">chart</text>'
    "</svg>"
)


def _write_report_dir(path: Path, *, reports: dict, parameters: dict | None = None) -> Path:
    """建立 builder 測試用報表目錄。"""
    version = "report_trial_20260723_000000"
    path.mkdir(parents=True)
    payload = {
        "parameters": {
            "version": version,
            "generated_at": "2026-07-23T00:00:00",
            "topic_run_id": "topic-run-001",
            "topic_state_version": "state-v3",
            **(parameters or {}),
        },
        "reports": reports,
        "family_reports": {},
        "chart_rows": {},
    }
    (path / "report_data.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    (path / "narratives.json").write_text(
        json.dumps({"based_on_version": version, "reports": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    for name in (
        "annual_trend.svg",
        "application_growth.svg",
        "cluster_topic_table.svg",
        "applicant_country_matrix.svg",
        "applicant_year_matrix.svg",
        "opportunity_quadrant.svg",
    ):
        (path / name).write_text(MINIMAL_SVG, encoding="utf-8")
    return path


@pytest.fixture()
def full_report_dir(tmp_path: Path) -> Path:
    """含有基礎 8 頁所需資料的報表版本。"""
    reports = {
        "application_trend": {
            "report_type": "trend",
            "rows": [
                {"year": 2023, "patent_count": 10},
                {"year": 2024, "patent_count": 25},
                {"year": 2025, "patent_count": 18},
            ],
        },
        "country_distribution": {
            "report_type": "distribution",
            "rows": [
                {"country": "CN", "patent_count": 40},
                {"country": "US", "patent_count": 13},
            ],
        },
        "cluster_topic_table": {
            "label_zh": "全分類技術指標總表",
            "report_type": "table",
            "rows": [{"label": "收繩機構", "patent_count": 8}],
        },
        "applicant_ranking": {
            "report_type": "ranking",
            "rows": [{"applicant": "Rexon", "patent_count": 30}],
        },
        "applicant_country_distribution": {
            "report_type": "matrix",
            "rows": [{"applicant": "Rexon", "CN": 12, "US": 5}],
        },
        "opportunity_quadrant": {
            "report_type": "matrix",
            "rows": [{"label": "收繩機構", "quadrant": "新興戰場"}],
        },
        "owner_ranking": {
            "report_type": "ranking",
            "rows": [{"owner": "Rexon", "patent_count": 22}],
        },
    }
    return _write_report_dir(tmp_path / "output" / "report_trial_20260723_000000", reports=reports)


@pytest.fixture()
def partial_approvals(tmp_path: Path) -> Path:
    """只填部分文案，用來驗證缺漏記錄但不浮水印。"""
    path = tmp_path / "approvals.json"
    path.write_text(
        json.dumps(
            {
                "report_version": "report_trial_20260723_000000",
                "slots": {
                    "cover.title": "專利情報整合分析",
                    "trend.narrative": "2024 年為高峰，2025 年回落。",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _slide_texts(prs: Presentation) -> list[str]:
    """擷取投影片文字供契約測試檢查。"""
    texts: list[str] = []
    for slide in prs.slides:
        chunks: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                chunks.append(shape.text_frame.text)
        texts.append("\n".join(chunks))
    return texts


def test_base_layout_v23_is_8_pages_without_market_or_pain_slots():
    """基礎 PAGE_LAYOUT 必須同步到 v2.3：8 頁，移除痛點與市場槽。"""
    builder = _load_builder()

    assert len(builder.PAGE_LAYOUT) == 8
    assert not any("pain_point" in key for key in builder.all_slot_keys())
    assert not any("market." in key or key.endswith(".market") for key in builder.all_slot_keys())
    assert "key_players.summary" in builder.all_slot_keys()
    assert any(getattr(spec, "is_appendix", False) for spec in builder.PAGE_LAYOUT)


def test_builds_8_page_deck_with_manifest_metadata(full_report_dir, partial_approvals, tmp_path):
    """資料完整時產出 8 頁，manifest 帶來源與分群追溯 metadata。"""
    builder = _load_builder()
    result = builder.build_ppt(
        report_dir=full_report_dir,
        approvals_path=partial_approvals,
        output_dir=tmp_path / "ppt",
    )

    pptx_path = Path(result["pptx_path"])
    assert pptx_path.exists()
    prs = Presentation(str(pptx_path))
    assert len(prs.slides) == 8

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["source_report_version"] == "report_trial_20260723_000000"
    assert len(manifest["sha256"]) == 64
    assert len(manifest["pages"]) == 8
    assert manifest["metadata"]["topic_run_id"] == "topic-run-001"
    assert manifest["metadata"]["topic_state_version"] == "state-v3"


def test_missing_slots_do_not_print_watermark_but_manifest_records_them(
    full_report_dir, partial_approvals, tmp_path
):
    """缺文案只進 manifest；PPT 本體不得出現浮水印。"""
    builder = _load_builder()
    result = builder.build_ppt(
        report_dir=full_report_dir,
        approvals_path=partial_approvals,
        output_dir=tmp_path / "ppt",
    )

    prs = Presentation(result["pptx_path"])
    assert "待確認" not in "\n".join(_slide_texts(prs))

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    pages = {p["page"]: p for p in manifest["pages"]}
    assert "direction.body" in pages[2]["missing_slots"]
    assert pages[2]["watermarked"] is False


def test_cover_stats_include_year_range(full_report_dir, partial_approvals, tmp_path):
    """封面第三格要顯示 application_trend 的年份區間。"""
    builder = _load_builder()
    result = builder.build_ppt(
        report_dir=full_report_dir,
        approvals_path=partial_approvals,
        output_dir=tmp_path / "ppt",
    )

    prs = Presentation(result["pptx_path"])
    cover_text = _slide_texts(prs)[0]
    assert "2023" in cover_text
    assert "2025" in cover_text


def test_unselected_or_empty_reports_do_not_create_their_pages(tmp_path, partial_approvals):
    """非 cover/direction 頁面必須由實際有資料的 report_key 驅動。"""
    builder = _load_builder()
    report_dir = _write_report_dir(
        tmp_path / "output" / "report_trial_20260723_000000",
        reports={
            "application_trend": {
                "report_type": "trend",
                "rows": [{"year": 2025, "patent_count": 3}],
            },
            "country_distribution": {
                "report_type": "distribution",
                "rows": [{"country": "TW", "patent_count": 3}],
            },
        },
    )

    result = builder.build_ppt(
        report_dir=report_dir,
        approvals_path=partial_approvals,
        output_dir=tmp_path / "ppt",
    )
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    assert [p["kind"] for p in manifest["pages"]] == [
        "cover",
        "direction",
        "chart_with_narrative",
    ]
    assert all("cluster_topic_table" not in p["report_keys"] for p in manifest["pages"])


def test_manifest_records_missing_report_keys_for_always_on_pages(tmp_path, partial_approvals):
    """cover/direction 恆出時，缺少的資料 key 要進 missing_reports。"""
    builder = _load_builder()
    report_dir = _write_report_dir(
        tmp_path / "output" / "report_trial_20260723_000000",
        reports={},
    )

    result = builder.build_ppt(
        report_dir=report_dir,
        approvals_path=partial_approvals,
        output_dir=tmp_path / "ppt",
    )
    prs = Presentation(result["pptx_path"])
    assert len(prs.slides) == 2

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    cover = manifest["pages"][0]
    assert set(cover["missing_reports"]) >= {"country_distribution", "application_trend"}
    assert manifest["missing_report_total"] >= 2


def test_rerun_same_version_does_not_overwrite(full_report_dir, partial_approvals, tmp_path):
    """同版本重跑要產生 _r2，不覆蓋舊 PPT。"""
    builder = _load_builder()
    out_dir = tmp_path / "ppt"

    first = builder.build_ppt(
        report_dir=full_report_dir, approvals_path=partial_approvals, output_dir=out_dir
    )
    first_path = Path(first["pptx_path"])
    first_bytes = first_path.read_bytes()

    second = builder.build_ppt(
        report_dir=full_report_dir, approvals_path=partial_approvals, output_dir=out_dir
    )
    second_path = Path(second["pptx_path"])

    assert second_path != first_path
    assert first_path.exists()
    assert first_path.read_bytes() == first_bytes
    assert second_path.exists()


def test_page_layout_is_table_driven():
    """版型仍由 PAGE_LAYOUT 單一來源驅動。"""
    builder = _load_builder()

    trend_page = next(p for p in builder.PAGE_LAYOUT if p.page == 3)
    appendix2 = next(p for p in builder.PAGE_LAYOUT if p.page == 8)
    assert "application_trend" in trend_page.report_keys
    assert "trend.narrative" in trend_page.slots
    assert appendix2.slots == ("key_players.summary",)


def test_svg_conversion_is_cached(full_report_dir, partial_approvals, tmp_path, monkeypatch):
    """同一張 SVG 不重複轉檔。"""
    builder = _load_builder()
    calls: list[Path] = []
    original = builder.rasterize_svg

    def counting(svg_path: Path, cache_dir: Path):
        calls.append(svg_path)
        return original(svg_path, cache_dir)

    monkeypatch.setattr(builder, "rasterize_svg", counting)
    builder.build_ppt(
        report_dir=full_report_dir,
        approvals_path=partial_approvals,
        output_dir=tmp_path / "ppt",
    )

    assert len(calls) == len(set(calls))
