"""專利分析報告 PPTX 產生器（deterministic，不呼叫 AI）。

設計原則：
- 引擎產可信結構化資料，本程式只組版；任何數字一律取自 `report_data.json`，不推算、不捏造。
- 版型由 `PAGE_LAYOUT` 對照表驅動（report_key → 第幾頁哪個位置）；報表增減改對照表，不改組版邏輯。
- 缺確認槽的頁標「待確認」浮水印，但不擋整檔產出。
- 輸出檔名帶 report_version 且版本不覆蓋；同版本重跑產生帶序號新檔。
- 外觀（配色、字體、版面）讀 `theme.json`，抽自範例 PPT。

獨立執行方式（不依賴主專案 import 路徑）：
    uv run --no-project --with python-pptx --python 3.12 \
        python .agents/skills/patent-report-ppt/scripts/build_ppt.py \
        --report-dir <報表版本目錄> --approvals <確認槽 JSON> [--output-dir <輸出目錄>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

SKILL_ROOT = Path(__file__).resolve().parent.parent
THEME_PATH = SKILL_ROOT / "theme.json"

WATERMARK_TEXT = "待確認"


# --------------------------------------------------------------------------
# 版型對照表：頁面 → 資料來源 report_key、圖檔、AI 文案槽
# 唯一來源＝.agents/context/report-requirements.md「範例 PPT 逐頁盤點」表。
# 報表增減時只改本表，不動組版函式。
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PageSpec:
    """單頁版型定義。

    page：頁碼（1-based）。
    kind：組版樣式（決定用哪個 render 函式）。
    title／subtitle：標題與副標（可含 {slot} 由確認槽填入）。
    report_keys：本頁引用的 report_data.json report_key，依序取用。
    charts：本頁配圖候選檔名（依序取第一個存在者）。
    slots：本頁需經使用者確認的文案槽；缺漏只寫 manifest，不印進 PPT。
    is_appendix：附錄段顯式旗標，動態插頁靠它找錨點，不用頁碼魔術數字。
    """

    page: int
    kind: str
    title: str
    report_keys: tuple[str, ...] = ()
    charts: tuple[str, ...] = ()
    slots: tuple[str, ...] = ()
    subtitle: str = ""
    is_appendix: bool = False


PAGE_LAYOUT: tuple[PageSpec, ...] = (
    PageSpec(
        page=1,
        kind="cover",
        title="專利情報整合分析",
        report_keys=("country_distribution", "application_trend", "lifecycle"),
        slots=("cover.title",),
        subtitle="統計時間段",
    ),
    PageSpec(
        page=2,
        kind="direction",
        title="研發方向建議",
        report_keys=(),
        charts=("opportunity_quadrant.svg", "cluster_topic_table.svg"),
        slots=("direction.body",),
        subtitle="綜合本次實際包含的報表產出建議",
    ),
    PageSpec(
        page=3,
        kind="chart_with_narrative",
        title="申請趨勢",
        report_keys=("application_trend", "publication_trend"),
        charts=("annual_trend.svg", "application_growth.svg"),
        slots=("trend.narrative",),
    ),
    PageSpec(
        page=4,
        kind="chart_with_narrative",
        title="技術分布",
        report_keys=("cluster_topic_table",),
        charts=("cluster_topic_table.svg", "ipc_main_distribution_L4.svg"),
        slots=("tech.narrative",),
    ),
    PageSpec(
        page=5,
        kind="chart_with_narrative",
        title="競爭者佈局",
        report_keys=("applicant_country_distribution", "applicant_ranking"),
        charts=("applicant_country_matrix.svg", "applicant_year_matrix.svg"),
        slots=("competitor.narrative",),
    ),
    PageSpec(
        page=6,
        kind="chart_with_narrative",
        title="機會評估四象限",
        report_keys=("opportunity_quadrant",),
        charts=("opportunity_quadrant.svg",),
        slots=("opportunity.narrative",),
    ),
    PageSpec(
        page=7,
        kind="table",
        title="附錄1：全分類技術指標總表",
        report_keys=("cluster_topic_table",),
        slots=(),
        is_appendix=True,
    ),
    PageSpec(
        page=8,
        kind="table_with_narrative",
        title="附錄2：主要專利權人與申請人",
        report_keys=("applicant_ranking", "owner_ranking"),
        slots=("key_players.summary",),
        is_appendix=True,
    ),
)


def _copy_page_spec(spec: PageSpec, *, page: int | None = None, kind: str | None = None) -> PageSpec:
    """建立覆寫後的頁面規格；PageSpec 是 frozen，不能原地修改。"""
    return PageSpec(
        page=spec.page if page is None else page,
        kind=spec.kind if kind is None else kind,
        title=spec.title,
        report_keys=spec.report_keys,
        charts=spec.charts,
        slots=spec.slots,
        subtitle=spec.subtitle,
        is_appendix=spec.is_appendix,
    )


def _iter_report_entries(report_data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """依 report_data 的順序列出所有報表，供 PPT 動態補頁使用。"""
    entries: list[tuple[str, dict[str, Any]]] = []
    for bucket in ("reports", "family_reports"):
        section = report_data.get(bucket) or {}
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if isinstance(value, dict):
                entries.append((key, value))
    return entries


def _chart_candidates_for(report_key: str) -> tuple[str, ...]:
    """用 report_key 推定圖檔候選；沒有圖檔時 renderer 會顯示佔位。"""
    return (f"{report_key}.svg", f"{report_key}.png", f"{report_key}.jpg")


def _kind_for_report(report: dict[str, Any]) -> str:
    """依報表型態挑預設版型；矩陣與明細偏表格，其餘用圖文版。"""
    report_type = str(report.get("report_type") or "").lower()
    if report_type in {"matrix", "detail", "table"}:
        return "table"
    return "chart_with_narrative"


def _report_key_has_data(report_data: dict[str, Any], report_key: str) -> bool:
    """判斷 report_key 是否在本次報表版本中真的有資料。"""
    for bucket in ("reports", "family_reports"):
        entry = (report_data.get(bucket) or {}).get(report_key)
        if not isinstance(entry, dict):
            continue
        rows = entry.get("rows")
        if isinstance(rows, list) and rows:
            return True
        try:
            if int(entry.get("row_count") or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _actual_report_keys(report_data: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, ...]:
    """多 key 頁面只保留本次有資料的 key，避免空框與錯資料。"""
    return tuple(key for key in keys if _report_key_has_data(report_data, key))


def _page_should_render(report_data: dict[str, Any], spec: PageSpec) -> bool:
    """cover/direction 恆出；其他頁面至少需有一個實際 report_key。"""
    if spec.kind in {"cover", "direction"}:
        return True
    return bool(_actual_report_keys(report_data, spec.report_keys))


def _filter_spec_report_keys(report_data: dict[str, Any], spec: PageSpec) -> PageSpec:
    """套用選擇驅動規則：只把實際存在的 report_key 交給 renderer。"""
    if spec.kind in {"cover", "direction"}:
        return spec
    actual = _actual_report_keys(report_data, spec.report_keys)
    if actual == spec.report_keys:
        return spec
    return PageSpec(
        page=spec.page,
        kind=spec.kind,
        title=spec.title,
        report_keys=actual,
        charts=spec.charts,
        slots=spec.slots,
        subtitle=spec.subtitle,
        is_appendix=spec.is_appendix,
    )


def _expand_page_layout(report_data: dict[str, Any]) -> list[PageSpec]:
    """把未列在基礎大綱的報表插到附錄／結論前，頁碼重新連號。"""
    base_layout = [
        _filter_spec_report_keys(report_data, spec)
        for spec in PAGE_LAYOUT
        if _page_should_render(report_data, spec)
    ]
    covered = {key for spec in PAGE_LAYOUT for key in spec.report_keys}
    extra_pages: list[PageSpec] = []
    for report_key, report in _iter_report_entries(report_data):
        if report_key in covered:
            continue
        if not _report_key_has_data(report_data, report_key):
            continue
        title = str(report.get("label_zh") or report.get("label") or report_key)
        subtitle = str(report.get("label") or "")
        extra_pages.append(
            PageSpec(
                page=0,
                kind=_kind_for_report(report),
                title=title,
                report_keys=(report_key,),
                charts=_chart_candidates_for(report_key),
                subtitle=subtitle,
            )
        )

    if not extra_pages:
        return [_copy_page_spec(spec, page=index) for index, spec in enumerate(base_layout, start=1)]

    # 附錄錨點由顯式旗標決定，不使用 spec.page >= N 這類魔術數字。
    appendix_index = next(
        (index for index, spec in enumerate(base_layout) if spec.is_appendix),
        len(base_layout),
    )
    expanded = list(base_layout[:appendix_index]) + extra_pages + list(base_layout[appendix_index:])
    return [_copy_page_spec(spec, page=index) for index, spec in enumerate(expanded, start=1)]


def _clean_layout_overrides(value: Any) -> dict[str, str]:
    """只接受 renderer 支援的版型名稱，避免無效覆寫讓產檔失敗。"""
    if not isinstance(value, dict):
        return {}
    allowed = set(RENDERERS)
    cleaned: dict[str, str] = {}
    for page, kind in value.items():
        kind_text = str(kind)
        if kind_text in allowed:
            cleaned[str(page)] = kind_text
    return cleaned


def _apply_layout_overrides(layout: list[PageSpec], overrides: dict[str, str]) -> list[PageSpec]:
    """依頁碼套用使用者選的版型，不改動 report_key 與文案槽。"""
    return [
        _copy_page_spec(spec, kind=overrides[str(spec.page)])
        if str(spec.page) in overrides
        else spec
        for spec in layout
    ]


POSITION_FIELDS = ("left_in", "top_in", "width_in", "height_in")


def _clean_position_overrides(value: Any) -> dict[str, dict[str, float]]:
    """清理前端拖曳產生的英吋座標，保留可被 renderer 使用的數值欄位。"""
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, dict[str, float]] = {}
    for key, raw_box in value.items():
        if not isinstance(raw_box, dict):
            continue
        box: dict[str, float] = {}
        for field_name in POSITION_FIELDS:
            if field_name not in raw_box:
                continue
            try:
                box[field_name] = float(raw_box[field_name])
            except (TypeError, ValueError):
                continue
        if box:
            cleaned[str(key)] = box
    return cleaned


def _component_box(ctx: dict[str, Any], spec: PageSpec, component: str, defaults: dict[str, float]) -> dict[str, float]:
    """取得元件座標；先看 position_overrides，沒有才用 theme.json 預設。"""
    box = dict(defaults)
    override = (ctx.get("position_overrides") or {}).get(f"{spec.page}.{component}") or {}
    for field_name in POSITION_FIELDS:
        if field_name in override:
            box[field_name] = override[field_name]
    return box


def _position_overrides_for_page(ctx: dict[str, Any], spec: PageSpec) -> list[str]:
    """列出實際套到該頁的座標覆寫 key，寫入 manifest 供驗收追蹤。"""
    prefix = f"{spec.page}."
    return sorted(
        key for key in (ctx.get("position_overrides") or {}) if key.startswith(prefix)
    )


def all_slot_keys() -> list[str]:
    """回傳全部確認槽鍵，供產生過稿範本或檢查覆蓋率。"""
    keys: list[str] = []
    for spec in PAGE_LAYOUT:
        keys.extend(spec.slots)
    return keys


# --------------------------------------------------------------------------
# 樣式
# --------------------------------------------------------------------------
@dataclass
class Theme:
    """外觀樣式，抽自範例 PPT；改樣式只改 theme.json。"""

    font: dict[str, Any]
    color: dict[str, str]
    geometry: dict[str, Any]
    slide: dict[str, float]

    @classmethod
    def load(cls, path: Path = THEME_PATH) -> "Theme":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            font=data["font"],
            color=data["color"],
            geometry=data["geometry"],
            slide=data["slide"],
        )

    def rgb(self, name: str) -> RGBColor:
        return RGBColor.from_string(self.color[name])


# --------------------------------------------------------------------------
# SVG 轉點陣：python-pptx 不吃 SVG，需先轉 PNG；轉換結果做快取只轉一次
# --------------------------------------------------------------------------
def rasterize_svg(svg_path: Path, cache_dir: Path) -> Path | None:
    """把 SVG 轉成 PNG 供 python-pptx 插入，結果落快取避免重複轉換。

    優先用 PyMuPDF（主專案既有依賴，不需額外安裝）；不可用時回傳 None，
    由呼叫端以「圖檔缺漏」佔位處理，不擋整檔產出。
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(svg_path.read_bytes()).hexdigest()[:16]
    png_path = cache_dir / f"{svg_path.stem}_{digest}.png"
    if png_path.exists():
        return png_path

    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return None

    try:
        doc = pymupdf.open(str(svg_path))
        pix = doc[0].get_pixmap(dpi=150)
        pix.save(str(png_path))
        doc.close()
    except Exception:
        # 轉換失敗（SVG 語法過於進階等）不中斷產出。
        return None
    return png_path


