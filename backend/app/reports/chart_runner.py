from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from backend.app.reports.report_definitions import REPORT_DEFINITIONS
from backend.app.reports.report_engine import parse_json_arg, run_report


def _app_layer_connect():
    import psycopg
    from psycopg.rows import dict_row

    from backend.app.db.connection import get_connection_kwargs

    return psycopg.connect(**get_connection_kwargs(), row_factory=dict_row)


def fetch_analysis_patent_ids(analysis_id: int) -> list[int]:
    """Return the patent_id snapshot for an analysis, or raise if it is missing."""
    with _app_layer_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT selected_patent_ids_json FROM app_layer.analysis_runs WHERE analysis_id = %s",
                (analysis_id,),
            )
            row = cur.fetchone()
    if row is None:
        raise ValueError(f"analysis_id {analysis_id} not found")
    return list(row["selected_patent_ids_json"] or [])


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_type_for(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith((".html", ".htm")):
        return "report_html"
    if lower.endswith(".svg"):
        return "chart_svg"
    if lower.endswith(".json"):
        return "report_data"
    return "file"


def record_exports(analysis_id: int, run_dir: Path, files: list[str], parameters: dict[str, Any]) -> int:
    """Write one app_layer.export_runs row per produced file (path + sha256)."""
    from psycopg.types.json import Jsonb

    inserted = 0
    with _app_layer_connect() as conn:
        with conn.cursor() as cur:
            for filename in files:
                file_path = run_dir / filename
                if not file_path.exists():
                    continue
                cur.execute(
                    """
                    INSERT INTO app_layer.export_runs
                        (analysis_id, export_type, file_path, file_hash, parameters_json)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        analysis_id,
                        export_type_for(filename),
                        str(file_path),
                        sha256_file(file_path),
                        Jsonb(parameters),
                    ),
                )
                inserted += 1
        conn.commit()
    return inserted


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"

COLOR_APPLICATION = "#2563EB"
COLOR_PUBLICATION = "#DC2626"
COLOR_BAR = "#0F766E"
COLOR_BAR_ALT = "#64748B"
COLOR_MAP = "#F8FAFC"
COLOR_GRID = "#CBD5E1"
COLOR_TEXT = "#111827"


def xml_text(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def scale(value: float, old_min: float, old_max: float, new_min: float, new_max: float) -> float:
    if old_max == old_min:
        return (new_min + new_max) / 2
    return new_min + (value - old_min) * (new_max - new_min) / (old_max - old_min)


def render_line_chart(
    path: Path,
    title: str,
    application_rows: list[dict[str, Any]],
    publication_rows: list[dict[str, Any]],
) -> None:
    app = {int(row["application_year"]): int(row["patent_count"]) for row in application_rows}
    pub = {int(row["publication_year"]): int(row["patent_count"]) for row in publication_rows if row.get("publication_year") is not None}
    years = sorted(set(app) | set(pub))
    max_count = max([*app.values(), *pub.values(), 1])
    width, height = 980, 560
    left, right, top, bottom = 76, 34, 64, 72
    plot_w = width - left - right
    plot_h = height - top - bottom

    def points(series: dict[int, int]) -> str:
        return " ".join(
            f"{scale(year, years[0], years[-1], left, left + plot_w):.1f},{scale(series.get(year, 0), 0, max_count, top + plot_h, top):.1f}"
            for year in years
        )

    y_ticks = [round(max_count * i / 4) for i in range(5)]
    x_labels = years if len(years) <= 12 else years[:: max(1, math.ceil(len(years) / 10))]
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{left}" y="34" font-size="24" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
        f'<text x="{left}" y="54" font-size="13" fill="#6B7280">'
        f'{"Application year and publication year comparison" if pub else "Yearly count"}</text>',
    ]
    for tick in y_ticks:
        y = scale(tick, 0, max_count, top + plot_h, top)
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="{COLOR_GRID}" stroke-width="1"/>')
        svg.append(f'<text x="{left - 12}" y="{y + 4:.1f}" text-anchor="end" font-size="12" fill="#6B7280">{tick}</text>')
    svg.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#111827"/>')
    svg.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#111827"/>')
    for year in x_labels:
        x = scale(year, years[0], years[-1], left, left + plot_w)
        svg.append(f'<text x="{x:.1f}" y="{top + plot_h + 26}" text-anchor="middle" font-size="12" fill="#6B7280">{year}</text>')
    svg.append(f'<polyline points="{points(app)}" fill="none" stroke="{COLOR_APPLICATION}" stroke-width="3"/>')
    if pub:
        # 只有真的有公告序列才畫第二條線，避免單序列時出現一條 0 的假線。
        svg.append(f'<polyline points="{points(pub)}" fill="none" stroke="{COLOR_PUBLICATION}" stroke-width="3"/>')
    for year in years:
        x = scale(year, years[0], years[-1], left, left + plot_w)
        svg.append(f'<circle cx="{x:.1f}" cy="{scale(app.get(year, 0), 0, max_count, top + plot_h, top):.1f}" r="3.5" fill="{COLOR_APPLICATION}"/>')
        if pub:
            svg.append(f'<circle cx="{x:.1f}" cy="{scale(pub.get(year, 0), 0, max_count, top + plot_h, top):.1f}" r="3.5" fill="{COLOR_PUBLICATION}"/>')
    svg.append(f'<rect x="{left + 10}" y="{top + 8}" width="12" height="12" fill="{COLOR_APPLICATION}"/><text x="{left + 28}" y="{top + 19}" font-size="13" fill="{COLOR_TEXT}">Application Year</text>')
    if pub:
        svg.append(f'<rect x="{left + 148}" y="{top + 8}" width="12" height="12" fill="{COLOR_PUBLICATION}"/><text x="{left + 166}" y="{top + 19}" font-size="13" fill="{COLOR_TEXT}">Publication Year</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def render_bar_chart(path: Path, title: str, rows: list[dict[str, Any]], label_key: str, value_key: str = "patent_count", limit: int = 20) -> None:
    data = rows[:limit]
    width = 980
    row_h = 30
    top = 68
    left = 310
    right = 40
    bottom = 34
    height = top + bottom + max(1, len(data)) * row_h
    plot_w = width - left - right
    max_value = max([int(row[value_key]) for row in data] + [1])
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="28" y="36" font-size="24" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
    ]
    for index, row in enumerate(data):
        y = top + index * row_h
        label = xml_text(row.get(label_key))
        value = int(row[value_key])
        bar_w = scale(value, 0, max_value, 0, plot_w)
        color = COLOR_BAR if index % 2 == 0 else COLOR_BAR_ALT
        svg.append(f'<text x="{left - 12}" y="{y + 20}" text-anchor="end" font-size="13" fill="{COLOR_TEXT}">{label[:42]}</text>')
        svg.append(f'<rect x="{left}" y="{y + 6}" width="{bar_w:.1f}" height="18" rx="2" fill="{color}"/>')
        svg.append(f'<text x="{left + bar_w + 8:.1f}" y="{y + 20}" font-size="13" fill="{COLOR_TEXT}">{value}</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def classification_level_key(value: Any, level: int) -> str:
    """Collapse an IPC/CPC symbol to the requested classification hierarchy level.

    Levels follow the IPC/CPC structure, not raw character count:
      level 4 -> subclass,  e.g. "A01D-034/416" -> "A01D"
      level 5 -> main group, e.g. IPC "A01D-034/416" -> "A01D-034"
                                  CPC "A01D-0034/416" -> "A01D-0034"

    Level 5 keeps the source main-group formatting (the part before the
    subgroup separator "/"), so IPC 3-digit groups and CPC 4-digit groups
    are both preserved without being truncated.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if level >= 5:
        # main group = everything before the subgroup separator "/"
        return text.split("/", 1)[0].strip()
    # subclass and shallower = section + class + subclass letters (first N alnum chars)
    normalized = "".join(char for char in text if char.isalnum())
    return normalized[:level]


def collapse_classification_rows(rows: list[dict[str, Any]], source_key: str, level: int) -> list[dict[str, Any]]:
    grouped: dict[str, int] = {}
    for row in rows:
        key = classification_level_key(row.get(source_key), level)
        if not key:
            continue
        grouped[key] = grouped.get(key, 0) + int(row["patent_count"])
    return [
        {source_key: key, "patent_count": count}
        for key, count in sorted(grouped.items(), key=lambda item: (-item[1], item[0]))
    ]


COUNTRY_CENTROIDS = {
    "US": (-98, 39),
    "CN": (104, 35),
    "JP": (138, 37),
    "KR": (128, 36),
    "TW": (121, 24),
    "EP": (10, 50),
    "DE": (10, 51),
    "FR": (2, 47),
    "GB": (-2, 54),
    "CA": (-106, 56),
    "AU": (134, -25),
    "IN": (78, 22),
}


def render_country_map(path: Path, rows: list[dict[str, Any]], title: str = "Patent Jurisdiction Distribution") -> None:
    # 區域專利局（EP 等）畫橘色泡泡標在轄區位置；WO/IB 無地域，落下方註記。
    from backend.app.reports.map_runner import (
        NON_COUNTRY_AUTHORITIES,
        REGIONAL_AUTHORITY_CENTROIDS,
        REGIONAL_AUTHORITY_NAMES,
    )

    width, height = 980, 540
    left, top = 50, 70
    map_w, map_h = 880, 390
    max_value = max([int(row["patent_count"]) for row in rows] + [1])
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="50" y="36" font-size="24" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
        f'<rect x="{left}" y="{top}" width="{map_w}" height="{map_h}" fill="{COLOR_MAP}" stroke="#94A3B8"/>',
    ]
    for lon in range(-180, 181, 60):
        x = scale(lon, -180, 180, left, left + map_w)
        svg.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + map_h}" stroke="{COLOR_GRID}" stroke-width="1"/>')
    for lat in range(-60, 61, 30):
        y = scale(lat, 85, -85, top, top + map_h)
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + map_w}" y2="{y:.1f}" stroke="{COLOR_GRID}" stroke-width="1"/>')
    no_geo_notes: list[str] = []
    for row in rows:
        code = str(row["country_code"])
        value = int(row["patent_count"])
        # 區域局身分以 NON_COUNTRY_AUTHORITIES 為準（本檔的 COUNTRY_CENTROIDS 歷史上混了 EP 座標，不可拿來判斷）。
        is_regional = code in NON_COUNTRY_AUTHORITIES
        if is_regional:
            centroid = REGIONAL_AUTHORITY_CENTROIDS.get(code)
        else:
            centroid = COUNTRY_CENTROIDS.get(code)
        if centroid is None:
            # 無地域代碼（WO/IB＝PCT）畫不上地圖，收進下方註記。
            no_geo_notes.append(f"{code}（{REGIONAL_AUTHORITY_NAMES.get(code, code)}）{value} 件")
            continue
        lon, lat = centroid
        x = scale(lon, -180, 180, left, left + map_w)
        y = scale(lat, 85, -85, top, top + map_h)
        radius = 8 + 34 * math.sqrt(value / max_value)
        # 區域局用橘色，與國家（藍色）視覺區分：代表「這個地區有佈局」而非單一國家。
        fill, stroke = ("#F59E0B", "#92400E") if is_regional else ("#2563EB", "#1E40AF")
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{fill}" fill-opacity="0.68" stroke="{stroke}" stroke-width="2"/>')
        svg.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle" font-size="13" fill="white" font-weight="700">{xml_text(code)}</text>')
        svg.append(f'<text x="{x:.1f}" y="{y + radius + 18:.1f}" text-anchor="middle" font-size="13" fill="{COLOR_TEXT}">{value}</text>')
    footnote = "Bubble view: circle area is proportional to patent count. 橘色＝區域專利局（標轄區位置）。"
    if no_geo_notes:
        footnote += " 無地域代碼：" + "、".join(no_geo_notes)
    svg.append(f'<text x="50" y="505" font-size="12" fill="#6B7280">{xml_text(footnote)}</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def compute_yoy_growth(rows: list[dict[str, Any]], year_key: str = "application_year", value_key: str = "patent_count") -> list[dict[str, Any]]:
    """由年度件數序列計算年增率（%）。

    只對「連續兩年且前一年 > 0」的年份產生增率點，年份斷檔或前年為 0 不硬算。
    """
    series = {int(r[year_key]): int(r[value_key]) for r in rows if r.get(year_key) is not None}
    years = sorted(series)
    growth: list[dict[str, Any]] = []
    for prev, cur in zip(years, years[1:]):
        if cur - prev == 1 and series[prev] > 0:
            growth.append({"year": cur, "growth_pct": round((series[cur] - series[prev]) / series[prev] * 100, 1)})
    return growth


def render_growth_chart(path: Path, title: str, growth_rows: list[dict[str, Any]]) -> None:
    """年增率折線圖：允許負值，畫 0% 基準線。"""
    width, height = 980, 560
    left, right, top, bottom = 76, 34, 64, 72
    plot_w, plot_h = width - left - right, height - top - bottom
    years = [int(r["year"]) for r in growth_rows]
    values = [float(r["growth_pct"]) for r in growth_rows]
    if not years:
        years, values = [0], [0.0]
    v_min, v_max = min(values + [0.0]), max(values + [0.0])
    pad = max((v_max - v_min) * 0.1, 5.0)
    v_min, v_max = v_min - pad, v_max + pad
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{left}" y="34" font-size="24" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
        f'<text x="{left}" y="54" font-size="13" fill="#6B7280">YoY growth (%), consecutive years only</text>',
    ]
    for i in range(5):
        tick = v_min + (v_max - v_min) * i / 4
        y = scale(tick, v_min, v_max, top + plot_h, top)
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="{COLOR_GRID}" stroke-width="1"/>')
        svg.append(f'<text x="{left - 12}" y="{y + 4:.1f}" text-anchor="end" font-size="12" fill="#6B7280">{tick:.0f}%</text>')
    zero_y = scale(0, v_min, v_max, top + plot_h, top)
    svg.append(f'<line x1="{left}" y1="{zero_y:.1f}" x2="{left + plot_w}" y2="{zero_y:.1f}" stroke="#111827" stroke-width="1.5"/>')
    x_labels = years if len(years) <= 12 else years[:: max(1, math.ceil(len(years) / 10))]
    for year in x_labels:
        x = scale(year, years[0], years[-1], left, left + plot_w)
        svg.append(f'<text x="{x:.1f}" y="{top + plot_h + 26}" text-anchor="middle" font-size="12" fill="#6B7280">{year}</text>')
    points = " ".join(
        f"{scale(y, years[0], years[-1], left, left + plot_w):.1f},{scale(v, v_min, v_max, top + plot_h, top):.1f}"
        for y, v in zip(years, values)
    )
    svg.append(f'<polyline points="{points}" fill="none" stroke="{COLOR_APPLICATION}" stroke-width="3"/>')
    for y, v in zip(years, values):
        svg.append(f'<circle cx="{scale(y, years[0], years[-1], left, left + plot_w):.1f}" cy="{scale(v, v_min, v_max, top + plot_h, top):.1f}" r="3.5" fill="{COLOR_APPLICATION}"/>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def render_bubble_chart(
    path: Path,
    title: str,
    rows: list[dict[str, Any]],
    x_key: str,
    y_key: str,
    size_key: str,
    label_key: str,
) -> None:
    """氣泡圖：X/Y 線性軸、泡泡面積正比 size_key（企業研發能量用）。"""
    width, height = 980, 620
    left, right, top, bottom = 90, 40, 64, 84
    plot_w, plot_h = width - left - right, height - top - bottom
    xs = [float(r[x_key]) for r in rows] or [0.0]
    ys = [float(r[y_key]) for r in rows] or [0.0]
    sizes = [float(r[size_key]) for r in rows] or [1.0]
    x_max, y_max, s_max = max(xs + [1.0]) * 1.1, max(ys + [1.0]) * 1.15, max(sizes + [1.0])
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{left}" y="34" font-size="24" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
        f'<text x="{left}" y="54" font-size="13" fill="#6B7280">X = total forward citations, Y = patents, bubble = inventors</text>',
    ]
    for i in range(5):
        y_tick = y_max * i / 4
        y = scale(y_tick, 0, y_max, top + plot_h, top)
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="{COLOR_GRID}" stroke-width="1"/>')
        svg.append(f'<text x="{left - 12}" y="{y + 4:.1f}" text-anchor="end" font-size="12" fill="#6B7280">{y_tick:.0f}</text>')
        x_tick = x_max * i / 4
        x = scale(x_tick, 0, x_max, left, left + plot_w)
        svg.append(f'<text x="{x:.1f}" y="{top + plot_h + 26}" text-anchor="middle" font-size="12" fill="#6B7280">{x_tick:.0f}</text>')
    svg.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#111827"/>')
    svg.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#111827"/>')
    svg.append(f'<text x="{left + plot_w / 2:.0f}" y="{height - 20}" text-anchor="middle" font-size="13" fill="{COLOR_TEXT}">被引用總數（下載時點快照）</text>')
    # 泡泡由大到小畫，避免大泡蓋掉小泡的標籤。
    ordered = sorted(rows, key=lambda r: -float(r[size_key]))
    for row in ordered:
        x = scale(float(row[x_key]), 0, x_max, left, left + plot_w)
        y = scale(float(row[y_key]), 0, y_max, top + plot_h, top)
        radius = 6 + 30 * math.sqrt(float(row[size_key]) / s_max)
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="#2563EB" fill-opacity="0.45" stroke="#1E40AF" stroke-width="1.5"/>')

    # 標籤：全部泡泡都標。預設放泡泡正上方；重疊時上下交錯逐步外推找空位，
    # 標籤離開泡泡邊緣時畫一條引線（箭頭）指回泡泡，群聚也能對得上誰是誰。
    placed: list[tuple[float, float, float]] = []  # (x_center, y_baseline, half_width)
    for row in ordered:
        x = scale(float(row[x_key]), 0, x_max, left, left + plot_w)
        y = scale(float(row[y_key]), 0, y_max, top + plot_h, top)
        radius = 6 + 30 * math.sqrt(float(row[size_key]) / s_max)
        label = str(row[label_key])[:22]
        half_w = len(label) * 3.3  # 11px 字約 6.6px 寬的一半估值
        default_y = y - radius - 5
        # 候選位置：上方原位 → 下方 → 更上 → 更下……交錯外推，最多 12 檔。
        candidate_ys = [default_y]
        for i in range(1, 12):
            step = 13 * ((i + 1) // 2)
            candidate_ys.append(y + radius + 12 + step if i % 2 else default_y - step)
        label_y = candidate_ys[-1]
        for cy in candidate_ys:
            if all(abs(cy - py) > 12 or abs(x - px) > (half_w + pw) for px, py, pw in placed):
                label_y = cy
                break
        placed.append((x, label_y, half_w))
        # 標籤不在預設位（被推開）→ 畫引線從標籤指回泡泡邊緣。
        if abs(label_y - default_y) > 6:
            if label_y < y:  # 標籤在泡泡上方
                line_y1, line_y2 = label_y + 3, y - radius
            else:            # 標籤在泡泡下方
                line_y1, line_y2 = label_y - 10, y + radius
            svg.append(f'<line x1="{x:.1f}" y1="{line_y1:.1f}" x2="{x:.1f}" y2="{line_y2:.1f}" stroke="#94A3B8" stroke-width="1"/>')
        svg.append(f'<text x="{x:.1f}" y="{label_y:.1f}" text-anchor="middle" font-size="11" fill="{COLOR_TEXT}">{xml_text(label)}</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def render_matrix_chart(
    path: Path,
    title: str,
    rows: list[dict[str, Any]],
    row_key: str,
    col_key: str,
    value_key: str = "patent_count",
    row_limit: int = 20,
) -> dict[str, Any]:
    """二維交叉矩陣（如 公司×國家）：一列＝一個 row_key 值，儲存格＝該列×該欄的量。

    每列彼此獨立、不跨列混算；列取總量前 row_limit 大、欄按總量排序。
    儲存格顯示件數、藍階深淺＝相對量級（以最大格為基準開根號縮放，避免
    極大值把其他格全壓成白色）。回傳實際入圖的列數/欄序供 note 使用。
    """
    # 列/欄總量：排序與 top-N 依據（只用來排序，不畫加總值——不混算）。
    row_totals: dict[str, int] = {}
    col_totals: dict[str, int] = {}
    cells: dict[tuple[str, str], int] = {}
    for row in rows:
        row_label = str(row.get(row_key) or "")
        col_label = str(row.get(col_key) or "")
        value = int(row.get(value_key) or 0)
        if not row_label or not col_label:
            continue
        cells[(row_label, col_label)] = cells.get((row_label, col_label), 0) + value
        row_totals[row_label] = row_totals.get(row_label, 0) + value
        col_totals[col_label] = col_totals.get(col_label, 0) + value

    top_rows = [name for name, _ in sorted(row_totals.items(), key=lambda kv: (-kv[1], kv[0]))[:row_limit]]
    # 欄只留 top rows 實際出現過的，按整體總量排序。
    used_cols = {col for (row_label, col) in cells if row_label in set(top_rows)}
    cols = [name for name, _ in sorted(col_totals.items(), key=lambda kv: (-kv[1], kv[0])) if name in used_cols]

    label_width, cell_w, cell_h, top_margin = 240, 54, 26, 64
    width = label_width + cell_w * max(len(cols), 1) + 24
    height = top_margin + cell_h * max(len(top_rows), 1) + 28
    max_value = max((cells[(r, c)] for r in top_rows for c in cols if (r, c) in cells), default=1)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="Segoe UI, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        f'<text x="16" y="26" font-size="16" font-weight="bold" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
    ]
    for col_index, col in enumerate(cols):
        x = label_width + col_index * cell_w + cell_w / 2
        parts.append(
            f'<text x="{x}" y="{top_margin - 10}" font-size="11" text-anchor="middle" fill="{COLOR_TEXT}">{xml_text(col)}</text>'
        )
    for row_index, row_label in enumerate(top_rows):
        y = top_margin + row_index * cell_h
        display = row_label if len(row_label) <= 26 else row_label[:25] + "…"
        parts.append(
            f'<text x="{label_width - 8}" y="{y + cell_h / 2 + 4}" font-size="11" text-anchor="end" fill="{COLOR_TEXT}">{xml_text(display)}</text>'
        )
        for col_index, col in enumerate(cols):
            x = label_width + col_index * cell_w
            value = cells.get((row_label, col))
            if value is None:
                parts.append(
                    f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" fill="#F1F5F9"/>'
                )
                continue
            # 開根號縮放：大格夠深、小格仍看得出深淺差。
            intensity = (value / max_value) ** 0.5
            opacity = 0.12 + 0.78 * intensity
            text_color = "#FFFFFF" if opacity > 0.55 else COLOR_TEXT
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" fill="{COLOR_APPLICATION}" fill-opacity="{opacity:.2f}"/>'
            )
            parts.append(
                f'<text x="{x + (cell_w - 2) / 2}" y="{y + cell_h / 2 + 4}" font-size="11" text-anchor="middle" fill="{text_color}">{value}</text>'
            )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")
    return {"rows_drawn": len(top_rows), "rows_total": len(row_totals), "cols": cols}


