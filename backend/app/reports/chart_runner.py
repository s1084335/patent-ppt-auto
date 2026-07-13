from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.app.reports.report_engine import run_report


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
        f'<text x="{left}" y="54" font-size="13" fill="#6B7280">Application year and publication year comparison</text>',
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
    svg.append(f'<polyline points="{points(pub)}" fill="none" stroke="{COLOR_PUBLICATION}" stroke-width="3"/>')
    for year in years:
        x = scale(year, years[0], years[-1], left, left + plot_w)
        svg.append(f'<circle cx="{x:.1f}" cy="{scale(app.get(year, 0), 0, max_count, top + plot_h, top):.1f}" r="3.5" fill="{COLOR_APPLICATION}"/>')
        svg.append(f'<circle cx="{x:.1f}" cy="{scale(pub.get(year, 0), 0, max_count, top + plot_h, top):.1f}" r="3.5" fill="{COLOR_PUBLICATION}"/>')
    svg.append(f'<rect x="{left + 10}" y="{top + 8}" width="12" height="12" fill="{COLOR_APPLICATION}"/><text x="{left + 28}" y="{top + 19}" font-size="13" fill="{COLOR_TEXT}">Application Year</text>')
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


def render_country_map(path: Path, rows: list[dict[str, Any]]) -> None:
    width, height = 980, 540
    left, top = 50, 70
    map_w, map_h = 880, 390
    max_value = max([int(row["patent_count"]) for row in rows] + [1])
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="50" y="36" font-size="24" font-weight="700" fill="{COLOR_TEXT}">Patent Jurisdiction Distribution</text>',
        f'<rect x="{left}" y="{top}" width="{map_w}" height="{map_h}" fill="{COLOR_MAP}" stroke="#94A3B8"/>',
    ]
    for lon in range(-180, 181, 60):
        x = scale(lon, -180, 180, left, left + map_w)
        svg.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + map_h}" stroke="{COLOR_GRID}" stroke-width="1"/>')
    for lat in range(-60, 61, 30):
        y = scale(lat, 85, -85, top, top + map_h)
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + map_w}" y2="{y:.1f}" stroke="{COLOR_GRID}" stroke-width="1"/>')
    for row in rows:
        code = str(row["country_code"])
        lon, lat = COUNTRY_CENTROIDS.get(code, (0, 0))
        x = scale(lon, -180, 180, left, left + map_w)
        y = scale(lat, 85, -85, top, top + map_h)
        value = int(row["patent_count"])
        radius = 8 + 34 * math.sqrt(value / max_value)
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="#2563EB" fill-opacity="0.68" stroke="#1E40AF" stroke-width="2"/>')
        svg.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle" font-size="13" fill="white" font-weight="700">{xml_text(code)}</text>')
        svg.append(f'<text x="{x:.1f}" y="{y + radius + 18:.1f}" text-anchor="middle" font-size="13" fill="{COLOR_TEXT}">{value}</text>')
    svg.append('<text x="50" y="505" font-size="12" fill="#6B7280">Bubble view: circle area is proportional to patent count. See the Choropleth tab for the filled map.</text>')
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
  <title>Patent Report</title>
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