class ImageResolver:
    """解析頁面配圖，並快取轉換結果（同一張圖只轉一次）。"""

    def __init__(self, report_dir: Path, cache_dir: Path) -> None:
        self.report_dir = report_dir
        self.cache_dir = cache_dir
        self._cache: dict[str, Path | None] = {}

    def resolve(self, candidates: tuple[str, ...]) -> Path | None:
        """依序取第一個存在的圖檔；SVG 轉 PNG，PNG／JPG 直接用。"""
        for name in candidates:
            if name in self._cache:
                if self._cache[name] is not None:
                    return self._cache[name]
                continue
            source = self.report_dir / name
            if not source.exists():
                self._cache[name] = None
                continue
            if source.suffix.lower() == ".svg":
                # 透過模組層名稱呼叫，讓測試可觀察轉換次數。
                resolved = globals()["rasterize_svg"](source, self.cache_dir)
            else:
                resolved = source
            self._cache[name] = resolved
            if resolved is not None:
                return resolved
        return None


# --------------------------------------------------------------------------
# 組版輔助
# --------------------------------------------------------------------------
def _add_text(
    slide,
    theme: Theme,
    text: str,
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    size: float,
    color: str = "ink",
    bold: bool = False,
    align=PP_ALIGN.LEFT,
) -> None:
    """加入一個文字框，套用主題字體與顏色。"""
    box = slide.shapes.add_textbox(
        Inches(left), Inches(top), Inches(width), Inches(height)
    )
    frame = box.text_frame
    frame.word_wrap = True
    para = frame.paragraphs[0]
    para.alignment = align
    run = para.add_run()
    run.text = text
    run.font.name = theme.font["family"]
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = theme.rgb(color)


