"""PPT 產生器契約測試。

驗證重點（對應 skill 步驟 D 的硬性規格）：
- 依 10 頁版型對照表組裝，頁數與標題正確。
- 缺確認槽的頁標「待確認」浮水印，但不擋整檔產出。
- 輸出檔名帶 report_version，且版本不覆蓋（重跑產生新檔）。
- manifest 記 SHA-256、來源報表版本與各頁槽位填充狀態。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pptx")
from pptx import Presentation  # noqa: E402

# 產生器實作放在 skill 目錄（可攜），測試以檔案路徑載入，不依賴主專案 import 路徑。
SKILL_DIR = Path("D:/力山/.agents/skills/patent-report-ppt")
BUILDER_PATH = SKILL_DIR / "scripts" / "build_ppt.py"


def _load_builder():
    """以檔案路徑載入 skill 內的產生器模組，驗證其可獨立執行。"""
    spec = importlib.util.spec_from_file_location("build_ppt", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_ppt"] = module
    spec.loader.exec_module(module)
    return module


# 最小合法 SVG，供圖片轉換路徑測試使用。
MINIMAL_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">'
    '<rect width="400" height="300" fill="#1F5C3D"/>'
    '<text x="20" y="40" fill="#FFFFFF">trend</text>'
    "</svg>"
)


@pytest.fixture()
def report_dir(tmp_path: Path) -> Path:
    """自建假的報表版本目錄：report_data.json + narratives.json + 一張 SVG。"""
    version = "report_trial_20260723_000000"
    d = tmp_path / "output" / version
    d.mkdir(parents=True)

    report_data = {
        "parameters": {
            "version": version,
            "generated_at": "2026-07-23T00:00:00",
            "scope": "full_database",
            "filters": None,
        },
        "reports": {
            "application_trend": {
                "report_name": "application_trend",
                "label": "Application Trend",
                "label_zh": "申請趨勢",
                "report_type": "trend",
                "row_count": 3,
                "rows": [
                    {"year": 2023, "patent_count": 10},
                    {"year": 2024, "patent_count": 25},
                    {"year": 2025, "patent_count": 18},
                ],
            },
            "applicant_ranking": {
                "report_name": "applicant_ranking",
                "label": "Applicant Ranking",
                "label_zh": "申請人排名",
                "report_type": "ranking",
                "row_count": 2,
                "rows": [
                    {"applicant": "泉峰", "patent_count": 30},
                    {"applicant": "牧田", "patent_count": 21},
                ],
            },
            "country_distribution": {
                "report_name": "country_distribution",
                "label": "Country Distribution",
                "label_zh": "國家分布",
                "report_type": "distribution",
                "row_count": 2,
                "rows": [
                    {"country": "CN", "patent_count": 40},
                    {"country": "US", "patent_count": 13},
                ],
            },
        },
        "family_reports": {},
        "chart_rows": {},
    }
    (d / "report_data.json").write_text(
        json.dumps(report_data, ensure_ascii=False), encoding="utf-8"
    )

    narratives = {
        "based_on_version": version,
        "reports": {
            "application_trend": {
                "variants": {
                    "default": {
                        "text": "2024 年 25 件為高峰，2025 年回落至 18 件。",
                        "ai_model": "test-model",
                        "prompt_version": "report_narrative_v2",
                        "generated_at": "2026-07-23T00:00:00",
                    }
                }
            }
        },
    }
    (d / "narratives.json").write_text(
        json.dumps(narratives, ensure_ascii=False), encoding="utf-8"
    )

    (d / "annual_trend.svg").write_text(MINIMAL_SVG, encoding="utf-8")
    return d


@pytest.fixture()
def approvals(tmp_path: Path) -> Path:
    """確認槽定稿文案：只填部分槽，其餘應觸發待確認浮水印。"""
    path = tmp_path / "approvals.json"
    payload = {
        "report_version": "report_trial_20260723_000000",
        "slots": {
            "cover.title": "自走式割草機 — 專利情報分析",
            "trend.narrative": "2024 年為申請高峰，2025 年回落。",
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _watermarked_slides(prs) -> set[int]:
    """回傳帶「待確認」浮水印的頁碼集合（1-based）。"""
    marked = set()
    for idx, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            if shape.has_text_frame and "待確認" in shape.text_frame.text:
                marked.add(idx)
                break
    return marked


def test_builds_ten_page_deck_with_manifest(report_dir, approvals, tmp_path):
    """產出 .pptx 存在、頁數依版型對照表、manifest 帶 SHA-256 與來源版本。"""
    builder = _load_builder()
    out_dir = tmp_path / "ppt"

    result = builder.build_ppt(
        report_dir=report_dir, approvals_path=approvals, output_dir=out_dir
    )

    pptx_path = Path(result["pptx_path"])
    assert pptx_path.exists()
    # 檔名須帶 report_version，供人工辨識來源報表。
    assert "report_trial_20260723_000000" in pptx_path.name

    prs = Presentation(str(pptx_path))
    assert len(prs.slides) == len(builder.PAGE_LAYOUT)
    assert len(prs.slides) == 10

    manifest_path = Path(result["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_report_version"] == "report_trial_20260723_000000"
    assert len(manifest["sha256"]) == 64
    # 各頁槽位填充狀態需逐頁記錄，供追溯哪些頁尚未過稿。
    assert len(manifest["pages"]) == 10
    assert any(p["watermarked"] for p in manifest["pages"])


def test_missing_slot_pages_get_watermark_without_blocking_output(
    report_dir, approvals, tmp_path
):
    """缺確認槽的頁標「待確認」浮水印，已填槽的頁不標，且整檔仍產出。"""
    builder = _load_builder()
    result = builder.build_ppt(
        report_dir=report_dir, approvals_path=approvals, output_dir=tmp_path / "ppt"
    )

    prs = Presentation(result["pptx_path"])
    marked = _watermarked_slides(prs)

    # 頁 3 申請趨勢的文案槽已定稿 → 不應有浮水印。
    assert 3 not in marked
    # 頁 2 研發方向建議未提供定稿文案 → 必須標待確認。
    assert 2 in marked
    # 不擋整檔產出：仍是完整 10 頁。
    assert len(prs.slides) == 10


def test_all_slots_filled_leaves_no_watermark(report_dir, tmp_path):
    """所有確認槽齊備時，不應有任何頁面帶浮水印。"""
    builder = _load_builder()
    slots = {slot: f"定稿文案 {slot}" for slot in builder.all_slot_keys()}
    approvals_path = tmp_path / "full_approvals.json"
    approvals_path.write_text(
        json.dumps(
            {"report_version": "report_trial_20260723_000000", "slots": slots},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = builder.build_ppt(
        report_dir=report_dir, approvals_path=approvals_path, output_dir=tmp_path / "ppt"
    )
    prs = Presentation(result["pptx_path"])
    assert _watermarked_slides(prs) == set()

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert all(not p["watermarked"] for p in manifest["pages"])


def test_rerun_same_version_does_not_overwrite(report_dir, approvals, tmp_path):
    """同版本重跑不覆蓋既有檔案，改產生帶序號的新檔。"""
    builder = _load_builder()
    out_dir = tmp_path / "ppt"

    first = builder.build_ppt(
        report_dir=report_dir, approvals_path=approvals, output_dir=out_dir
    )
    first_path = Path(first["pptx_path"])
    first_bytes = first_path.read_bytes()

    second = builder.build_ppt(
        report_dir=report_dir, approvals_path=approvals, output_dir=out_dir
    )
    second_path = Path(second["pptx_path"])

    assert second_path != first_path
    # 既有版本內容原封不動。
    assert first_path.exists()
    assert first_path.read_bytes() == first_bytes
    assert second_path.exists()


def test_missing_report_data_pages_degrade_gracefully(tmp_path, approvals):
    """報表資料缺漏時不 crash：頁面仍產出並標記待確認。"""
    builder = _load_builder()
    version = "report_trial_20260723_000000"
    empty_dir = tmp_path / "output" / version
    empty_dir.mkdir(parents=True)
    (empty_dir / "report_data.json").write_text(
        json.dumps({"parameters": {"version": version}, "reports": {}}),
        encoding="utf-8",
    )

    result = builder.build_ppt(
        report_dir=empty_dir, approvals_path=approvals, output_dir=tmp_path / "ppt"
    )
    prs = Presentation(result["pptx_path"])
    assert len(prs.slides) == 10


def test_manifest_records_slot_status_per_page(report_dir, approvals, tmp_path):
    """manifest 逐頁記錄槽位填充狀態，可看出哪些槽缺稿。"""
    builder = _load_builder()
    result = builder.build_ppt(
        report_dir=report_dir, approvals_path=approvals, output_dir=tmp_path / "ppt"
    )
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    pages = {p["page"]: p for p in manifest["pages"]}
    assert pages[3]["filled_slots"] == ["trend.narrative"]
    assert pages[3]["missing_slots"] == []
    assert "direction.body" in pages[2]["missing_slots"]


def test_page_layout_is_table_driven(report_dir, approvals, tmp_path):
    """版型以對照表驅動：report_key → 頁碼位置，改對照表即改版型。"""
    builder = _load_builder()

    # 對照表須涵蓋 10 頁，且每頁宣告 report_key 與槽位，而非寫死在組版邏輯中。
    assert len(builder.PAGE_LAYOUT) == 10
    trend_page = next(p for p in builder.PAGE_LAYOUT if p.page == 3)
    assert "application_trend" in trend_page.report_keys
    assert "trend.narrative" in trend_page.slots


def test_svg_conversion_is_cached(report_dir, approvals, tmp_path, monkeypatch):
    """同一張圖只轉換一次，重複引用走快取。"""
    builder = _load_builder()
    calls: list[Path] = []
    original = builder.rasterize_svg

    def counting(svg_path: Path, cache_dir: Path):
        calls.append(svg_path)
        return original(svg_path, cache_dir)

    monkeypatch.setattr(builder, "rasterize_svg", counting)
    builder.build_ppt(
        report_dir=report_dir, approvals_path=approvals, output_dir=tmp_path / "ppt"
    )

    # 同一 SVG 不得重複轉換。
    assert len(calls) == len(set(calls))