def run_chart_trial(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    ranking_limit: int = 100,
    ipc_levels: tuple[int, ...] = (4, 5),
    cpc_levels: tuple[int, ...] = (4, 5),
    analysis_id: int | None = None,
) -> dict[str, Any]:
    # With an analysis_id the charts use that analysis's patent snapshot and each
    # produced file is recorded into app_layer.export_runs. Without it the run is
    # a trial (no DB writes), preserving the original behaviour.
    patent_ids = fetch_analysis_patent_ids(analysis_id) if analysis_id is not None else None
    prefix = f"analysis_{analysis_id}_" if analysis_id is not None else "report_trial_"
    run_dir = output_dir / (prefix + datetime.now().strftime("%Y%m%d_%H%M%S"))
    run_dir.mkdir(parents=True, exist_ok=True)

    ipc_levels = tuple(dict.fromkeys(ipc_levels))
    cpc_levels = tuple(dict.fromkeys(cpc_levels))

    reports = {
        "application_trend": run_report("application_trend", patent_ids=patent_ids),
        "publication_trend": run_report("publication_trend", patent_ids=patent_ids),
        "country_distribution": run_report("country_distribution", patent_ids=patent_ids),
        "ipc_main_distribution": run_report("ipc_main_distribution", patent_ids=patent_ids),
        "cpc_main_distribution": run_report("cpc_main_distribution", patent_ids=patent_ids),
        "applicant_ranking": run_report("applicant_ranking", limit=ranking_limit, patent_ids=patent_ids),
        "owner_ranking": run_report("owner_ranking", limit=ranking_limit, patent_ids=patent_ids),
    }

    sections: list[dict[str, Any]] = []

    render_line_chart(run_dir / "annual_trend.svg", "Patent Application and Publication Trend", reports["application_trend"]["rows"], reports["publication_trend"]["rows"])
    sections.append({"title": "Patent Application & Publication Trend", "variants": [{"label": "Trend", "file": "annual_trend.svg"}]})

    map_result = render_jurisdiction_map(run_dir, reports["country_distribution"]["rows"])
    sections.append(map_result["section"])

    chart_rows: dict[str, list[dict[str, Any]]] = {}
    classification_charts = (
        ("ipc_main_distribution", "IPC Classification Distribution", "Curr. IPC(Main)", ipc_levels),
        ("cpc_main_distribution", "CPC Classification Distribution", "Curr. CPC(Main)", cpc_levels),
    )
    for report_key, title, source_column, levels in classification_charts:
        variants: list[dict[str, str]] = []
        for level in levels:
            rows = collapse_classification_rows(reports[report_key]["rows"], source_column, level)
            chart_key = f"{report_key}_L{level}"
            chart_rows[chart_key] = rows
            filename = f"{chart_key}.svg"
            level_label = CLASSIFICATION_LEVEL_LABELS.get(level, f"Level {level}")
            render_bar_chart(run_dir / filename, f"{title} - {level_label}", rows, source_column)
            variants.append({"label": f"{level} 階 · {level_label.split('(')[-1].rstrip(')')}", "file": filename})
        sections.append({"title": title, "variants": variants, "note": "4 階=subclass，5 階=main group；可用切換鈕比較。"})

    render_bar_chart(run_dir / "applicant_ranking.svg", "Top Patent Applicants", reports["applicant_ranking"]["rows"], "applicant_display_name")
    sections.append({"title": "Top Patent Applicants", "variants": [{"label": "Applicants", "file": "applicant_ranking.svg"}]})
    render_bar_chart(run_dir / "owner_ranking.svg", "Current Patent Assignee Ranking", reports["owner_ranking"]["rows"], "current_assignee_display_name")
    sections.append({"title": "Current Patent Assignee Ranking", "variants": [{"label": "Assignees", "file": "owner_ranking.svg"}]})

    write_json(
        run_dir / "report_data.json",
        {
            "parameters": {
                "ranking_limit": ranking_limit,
                "ipc_levels": list(ipc_levels),
                "cpc_levels": list(cpc_levels),
            },
            "reports": reports,
            "chart_rows": chart_rows,
            "map": map_result["meta"],
        },
    )

    render_index(
        run_dir / "index.html",
        sections,
        meta={
            "ranking_limit": ranking_limit,
            "ipc_levels": " ".join(str(v) for v in ipc_levels),
            "cpc_levels": " ".join(str(v) for v in cpc_levels),
        },
    )

    files: list[str] = []
    for section in sections:
        files.extend(variant["file"] for variant in section.get("variants", []))
        files.extend(link["file"] for link in section.get("links", []))
    files += ["report_data.json", "index.html"]
    # De-duplicate while keeping order (a file may appear as both variant and link).
    files = list(dict.fromkeys(files))

    result: dict[str, Any] = {
        "status": "ok",
        "output_dir": str(run_dir),
        "ranking_limit": ranking_limit,
        "ipc_levels": list(ipc_levels),
        "cpc_levels": list(cpc_levels),
        "map": map_result["meta"],
        "files": files,
    }

    if analysis_id is not None:
        parameters = {"ranking_limit": ranking_limit, "ipc_levels": list(ipc_levels), "cpc_levels": list(cpc_levels)}
        export_count = record_exports(analysis_id, run_dir, files, parameters)
        result["analysis_id"] = analysis_id
        result["export_count"] = export_count

    return result


def render_jurisdiction_map(run_dir: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Render the jurisdiction map, preferring the Plotly choropleth runner.

    Falls back to the trial bubble SVG if Plotly/kaleido is unavailable, so the
    report always has a jurisdiction panel. Returns the index section and meta.
    """
    # The bubble view always renders (standard-library SVG, no extra deps).
    render_country_map(run_dir / "country_bubble.svg", rows)
    bubble_variant = {"label": "泡泡圖 Bubble", "file": "country_bubble.svg"}

    try:
        from backend.app.reports.map_runner import build_country_choropleth

        result = build_country_choropleth(rows, run_dir, basename="country_map")
        map_variant = (
            {"label": "地圖 Choropleth", "file": result["svg_file"]}
            if result.get("svg_file")
            else {"label": "地圖 Choropleth", "file": result["html_file"]}
        )
        variants = [map_variant, bubble_variant]
        links = [{"label": "互動地圖 HTML", "file": result["html_file"]}]
        notes = ["地圖：沒有專利的國家不上色（白底）；有專利的以藍階＋深藍外框標示。"]
        if result.get("skipped"):
            skipped_codes = ", ".join(item["country_code"] for item in result["skipped"])
            notes.append(f"未在單國地圖繪出的代碼（區域專利局/未對照）：{skipped_codes}")
        section = {
            "title": "Patent Jurisdiction Distribution (Map)",
            "variants": variants,
            "links": links,
            "note": " ".join(notes),
        }
        return {"section": section, "meta": {"engine": "plotly", **{k: result[k] for k in ("static_ok", "drawn", "labeled", "skipped")}}}
    except Exception as exc:  # noqa: BLE001 - fall back so the report still renders
        section = {
            "title": "Patent Jurisdiction Distribution (Map)",
            "variants": [bubble_variant],
            "note": f"Plotly 地圖不可用，只輸出泡泡圖：{type(exc).__name__}: {exc}",
        }
        return {"section": section, "meta": {"engine": "bubble_only", "error": f"{type(exc).__name__}: {exc}"}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Render first-pass report charts into output directory.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--ranking-limit", type=int, default=100, help="Top N limit for applicant and current assignee ranking charts.")
    parser.add_argument("--ipc-levels", type=int, nargs="+", choices=(4, 5), default=[4, 5], help="IPC classification levels to render (4=subclass, 5=main group). Defaults to both.")
    parser.add_argument("--cpc-levels", type=int, nargs="+", choices=(4, 5), default=[4, 5], help="CPC classification levels to render (4=subclass, 5=main group). Defaults to both.")
    parser.add_argument("--analysis-id", type=int, help="Bind charts to an app_layer analysis: use its patent snapshot and record files into export_runs.")
    args = parser.parse_args()
    try:
        result = run_chart_trial(
            args.output_dir,
            ranking_limit=args.ranking_limit,
            ipc_levels=tuple(args.ipc_levels),
            cpc_levels=tuple(args.cpc_levels),
            analysis_id=args.analysis_id,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary: emit a clean error, exit non-zero
        print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