def _add_band(slide, theme: Theme, left, top, width, height, color: str) -> None:
    """加入色帶／底板（範例採實心矩形分區）。"""
    from pptx.enum.shapes import MSO_SHAPE

    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = theme.rgb(color)
    shape.line.fill.background()
    shape.text_frame.text = ""


def _rows_of(report_data: dict, report_key: str) -> list[dict]:
    """取某報表的 rows；缺報表回空清單，讓頁面優雅降級不 crash。"""
    for bucket in ("reports", "family_reports"):
        section = report_data.get(bucket) or {}
        entry = section.get(report_key)
        if isinstance(entry, dict) and isinstance(entry.get("rows"), list):
            return entry["rows"]
    return []


def _label_of(report_data: dict, report_key: str, fallback: str) -> str:
    for bucket in ("reports", "family_reports"):
        entry = (report_data.get(bucket) or {}).get(report_key)
        if isinstance(entry, dict):
            return entry.get("label_zh") or entry.get("label") or fallback
    return fallback


def _narrative_of(narratives: dict, report_key: str) -> str:
    """取解讀文字（AI 草稿）；僅在無定稿槽時作為顯示備援。"""
    entry = (narratives.get("reports") or {}).get(report_key) or {}
    variants = entry.get("variants") or {}
    for key in ("default", *variants):
        if key in variants and variants[key].get("text"):
            return variants[key]["text"]
    return ""