def render_lifecycle_chart(path: Path, title: str, rows: list[dict[str, Any]]) -> None:
    """生命週期軌跡圖：X=申請人家數、Y=件數，依年份連線（技術生命週期判讀用）。"""
    width, height = 980, 620
    left, right, top, bottom = 90, 40, 64, 84
    plot_w, plot_h = width - left - right, height - top - bottom
    data = [
        (int(r["application_year"]), int(r["applicant_count"]), int(r["patent_count"]))
        for r in rows
        if r.get("application_year") is not None
    ]
    data.sort()
    x_max = max([d[1] for d in data] + [1]) * 1.15
    y_max = max([d[2] for d in data] + [1]) * 1.15
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{left}" y="34" font-size="24" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
        f'<text x="{left}" y="54" font-size="13" fill="#6B7280">X = applicant count, Y = patent count, connected by year</text>',
    ]
    for i in range(5):
        y_tick = y_max * i / 4
        y = scale(y_tick, 0, y_max, top + plot_h, top)
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="{COLOR_GRID}" stroke-width="1"/>')
        svg.append(f'<text x="{left - 12}" y="{y + 4:.1f}" text-anchor="end" font-size="12" fill="#6B7280">{y_tick:.0f}</text>')
        x_tick = x_max * i / 4
        x = scale(x_tick, 0, x_max, left, left + plot_w)
        svg.append(f'<text x="{x:.1f}" y="{top + plot_h + 26}" text-anchor="middle" font-size="12" fill="#6B7280">{x_tick:.0f}</text>')
    svg.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#111827"/>')
    svg.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#111827"/>')
    svg.append(f'<text x="{left + plot_w / 2:.0f}" y="{height - 20}" text-anchor="middle" font-size="13" fill="{COLOR_TEXT}">申請人家數</text>')
    points = " ".join(
        f"{scale(a, 0, x_max, left, left + plot_w):.1f},{scale(c, 0, y_max, top + plot_h, top):.1f}"
        for _y, a, c in data
    )
    svg.append(f'<polyline points="{points}" fill="none" stroke="#94A3B8" stroke-width="1.5"/>')
    label_step = max(1, math.ceil(len(data) / 12))
    for index, (year, applicants, count) in enumerate(data):
        x = scale(applicants, 0, x_max, left, left + plot_w)
        y = scale(count, 0, y_max, top + plot_h, top)
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{COLOR_APPLICATION}"/>')
        if index % label_step == 0 or index == len(data) - 1:
            svg.append(f'<text x="{x + 6:.1f}" y="{y - 6:.1f}" font-size="11" fill="#6B7280">{year}</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def render_chart_embed(file: str) -> str:
    """Generic embed: SVG/PNG as <img>, HTML as <iframe>."""
    lower = file.lower()
    if lower.endswith((".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return f'<img class="chart-media" src="{xml_text(file)}" alt="{xml_text(file)}" loading="lazy">'
    if lower.endswith((".html", ".htm")):
        return f'<iframe class="chart-media chart-frame" src="{xml_text(file)}" loading="lazy"></iframe>'
    return f'<a class="chart-fallback" href="{xml_text(file)}">{xml_text(file)}</a>'


def render_index(path: Path, sections: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> None:
    """Generic report index with per-section level/variant toggles.

    Each section: {"title", "variants": [{"label", "file"}, ...], "links"?, "note"?}.
    A section with more than one variant renders toggle buttons; the first
    variant shows by default and buttons swap the visible chart. This layout is
    chart-agnostic, so any report with multiple variants (IPC/CPC 4/5 階, or
    future multi-variant charts) reuses the same toggle behaviour.
    """
    meta = meta or {}
    blocks: list[str] = []
    for index, section in enumerate(sections):
        variants = section.get("variants", [])
        if not variants:
            continue
        group_id = f"sec{index}"
        buttons = ""
        if len(variants) > 1:
            btns = "".join(
                f'<button type="button" class="toggle-btn{" active" if v_i == 0 else ""}" '
                f'data-group="{group_id}" data-target="{group_id}-{v_i}">{xml_text(variant["label"])}</button>'
                for v_i, variant in enumerate(variants)
            )
            buttons = f'<div class="toggle-bar">{btns}</div>'
        panels = "".join(
            f'<div class="chart-panel" id="{group_id}-{v_i}"{"" if v_i == 0 else " hidden"}>'
            f'{render_chart_embed(variant["file"])}</div>'
            for v_i, variant in enumerate(variants)
        )
        note = f'<p class="section-note">{xml_text(section["note"])}</p>' if section.get("note") else ""
        links = section.get("links", [])
        link_html = ""
        if links:
            items = " ".join(
                f'<a class="section-link" href="{xml_text(link["file"])}" target="_blank" rel="noopener">{xml_text(link["label"])} ↗</a>'
                for link in links
            )
            link_html = f'<div class="section-links">{items}</div>'
        blocks.append(
            f'<section class="report-section">'
            f'<div class="section-head"><h2>{xml_text(section.get("title", ""))}</h2>{link_html}</div>'
            f'{buttons}{note}<div class="chart-stage">{panels}</div>'
            f'</section>'
        )

    meta_items = " · ".join(f"{xml_text(k)}: {xml_text(v)}" for k, v in meta.items())
    meta_bar = f'<p class="meta-bar">{meta_items}</p>' if meta_items else ""

    html_text = f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>專利報表</title>
  <style>
    :root {{ color-scheme: light dark; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: "Microsoft JhengHei", "Segoe UI", Arial, sans-serif; margin: 0; padding: 32px; color: #111827; background: #F8FAFC; }}
    h1 {{ font-size: 28px; margin: 0 0 4px; }}
    .meta-bar {{ color: #6B7280; font-size: 13px; margin: 0 0 24px; }}
    .report-section {{ background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 12px; padding: 20px 22px; margin: 0 0 22px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    .section-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; }}
    .report-section h2 {{ font-size: 19px; margin: 0 0 12px; }}
    .section-links {{ font-size: 13px; }}
    .section-link {{ color: #2563EB; text-decoration: none; margin-left: 12px; }}
    .section-link:hover {{ text-decoration: underline; }}
    .section-note {{ color: #6B7280; font-size: 13px; margin: 0 0 12px; }}
    .toggle-bar {{ display: inline-flex; gap: 4px; padding: 4px; background: #F1F5F9; border-radius: 9px; margin: 0 0 14px; }}
    .toggle-btn {{ border: none; background: transparent; color: #334155; font-size: 14px; font-weight: 600; padding: 7px 16px; border-radius: 7px; cursor: pointer; }}
    .toggle-btn:hover {{ background: #E2E8F0; }}
    .toggle-btn.active {{ background: #2563EB; color: #FFFFFF; }}
    .chart-stage {{ width: 100%; overflow-x: auto; }}
    .chart-media {{ max-width: 100%; height: auto; display: block; }}
    .chart-frame {{ width: 100%; height: 620px; border: 1px solid #E5E7EB; border-radius: 8px; }}
    [hidden] {{ display: none !important; }}
  </style>
</head>
<body>
  <h1>Patent Report</h1>
  {meta_bar}
  {"".join(blocks)}
  <script>
    document.querySelectorAll('.toggle-btn').forEach(function (btn) {{
      btn.addEventListener('click', function () {{
        var group = btn.getAttribute('data-group');
        var target = btn.getAttribute('data-target');
        document.querySelectorAll('.toggle-btn[data-group="' + group + '"]').forEach(function (b) {{
          b.classList.toggle('active', b === btn);
        }});
        document.querySelectorAll('.chart-panel[id^="' + group + '-"]').forEach(function (panel) {{
          panel.hidden = (panel.id !== target);
        }});
      }});
    }});
  </script>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


CLASSIFICATION_LEVEL_LABELS = {4: "Level 4 (Subclass)", 5: "Level 5 (Main Group)"}


# ---------------------------------------------------------------------------
# 選擇性出圖：section registry
#
# 每個圖表 section 宣告它依賴哪些報表（SectionSpec.reports），run_chart_trial 依
# 呼叫端指定的 report_names 決定要渲染哪些 sections；不指定＝整套（保留舊行為）。
# ---------------------------------------------------------------------------

# 排名類報表出圖時套 ranking_limit（其餘報表用各自定義的預設列數）。
RANKING_LIMIT_REPORTS = ("applicant_ranking", "owner_ranking")


@dataclass
class ChartContext:
    """單次出圖執行的共享狀態。

    section builders 之間共用：報表結果快取（同一張報表被多個 section 依賴時只查
    一次 DB）、累積的 sections／chart_rows，與 index/report_data 需要的中繼資料。
    """

    run_dir: Path
    ranking_limit: int
    ipc_levels: tuple[int, ...]
    cpc_levels: tuple[int, ...]
    patent_ids: list[int] | None
    filters: dict[str, Any] | None
    analysis_id: int | None
    sections: list[dict[str, Any]] = field(default_factory=list)
    chart_rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)  # map / family_map（有渲染該 section 才有）
    _report_cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    def report(self, name: str) -> dict[str, Any]:
        """取報表結果（有快取），filters／快照與數據端 run_reports_batch 同口徑。

        家族層報表（supports_patent_ids=False）由引擎把 filters/快照轉譯成
        「選中專利所屬家族」的家族集合（完整佈局、含家族全體成員）。
        """
        if name not in self._report_cache:
            limit = self.ranking_limit if name in RANKING_LIMIT_REPORTS else None
            self._report_cache[name] = run_report(
                name, filters=self.filters, limit=limit, patent_ids=self.patent_ids
            )
        return self._report_cache[name]

    def fetched_reports(self) -> dict[str, dict[str, Any]]:
        """本次實際查過的報表結果（report_data.json 落檔用）。"""
        return dict(self._report_cache)


def _build_trend_section(ctx: ChartContext) -> None:
    """申請＋公告趨勢雙線圖（兩張報表固定同圖，選其一也會補齊另一條線）。"""
    # 標題一律取自報表引擎定義的 label_zh（唯一來源），chart_runner 不自己寫標題字串。
    application = ctx.report("application_trend")
    publication = ctx.report("publication_trend")
    trend_title = f'{application["label_zh"]}與{publication["label_zh"]}'
    render_line_chart(ctx.run_dir / "annual_trend.svg", trend_title, application["rows"], publication["rows"])
    ctx.sections.append({"title": trend_title, "variants": [{"label": "Trend", "file": "annual_trend.svg"}]})


def _build_country_map_section(ctx: ChartContext) -> None:
    """專利受理局分布：choropleth 地圖＋泡泡圖。"""
    report = ctx.report("country_distribution")
    map_result = render_jurisdiction_map(ctx.run_dir, report["rows"], title=report["label_zh"])
    ctx.sections.append(map_result["section"])
    ctx.meta["map"] = map_result["meta"]


def _build_family_layout_section(ctx: ChartContext) -> None:
    """國家佈局（現有保護口徑）：家族×國家報表。

    filters/快照經引擎轉譯成「選中專利所屬家族」的家族集合，佈局計入家族
    全體成員；不帶篩選＝全庫。
    """
    family_report = ctx.report("family_country_layout")
    quality_report = ctx.report("family_quality_detail")
    quality_rows = quality_report["rows"]
    family_notes = [
        "計數單位是「同族（發明）」：group by 同族ID，做到申請國（受理局）層級；EP 以區域標示呈現，暫不展開生效國。",
        (
            f"品質現形：生效程序進行中 EP {sum(int(q['ep_in_transition_count']) for q in quality_rows)} 件、"
            f"不完整家族 {sum(1 for q in quality_rows if q['family_incomplete'])}、"
            f"unknown 狀態 {sum(int(q['unknown_status_count']) for q in quality_rows)} 件、"
            f"EPC 欄缺值 EP {sum(int(q['ep_missing_epc_count']) for q in quality_rows)} 件、"
            f"surrogate 家族（無同族ID）{sum(1 for q in quality_rows if q['is_surrogate_family'])}。"
            "明細見 family_quality.json。"
        ),
    ]
    if ctx.analysis_id is not None or ctx.filters:
        family_notes.append("家族集合依篩選／快照圈定；佈局計入家族全體成員，可能含篩選外的國家。")
    family_map_result = render_jurisdiction_map(
        ctx.run_dir,
        family_report["rows"],
        basename="family_country_map",
        bubble_filename="family_country_bubble.svg",
        title=family_report["label_zh"],
        extra_notes=family_notes,
    )
    ctx.sections.append(family_map_result["section"])
    ctx.sections[-1].setdefault("links", []).append({"label": "家族品質明細 JSON", "file": "family_quality.json"})
    write_json(ctx.run_dir / "family_quality.json", {"report": quality_report["report_name"], "rows": quality_rows})
    ctx.meta["family_map"] = family_map_result["meta"]

def _build_classification_section(
    ctx: ChartContext, report_key: str, source_column: str, levels: tuple[int, ...]
) -> None:
    """IPC/CPC 分布共用：每個階層一個 variant（4 階=subclass、5 階=main group）。"""
    report = ctx.report(report_key)
    variants: list[dict[str, str]] = []
    for level in levels:
        rows = collapse_classification_rows(report["rows"], source_column, level)
        chart_key = f"{report_key}_L{level}"
        ctx.chart_rows[chart_key] = rows
        filename = f"{chart_key}.svg"
        level_label = CLASSIFICATION_LEVEL_LABELS.get(level, f"Level {level}")
        render_bar_chart(ctx.run_dir / filename, f'{report["label_zh"]} - {level_label}', rows, source_column)
        variants.append({"label": f"{level} 階 · {level_label.split('(')[-1].rstrip(')')}", "file": filename})
    ctx.sections.append({"title": report["label_zh"], "variants": variants, "note": "4 階=subclass，5 階=main group；可用切換鈕比較。"})


def _build_ipc_section(ctx: ChartContext) -> None:
    _build_classification_section(ctx, "ipc_main_distribution", "Curr. IPC(Main)", ctx.ipc_levels)


def _build_cpc_section(ctx: ChartContext) -> None:
    _build_classification_section(ctx, "cpc_main_distribution", "Curr. CPC(Main)", ctx.cpc_levels)


def _build_applicant_ranking_section(ctx: ChartContext) -> None:
    report = ctx.report("applicant_ranking")
    render_bar_chart(ctx.run_dir / "applicant_ranking.svg", report["label_zh"], report["rows"], "applicant_display_name")
    ctx.sections.append({"title": report["label_zh"], "variants": [{"label": "Applicants", "file": "applicant_ranking.svg"}]})


def _build_owner_ranking_section(ctx: ChartContext) -> None:
    report = ctx.report("owner_ranking")
    render_bar_chart(ctx.run_dir / "owner_ranking.svg", report["label_zh"], report["rows"], "current_assignee_display_name")
    ctx.sections.append({"title": report["label_zh"], "variants": [{"label": "Assignees", "file": "owner_ranking.svg"}]})


def _build_applicant_country_section(ctx: ChartContext) -> None:
    """公司×國家交叉矩陣：一列一家公司、儲存格不跨公司混算。

    預設取前 20 大公司（按總件數排序）；正式流程由使用者給「追蹤公司清單」，
    以 filters 圈定申請人後，矩陣就只畫該清單的公司。
    """
    report = ctx.report("applicant_country_distribution")
    meta = render_matrix_chart(
        ctx.run_dir / "applicant_country_matrix.svg",
        report["label_zh"],
        report["rows"],
        row_key="applicant_display_name",
        col_key="country_code",
    )
    note = (
        f"一列＝一家公司（前 {meta['rows_drawn']} 大／共 {meta['rows_total']} 家，按總件數排序），"
        "欄＝受理局（按件、含死案，與受理局分布同口徑）；儲存格＝該公司在該受理局的件數，不跨公司混算。"
        "完整數據見 report_data.json；追蹤特定公司時用 filters 圈定申請人清單。"
    )
    ctx.sections.append({
        "title": report["label_zh"],
        "variants": [{"label": "Matrix", "file": "applicant_country_matrix.svg"}],
        "note": note,
    })


def _build_top_cited_section(ctx: ChartContext) -> None:
    """高被引用專利排名：detail rows 先組顯示標籤（號碼＋申請人）再畫長條。"""
    report = ctx.report("top_cited_patents")
    cited_rows = [
        {
            **row,
            "cite_label": f'{row.get("授權公告號") or row.get("未審查的公開號(轉換後)") or row["patent_id"]}'
                          f'（{str(row.get("applicant_display_name") or "?")[:14]}）',
        }
        for row in report["rows"]
    ]
    render_bar_chart(
        ctx.run_dir / "top_cited_patents.svg",
        report["label_zh"],
        cited_rows,
        "cite_label",
        value_key="(F1)引用文獻數",
    )
    ctx.sections.append({
        "title": report["label_zh"],
        "variants": [{"label": "Top Cited", "file": "top_cited_patents.svg"}],
        "note": "被引用數（F1）是資料下載時點的快照；無引用欄的批次（精簡匯出）不在排名內。",
    })


def _build_rd_energy_section(ctx: ChartContext) -> None:
    """企業研發能量氣泡圖：cited_rows=0 代表整批無引用資料（精簡匯出），不畫進圖、在 note 現形。"""
    report = ctx.report("company_rd_energy")
    energy_rows = report["rows"]
    energy_plot = [r for r in energy_rows if int(r.get("cited_rows") or 0) > 0]
    energy_skipped = [r for r in energy_rows if int(r.get("cited_rows") or 0) == 0]
    render_bubble_chart(
        ctx.run_dir / "company_rd_energy.svg",
        report["label_zh"],
        energy_plot,
        x_key="cited_total",
        y_key="patent_count",
        size_key="inventor_total",
        label_key="applicant_display_name",
    )
    energy_note = "X＝被引用總數（下載時點快照）、Y＝申請量、泡泡＝發明人數合計。"
    if energy_skipped:
        skipped_names = "、".join(str(r["applicant_display_name"])[:20] for r in energy_skipped[:5])
        energy_note += f" 無引用資料（精簡匯出批）未入圖 {len(energy_skipped)} 家：{skipped_names}{'…' if len(energy_skipped) > 5 else ''}。"
    ctx.sections.append({
        "title": report["label_zh"],
        "variants": [{"label": "Bubble", "file": "company_rd_energy.svg"}],
        "note": energy_note,
    })


def _build_lifecycle_section(ctx: ChartContext) -> None:
    """生命週期軌跡圖：年度 × 申請人家數 vs 件數。"""
    report = ctx.report("lifecycle")
    render_lifecycle_chart(ctx.run_dir / "lifecycle.svg", report["label_zh"], report["rows"])
    ctx.sections.append({
        "title": report["label_zh"],
        "variants": [{"label": "Lifecycle", "file": "lifecycle.svg"}],
        "note": "各點＝一個申請年；萌芽/成長/成熟/衰退的階段判讀由分析者依軌跡判斷。",
    })


def _build_growth_section(ctx: ChartContext) -> None:
    """年增率折線：由申請趨勢衍生計算（連續年才計，前年為 0 不硬算）。"""
    application = ctx.report("application_trend")
    growth_rows = compute_yoy_growth(application["rows"])
    growth_title = f'{application["label_zh"]}年增率'
    render_growth_chart(ctx.run_dir / "application_growth.svg", growth_title, growth_rows)
    ctx.sections.append({
        "title": growth_title,
        "variants": [{"label": "YoY %", "file": "application_growth.svg"}],
        "note": "年增率＝(當年−前一年)/前一年；年份斷檔或前一年為 0 的年份不產生增率點。技術別成長折線待分群引擎產出 topic 後再加。",
    })
    ctx.chart_rows["application_growth"] = growth_rows


@dataclass(frozen=True)
class SectionSpec:
    """一個圖表 section 的宣告：依賴哪些報表、由哪個 builder 渲染。"""

    key: str
    reports: tuple[str, ...]
    build: Callable[[ChartContext], None]


# section 註冊表（順序＝index.html 呈現順序）。選擇性出圖規則：request 的
# report_names 與 spec.reports 有交集 → 渲染該 section。新報表加進報表引擎時
# 必須掛進某個 spec（tests/test_chart_sections.py 會驗 registry 覆蓋所有報表定義）。
SECTION_SPECS: tuple[SectionSpec, ...] = (
    SectionSpec("annual_trend", ("application_trend", "publication_trend"), _build_trend_section),
    SectionSpec("country_map", ("country_distribution",), _build_country_map_section),
    SectionSpec("family_layout", ("family_country_layout", "family_quality_detail"), _build_family_layout_section),
    SectionSpec("ipc", ("ipc_main_distribution",), _build_ipc_section),
    SectionSpec("cpc", ("cpc_main_distribution",), _build_cpc_section),
    SectionSpec("applicant_ranking", ("applicant_ranking",), _build_applicant_ranking_section),
    SectionSpec("owner_ranking", ("owner_ranking",), _build_owner_ranking_section),
    SectionSpec("applicant_country", ("applicant_country_distribution",), _build_applicant_country_section),
    SectionSpec("top_cited", ("top_cited_patents",), _build_top_cited_section),
    SectionSpec("rd_energy", ("company_rd_energy",), _build_rd_energy_section),
    SectionSpec("lifecycle", ("lifecycle",), _build_lifecycle_section),
    SectionSpec("application_growth", ("application_trend",), _build_growth_section),
)


def resolve_sections(report_names: Sequence[str] | None) -> tuple[SectionSpec, ...]:
    """把要出的報表名轉成要渲染的 sections；None＝全部。未知報表名 fail loud。"""
    if report_names is None:
        return SECTION_SPECS
    requested = set(report_names)
    if not requested:
        raise ValueError("report_names 不可為空清單（要全部出圖請傳 None）")
    known = {name for spec in SECTION_SPECS for name in spec.reports}
    unknown = sorted(requested - known)
    if unknown:
        raise ValueError(f"未知報表名（無對應圖表 section）：{', '.join(unknown)}")
    return tuple(spec for spec in SECTION_SPECS if requested & set(spec.reports))


def _create_run_dir(output_dir: Path, prefix: str) -> Path:
    """建立唯一輸出資料夾：同秒重複執行時加序號，避免撞名互寫。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for attempt in range(1, 1001):
        suffix = "" if attempt == 1 else f"_{attempt}"
        candidate = output_dir / f"{prefix}{stamp}{suffix}"
        try:
            candidate.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError(f"無法在 {output_dir} 建立唯一輸出資料夾（同名資料夾過多）")


def run_chart_trial(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    ranking_limit: int = 100,
    ipc_levels: tuple[int, ...] = (4, 5),
    cpc_levels: tuple[int, ...] = (4, 5),
    analysis_id: int | None = None,
    report_names: Sequence[str] | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """渲染報表圖組（MCP reporting tools 與 CLI 共用的出圖入口）。

    report_names=None 出整套（保留舊行為）；給清單則只渲染依賴到那些報表的
    sections（選擇性出圖）。analysis_id 給了用該 analysis 的專利快照出圖，並把
    每個產出檔登錄 app_layer.export_runs；filters 讓 patent 層報表與數據端同
    口徑（家族層報表一律全庫口徑，note 現形）。
    """
    specs = resolve_sections(report_names)
    patent_ids = fetch_analysis_patent_ids(analysis_id) if analysis_id is not None else None
    prefix = f"analysis_{analysis_id}_" if analysis_id is not None else "report_trial_"
    run_dir = _create_run_dir(output_dir, prefix)

    ctx = ChartContext(
        run_dir=run_dir,
        ranking_limit=ranking_limit,
        ipc_levels=tuple(dict.fromkeys(ipc_levels)),
        cpc_levels=tuple(dict.fromkeys(cpc_levels)),
        patent_ids=patent_ids,
        filters=filters or None,
        analysis_id=analysis_id,
    )
    for spec in specs:
        spec.build(ctx)

    fetched = ctx.fetched_reports()
    parameters = {
        "ranking_limit": ranking_limit,
        "ipc_levels": list(ctx.ipc_levels),
        "cpc_levels": list(ctx.cpc_levels),
        "reports_selected": sorted(set(report_names)) if report_names is not None else "all",
        "filters": filters or None,
    }

    write_json(
        run_dir / "report_data.json",
        {
            "parameters": parameters,
            "reports": {
                name: report for name, report in fetched.items() if REPORT_DEFINITIONS[name].supports_patent_ids
            },
            "family_reports": {
                name: report for name, report in fetched.items() if not REPORT_DEFINITIONS[name].supports_patent_ids
            },
            "chart_rows": ctx.chart_rows,
            **ctx.meta,
        },
    )

    render_index(
        run_dir / "index.html",
        ctx.sections,
        meta={
            "ranking_limit": ranking_limit,
            "ipc_levels": " ".join(str(v) for v in ctx.ipc_levels),
            "cpc_levels": " ".join(str(v) for v in ctx.cpc_levels),
        },
    )

    files: list[str] = []
    for section in ctx.sections:
        files.extend(variant["file"] for variant in section.get("variants", []))
        files.extend(link["file"] for link in section.get("links", []))
    files += ["report_data.json", "index.html"]
    # De-duplicate while keeping order (a file may appear as both variant and link).
    files = list(dict.fromkeys(files))

    result: dict[str, Any] = {
        "status": "ok",
        "output_dir": str(run_dir),
        "ranking_limit": ranking_limit,
        "ipc_levels": list(ctx.ipc_levels),
        "cpc_levels": list(ctx.cpc_levels),
        "sections_rendered": [spec.key for spec in specs],
        "files": files,
        **ctx.meta,
    }

    if analysis_id is not None:
        export_count = record_exports(analysis_id, run_dir, files, parameters)
        result["analysis_id"] = analysis_id
        result["export_count"] = export_count

    return result


def render_jurisdiction_map(
    run_dir: Path,
    rows: list[dict[str, Any]],
    basename: str = "country_map",
    bubble_filename: str = "country_bubble.svg",
    title: str = "Patent Jurisdiction Distribution (Map)",
    extra_notes: list[str] | None = None,
) -> dict[str, Any]:
    """Render the jurisdiction map, preferring the Plotly choropleth runner.

    Falls back to the trial bubble SVG if Plotly/kaleido is unavailable, so the
    report always has a jurisdiction panel. Returns the index section and meta.
    basename/bubble_filename/title 可覆寫，讓家族佈局地圖等其他國別報表複用。
    """
    # 圖內標題＝section 標題去掉「（地圖）/ (Map)」尾綴（bubble 與 choropleth 共用）。
    chart_title = title.replace("（地圖）", "").replace(" (Map)", "").strip()
    # The bubble view always renders (standard-library SVG, no extra deps).
    render_country_map(run_dir / bubble_filename, rows, title=chart_title)
    bubble_variant = {"label": "泡泡圖 Bubble", "file": bubble_filename}

    try:
        from backend.app.reports.map_runner import build_country_choropleth

        result = build_country_choropleth(rows, run_dir, basename=basename, title=chart_title)
        map_variant = (
            {"label": "地圖 Choropleth", "file": result["svg_file"]}
            if result.get("svg_file")
            else {"label": "地圖 Choropleth", "file": result["html_file"]}
        )
        variants = [map_variant, bubble_variant]
        links = [{"label": "互動地圖 HTML", "file": result["html_file"]}]
        notes = ["地圖：沒有專利的國家不上色（白底）；有專利的以藍階＋深藍外框標示。"]
        if result.get("regional_marked"):
            marked = "、".join(f'{item["country_code"]} {item["patent_count"]}' for item in result["regional_marked"])
            notes.append(f"橘色菱形＝區域專利局（國家級展不開，標轄區位置）：{marked}。")
        if result.get("skipped"):
            skipped_codes = ", ".join(item["country_code"] for item in result["skipped"])
            notes.append(f"未在地圖繪出的代碼（無地域/未對照，見圖面下方註記）：{skipped_codes}")
        notes.extend(extra_notes or [])
        section = {
            "title": title,
            "variants": variants,
            "links": links,
            "note": " ".join(notes),
        }
        return {"section": section, "meta": {"engine": "plotly", **{k: result[k] for k in ("static_ok", "drawn", "labeled", "regional_marked", "skipped")}}}
    except Exception as exc:  # noqa: BLE001 - fall back so the report still renders
        notes = [f"Plotly 地圖不可用，只輸出泡泡圖：{type(exc).__name__}: {exc}"]
        notes.extend(extra_notes or [])
        section = {
            "title": title,
            "variants": [bubble_variant],
            "note": " ".join(notes),
        }
        return {"section": section, "meta": {"engine": "bubble_only", "error": f"{type(exc).__name__}: {exc}"}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Render first-pass report charts into output directory.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--ranking-limit", type=int, default=100, help="Top N limit for applicant and current assignee ranking charts.")
    parser.add_argument("--ipc-levels", type=int, nargs="+", choices=(4, 5), default=[4, 5], help="IPC classification levels to render (4=subclass, 5=main group). Defaults to both.")
    parser.add_argument("--cpc-levels", type=int, nargs="+", choices=(4, 5), default=[4, 5], help="CPC classification levels to render (4=subclass, 5=main group). Defaults to both.")
    parser.add_argument("--analysis-id", type=int, help="Bind charts to an app_layer analysis: use its patent snapshot and record files into export_runs.")
    parser.add_argument("--reports", help="Comma-separated report keys to render selectively (default: full battery).")
    parser.add_argument("--filters", help="JSON object of report filters (whitelist columns; family reports stay full-DB scope).")
    args = parser.parse_args()
    report_names = [name.strip() for name in args.reports.split(",") if name.strip()] if args.reports else None
    try:
        result = run_chart_trial(
            args.output_dir,
            ranking_limit=args.ranking_limit,
            ipc_levels=tuple(args.ipc_levels),
            cpc_levels=tuple(args.cpc_levels),
            analysis_id=args.analysis_id,
            report_names=report_names,
            filters=parse_json_arg(args.filters),
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary: emit a clean error, exit non-zero
        print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