def _year_value(row: dict[str, Any]) -> int | None:
    """從趨勢 row 取年份，支援既有 year 與 application_year 欄位。"""
    raw = row.get("year", row.get("application_year"))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 頁面組版：依 PageSpec.kind 分派
# --------------------------------------------------------------------------
def _render_cover(slide, theme, spec, ctx) -> None:
    """封面：品牌色底＋標題＋統計時間段＋關鍵統計卡。"""
    geo = theme.geometry["cover"]
    _add_band(slide, theme, geo["background_left_in"], geo["background_top_in"],
              theme.slide["width_in"], theme.slide["height_in"], "brand_dark")
    title = ctx["slots"].get("cover.title") or spec.title
    _add_text(
        slide, theme, title,
        left=geo["title_left_in"], top=geo["title_top_in"],
        width=geo["title_width_in"], height=geo["title_height_in"],
        size=theme.font["cover_title_pt"], color="on_dark", bold=True,
    )
    params = ctx["report_data"].get("parameters") or {}
    _add_text(
        slide, theme,
        f"統計時間段　報表版本 {params.get('version', ctx['version'])}",
        left=geo["period_left_in"], top=geo["period_top_in"],
        width=geo["period_width_in"], height=geo["period_height_in"],
        size=theme.font["stat_label_pt"], color="on_dark_soft",
    )

    # 統計卡：件數取自引擎 rows 的合計，不自行推算。
    stats: list[tuple[str, str, str]] = []
    trend_rows = _rows_of(ctx["report_data"], "application_trend")
    if trend_rows:
        total = sum(int(r.get("patent_count") or 0) for r in trend_rows)
        stats.append((str(total), "件", "專利總數"))
    country_rows = _rows_of(ctx["report_data"], "country_distribution")
    if country_rows:
        top = country_rows[:2]
        stats.append((
            " ｜ ".join(str(r.get("patent_count") or 0) for r in top),
            " ｜ ".join(str(r.get("country") or "-") for r in top),
            "地域分布(件數)",
        ))
    if trend_rows:
        years = sorted(y for row in trend_rows if (y := _year_value(row)) is not None)
        if years:
            year_range = str(years[0]) if years[0] == years[-1] else f"{years[0]}–{years[-1]}"
            stats.append((year_range, "年", "年份區間"))
    if not stats:
        stats.append(("—", "件", "資料待補"))

    for idx, (value, unit, label) in enumerate(stats[:4]):
        left = geo["stat_left_in"] + idx * geo["stat_gap_in"]
        _add_band(slide, theme, left, geo["stat_top_in"], geo["stat_width_in"], geo["stat_height_in"], "surface")
        _add_text(slide, theme, value, left=left, top=geo["stat_value_top_in"],
                  width=geo["stat_width_in"], height=geo["stat_value_height_in"],
                  size=theme.font["stat_value_pt"] if len(value) <= 4 else 24.0,
                  color="brand_dark", bold=True, align=PP_ALIGN.CENTER)
        _add_text(slide, theme, unit, left=left, top=geo["stat_unit_top_in"],
                  width=geo["stat_width_in"], height=geo["stat_unit_height_in"],
                  size=theme.font["stat_label_pt"], color="accent", bold=True, align=PP_ALIGN.CENTER)
        _add_text(slide, theme, label, left=left + geo["stat_label_inset_in"], top=geo["stat_label_top_in"],
                  width=geo["stat_width_in"] - geo["stat_label_inset_in"] * 2, height=geo["stat_label_height_in"],
                  size=theme.font["stat_label_pt"], color="ink", align=PP_ALIGN.CENTER)


def _render_header(slide, theme, spec, ctx) -> None:
    """內頁共用頁首：標題、副標、右上頁碼。"""
    geo = theme.geometry
    hdr = geo["header"]
    _add_text(slide, theme, spec.title,
              left=geo["margin_in"], top=geo["title_top_in"],
              width=hdr["title_width_in"], height=hdr["title_height_in"],
              size=theme.font["title_pt"], color="ink", bold=True)
    if spec.subtitle:
        _add_text(slide, theme, spec.subtitle,
                  left=geo["margin_in"], top=geo["subtitle_top_in"],
                  width=hdr["subtitle_width_in"], height=hdr["subtitle_height_in"],
                  size=theme.font["subtitle_pt"], color="ink")
    _add_text(slide, theme, f"{spec.page:02d}",
              left=geo["page_number_left_in"], top=geo["title_top_in"],
              width=hdr["page_number_width_in"], height=hdr["page_number_height_in"],
              size=theme.font["page_number_pt"], color="accent", bold=True)


def _render_chart_with_narrative(slide, theme, spec, ctx) -> None:
    """圖＋解讀：左圖右文；圖缺漏時以佔位文字說明，不 crash。"""
    _render_header(slide, theme, spec, ctx)
    image = ctx["images"].resolve(spec.charts)
    top = theme.geometry["body_top_in"]
    g = theme.geometry["chart_with_narrative"]
    if image is not None:
        slide.shapes.add_picture(str(image), Inches(g["image_left_in"]), Inches(top),
                                 width=Inches(g["image_width_in"]))
    else:
        _add_band(slide, theme, g["placeholder_band_left_in"], top,
                  g["placeholder_band_width_in"], g["placeholder_band_height_in"], "accent_soft")
        _add_text(slide, theme, "（圖檔待產出）",
                  left=g["placeholder_text_left_in"], top=top + g["placeholder_text_top_offset_in"],
                  width=g["placeholder_text_width_in"], height=g["placeholder_text_height_in"],
                  size=theme.font["body_pt"], color="ink", align=PP_ALIGN.CENTER)

    text = _first_slot_text(spec, ctx) or _narrative_of(ctx["narratives"], spec.report_keys[0] if spec.report_keys else "")
    _add_band(slide, theme, g["text_band_left_in"], top,
              g["text_band_width_in"], g["text_band_height_in"], "accent_soft")
    _add_text(slide, theme, text or "（解讀尚未產生）",
              left=g["text_left_in"], top=top + g["text_top_offset_in"],
              width=g["text_width_in"], height=g["text_height_in"],
              size=theme.font["body_pt"], color="ink")


def _render_direction(slide, theme, spec, ctx) -> None:
    """研發方向建議：三欄表頭＋定稿建議內容（全文由使用者確認）。"""
    _render_header(slide, theme, spec, ctx)
    g = theme.geometry["direction"]
    top = g["header_band_top_in"]
    _add_band(slide, theme, g["header_band_left_in"], top,
              g["header_band_width_in"], g["header_band_height_in"], "brand_deep")
    for column in g["columns"]:
        _add_text(slide, theme, column["label"],
                  left=column["left_in"], top=top + g["header_label_top_offset_in"],
                  width=column["width_in"], height=g["header_label_height_in"],
                  size=theme.font["table_header_pt"], color="on_dark", bold=True)
    body = ctx["slots"].get("direction.body") or "（研發方向建議尚未產生）"
    _add_band(slide, theme, g["body_band_left_in"], g["body_band_top_in"],
              g["body_band_width_in"], g["body_band_height_in"], "accent_soft")
    _add_text(slide, theme, body,
              left=g["body_text_left_in"], top=g["body_text_top_in"],
              width=g["body_text_width_in"], height=g["body_text_height_in"],
              size=theme.font["body_pt"], color="ink")


def _render_table(slide, theme, spec, ctx) -> None:
    """純表格頁：直接列引擎 rows，不加解讀。"""
    _render_header(slide, theme, spec, ctx)
    rows = []
    for key in spec.report_keys:
        rows = _rows_of(ctx["report_data"], key)
        if rows:
            break
    g = theme.geometry["table"]
    box = _component_box(
        ctx,
        spec,
        "table",
        {
            "left_in": g["left_in"],
            "top_in": theme.geometry["body_top_in"],
            "width_in": g["width_in"],
            "height_in": g["height_in"],
        },
    )
    _add_table(
        slide,
        theme,
        rows,
        left=box["left_in"],
        top=box["top_in"],
        height=box["height_in"],
        width=box["width_in"],
    )


def _render_table_with_narrative(slide, theme, spec, ctx) -> None:
    """左表（專利側，引擎數據）＋右文（主要權人／申請人摘要）。"""
    _render_header(slide, theme, spec, ctx)
    top = theme.geometry["body_top_in"]
    rows = []
    for key in spec.report_keys:
        rows = _rows_of(ctx["report_data"], key)
        if rows:
            break
    g = theme.geometry["table_with_narrative"]
    _add_table(slide, theme, rows, top=top, height=g["table_height_in"], width=g["table_width_in"])
    _add_band(slide, theme, g["text_band_left_in"], top,
              g["text_band_width_in"], g["text_band_height_in"], "accent_soft")
    _add_text(slide, theme, _first_slot_text(spec, ctx) or "（主要權人與申請人摘要尚未產生）",
              left=g["text_left_in"], top=top + g["text_top_offset_in"],
              width=g["text_width_in"], height=g["text_height_in"],
              size=theme.font["body_pt"], color="ink")


def _render_narrative_only(slide, theme, spec, ctx) -> None:
    """純敘述頁：逐槽分區呈現定稿文案；目前保留供相容動態頁使用。"""
    _render_header(slide, theme, spec, ctx)
    top = theme.geometry["body_top_in"]
    g = theme.geometry["narrative_only"]
    for slot in spec.slots:
        _add_band(slide, theme, g["band_left_in"], top, g["band_width_in"], g["band_height_in"], "accent_soft")
        _add_text(slide, theme, slot,
                  left=g["slot_title_left_in"], top=top + g["slot_title_top_offset_in"],
                  width=g["slot_title_width_in"], height=g["slot_title_height_in"],
                  size=theme.font["table_header_pt"], color="brand_dark", bold=True)
        _add_text(slide, theme, ctx["slots"].get(slot) or "（內容尚未產生）",
                  left=g["slot_text_left_in"], top=top + g["slot_text_top_offset_in"],
                  width=g["slot_text_width_in"], height=g["slot_text_height_in"],
                  size=theme.font["body_pt"], color="ink")
        top += g["block_step_in"]


def _add_table(slide, theme, rows: list[dict], *, top: float, height: float, width: float | None = None, left: float | None = None) -> None:
    """把引擎 rows 畫成表格；無資料時顯示佔位，不 crash。

    `width` 未指定時取 theme.geometry.table.width_in（整頁寬表格的預設值）。
    """
    g = theme.geometry["table"]
    if width is None:
        width = g["width_in"]
    if left is None:
        left = g["left_in"]
    if not rows:
        _add_band(slide, theme, g["left_in"], top, width, height, "accent_soft")
        _add_text(slide, theme, "（本頁資料待產出）",
                  left=g["placeholder_text_left_in"],
                  top=top + height / 2 - g["placeholder_text_top_shift_in"],
                  width=width - g["placeholder_text_inset_in"],
                  height=g["placeholder_text_height_in"], size=theme.font["body_pt"],
                  color="ink", align=PP_ALIGN.CENTER)
        return

    columns = list(rows[0].keys())[:6]
    display = rows[:12]
    table = slide.shapes.add_table(
        len(display) + 1, len(columns), Inches(left), Inches(top), Inches(width), Inches(height)
    ).table
    for c, name in enumerate(columns):
        cell = table.cell(0, c)
        cell.text = str(name)
        _style_cell(cell, theme, size=theme.font["table_header_pt"], color="on_dark", bold=True, fill="brand_deep")
    for r, row in enumerate(display, start=1):
        for c, name in enumerate(columns):
            cell = table.cell(r, c)
            cell.text = "" if row.get(name) is None else str(row.get(name))
            _style_cell(cell, theme, size=theme.font["body_pt"] - 2, color="ink",
                        fill="surface" if r % 2 else "accent_soft")


def _style_cell(cell, theme: Theme, *, size: float, color: str, bold: bool = False, fill: str = "surface") -> None:
    cell.fill.solid()
    cell.fill.fore_color.rgb = theme.rgb(fill)
    for para in cell.text_frame.paragraphs:
        for run in para.runs:
            run.font.name = theme.font["family"]
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.color.rgb = theme.rgb(color)


def _first_slot_text(spec: PageSpec, ctx: dict) -> str:
    for slot in spec.slots:
        if ctx["slots"].get(slot):
            return ctx["slots"][slot]
    return ""


def _add_watermark(slide, theme: Theme) -> None:
    """舊版相容函式；v2.3 起缺漏不印進 PPT，呼叫端不再使用。"""
    geo = theme.geometry["watermark"]
    _add_text(
        slide, theme, WATERMARK_TEXT,
        left=geo["left_in"], top=geo["top_in"], width=geo["width_in"], height=geo["height_in"],
        size=theme.font["watermark_pt"], color="watermark", bold=True, align=PP_ALIGN.RIGHT,
    )


RENDERERS = {
    "cover": _render_cover,
    "direction": _render_direction,
    "chart_with_narrative": _render_chart_with_narrative,
    "table": _render_table,
    "table_with_narrative": _render_table_with_narrative,
    "narrative_only": _render_narrative_only,
}


# --------------------------------------------------------------------------
# 版本不覆蓋：同版本重跑產生帶序號新檔
# --------------------------------------------------------------------------
def _next_available_path(output_dir: Path, version: str) -> Path:
    """回傳未被占用的輸出路徑；既有版本一律保留不覆蓋。"""
    base = output_dir / f"{version}.pptx"
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = output_dir / f"{version}_r{index}.pptx"
        if not candidate.exists():
            return candidate
        index += 1


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def build_ppt(
    *,
    report_dir: Path | str,
    approvals_path: Path | str | None = None,
    output_dir: Path | str | None = None,
    theme_path: Path | str = THEME_PATH,
) -> dict[str, Any]:
    """依版型對照表組出報告 PPTX，回傳輸出路徑與 manifest 路徑。

    缺確認槽或缺報表只寫入 manifest；輸出不覆蓋既有版本。
    """
    report_dir = Path(report_dir)
    report_data = _load_json(report_dir / "report_data.json", {})
    narratives = _load_json(report_dir / "narratives.json", {})
    approvals = _load_json(Path(approvals_path), {}) if approvals_path else {}
    slots: dict[str, str] = approvals.get("slots") or {}
    layout_overrides = _clean_layout_overrides(approvals.get("layout_overrides"))
    position_overrides = _clean_position_overrides(approvals.get("position_overrides"))

    version = (
        (report_data.get("parameters") or {}).get("version")
        or approvals.get("report_version")
        or report_dir.name
    )
    output_dir = Path(output_dir) if output_dir else Path("data/report_artifacts/ppt")
    output_dir.mkdir(parents=True, exist_ok=True)

    theme = Theme.load(Path(theme_path))
    ctx = {
        "report_data": report_data,
        "narratives": narratives,
        "slots": slots,
        "position_overrides": position_overrides,
        "version": version,
        "images": ImageResolver(report_dir, output_dir / ".cache"),
    }

    prs = Presentation()
    prs.slide_width = Inches(theme.slide["width_in"])
    prs.slide_height = Inches(theme.slide["height_in"])
    blank = prs.slide_layouts[6]

    layout = _apply_layout_overrides(_expand_page_layout(report_data), layout_overrides)

    pages: list[dict[str, Any]] = []
    for spec in layout:
        slide = prs.slides.add_slide(blank)
        RENDERERS[spec.kind](slide, theme, spec, ctx)

        filled = [s for s in spec.slots if slots.get(s)]
        missing = [s for s in spec.slots if not slots.get(s)]
        missing_reports = [
            key for key in spec.report_keys if not _report_key_has_data(report_data, key)
        ]
        pages.append({
            "page": spec.page,
            "kind": spec.kind,
            "title": spec.title,
            "report_keys": list(spec.report_keys),
            "is_appendix": spec.is_appendix,
            "filled_slots": filled,
            "missing_slots": missing,
            "missing_reports": missing_reports,
            "position_overrides_applied": _position_overrides_for_page(ctx, spec),
            "watermarked": False,
        })

    pptx_path = _next_available_path(output_dir, version)
    prs.save(str(pptx_path))

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_report_version": version,
        "source_report_dir": str(report_dir),
        "pptx_file": pptx_path.name,
        "sha256": _sha256_of(pptx_path),
        "slot_total": len(all_slot_keys()),
        "slot_filled": sum(len(p["filled_slots"]) for p in pages),
        "missing_slot_total": sum(len(p["missing_slots"]) for p in pages),
        "missing_report_total": sum(len(p["missing_reports"]) for p in pages),
        "metadata": {
            key: (report_data.get("parameters") or {}).get(key)
            for key in ("topic_run_id", "topic_state_version")
            if (report_data.get("parameters") or {}).get(key) is not None
        },
        "layout_overrides": layout_overrides,
        "position_override_total": len(position_overrides),
        "pages": pages,
    }
    manifest_path = pptx_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "pptx_path": str(pptx_path),
        "manifest_path": str(manifest_path),
        "manifest": manifest,
    }


def write_approval_template(path: Path) -> Path:
    """產出確認槽範本，供使用者填入定稿文案。"""
    payload = {
        "report_version": "<報表版本>",
        "slots": {slot: "" for slot in all_slot_keys()},
        "layout_overrides": {},
        "position_overrides": {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="專利分析報告 PPTX 產生器（deterministic）")
    parser.add_argument("--report-dir", required=False, help="報表版本目錄（含 report_data.json）")
    parser.add_argument("--approvals", help="確認槽定稿文案 JSON")
    parser.add_argument("--output-dir", default="data/report_artifacts/ppt", help="輸出目錄")
    parser.add_argument("--init-approvals", help="產出確認槽範本到指定路徑後結束")
    args = parser.parse_args()

    if args.init_approvals:
        path = write_approval_template(Path(args.init_approvals))
        print(f"approval template: {path}")
        return

    if not args.report_dir:
        parser.error("--report-dir is required unless --init-approvals is used")

    result = build_ppt(
        report_dir=args.report_dir,
        approvals_path=args.approvals,
        output_dir=args.output_dir,
    )
    manifest = result["manifest"]
    print(f"pptx: {result['pptx_path']}")
    print(f"manifest: {result['manifest_path']}")
    print(f"sha256: {manifest['sha256']}")
    print(f"slots filled: {manifest['slot_filled']}/{manifest['slot_total']}")
    pending = [str(p["page"]) for p in manifest["pages"] if p["watermarked"]]
    if pending:
        print(f"待確認頁: {', '.join(pending)}")


if __name__ == "__main__":
    main()
