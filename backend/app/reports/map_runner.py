"""Country jurisdiction choropleth map generator.

CLI-first, reusable map runner. Takes the `country_distribution` report rows
(``country_code`` + ``patent_count``) and renders a Plotly choropleth as an
interactive HTML file plus, when a static image engine (kaleido/chrome) is
available, a static SVG.

The runner never rewrites Raw/Core data; it only reads report rows and writes
output files. Country codes are 2-letter ISO alpha-2 and are mapped to alpha-3
for Plotly's ``locationmode="ISO-3"``. Regional patent authorities (EP, WO ...)
have no single-country geometry and are reported as skipped rather than drawn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ISO 3166-1 alpha-2 -> alpha-3 for jurisdictions common in patent data.
ISO2_TO_ISO3: dict[str, str] = {
    "US": "USA", "CN": "CHN", "JP": "JPN", "KR": "KOR", "TW": "TWN",
    "DE": "DEU", "FR": "FRA", "GB": "GBR", "CA": "CAN", "AU": "AUS",
    "IN": "IND", "IT": "ITA", "ES": "ESP", "NL": "NLD", "CH": "CHE",
    "SE": "SWE", "BE": "BEL", "AT": "AUT", "RU": "RUS", "BR": "BRA",
    "MX": "MEX", "SG": "SGP", "HK": "HKG", "IL": "ISR", "FI": "FIN",
    "DK": "DNK", "NO": "NOR", "PL": "POL", "TR": "TUR", "ZA": "ZAF",
    "NZ": "NZL", "MY": "MYS", "TH": "THA", "ID": "IDN", "VN": "VNM",
    "PH": "PHL", "PT": "PRT", "IE": "IRL", "CZ": "CZE", "HU": "HUN",
    "AR": "ARG", "CL": "CHL", "SA": "SAU", "AE": "ARE", "EG": "EGY",
    "UA": "UKR", "GR": "GRC", "RO": "ROU", "LU": "LUX",
}

# Approximate country centroids (lon, lat) for placing on-map value labels.
COUNTRY_CENTROIDS: dict[str, tuple[float, float]] = {
    "US": (-98, 39), "CN": (104, 35), "JP": (138, 37), "KR": (128, 36), "TW": (121, 24),
    "DE": (10, 51), "FR": (2, 47), "GB": (-2, 54), "CA": (-106, 56), "AU": (134, -25),
    "IN": (78, 22), "IT": (12, 42), "ES": (-4, 40), "NL": (5, 52), "CH": (8, 47),
    "SE": (15, 62), "BE": (4, 50), "AT": (14, 47), "RU": (100, 62), "BR": (-52, -10),
    "MX": (-102, 23), "SG": (104, 1), "HK": (114, 22), "IL": (35, 31), "FI": (26, 64),
    "DK": (10, 56), "NO": (9, 62), "PL": (19, 52), "TR": (35, 39), "ZA": (24, -29),
    "NZ": (172, -41), "MY": (102, 4), "TH": (101, 15), "ID": (118, -2), "VN": (106, 16),
    "PH": (122, 12), "PT": (-8, 39), "IE": (-8, 53), "CZ": (15, 50), "HU": (19, 47),
    "AR": (-64, -34), "CL": (-71, -30), "SA": (45, 24), "AE": (54, 24), "EG": (30, 27),
    "UA": (32, 49), "GR": (22, 39), "RO": (25, 46), "LU": (6, 50),
}

# Regional / supranational patent authorities: no single-country choropleth geometry.
NON_COUNTRY_AUTHORITIES: set[str] = {"EP", "WO", "EA", "OA", "AP", "GC", "BX", "IB"}

# 區域專利局的地區座標（lon, lat）：受理局分布做不到國家級展開時，
# 至少在地圖上把該「地區」標出來（2026-07-15 使用者定案）。
# WO/IB（PCT 國際申請）沒有地域，不標點、改在圖面下方註記。
REGIONAL_AUTHORITY_CENTROIDS: dict[str, tuple[float, float]] = {
    "EP": (10, 50),   # 歐洲專利局 EPO
    "EA": (65, 55),   # 歐亞專利局 EAPO
    "AP": (30, -8),   # ARIPO（非洲地區工業產權組織）
    "OA": (2, 10),    # OAPI（非洲智慧財產權組織）
    "GC": (48, 24),   # GCC 海灣專利局
    "BX": (5, 51),    # Benelux 比荷盧
}

# 區域局/無地域代碼的顯示名稱（hover 與註記用）。
REGIONAL_AUTHORITY_NAMES: dict[str, str] = {
    "EP": "歐洲專利局 EPO",
    "EA": "歐亞專利局 EAPO",
    "AP": "ARIPO 非洲地區工業產權組織",
    "OA": "OAPI 非洲智慧財產權組織",
    "GC": "GCC 海灣專利局",
    "BX": "Benelux 比荷盧",
    "WO": "PCT 國際申請",
    "IB": "PCT 國際局",
}

DEFAULT_BASENAME = "country_map"
MAP_TITLE = "Patent Jurisdiction Distribution"


def to_iso3(code: Any) -> str | None:
    """Return the ISO alpha-3 code for a 2-letter jurisdiction code, or None."""
    if code is None:
        return None
    key = str(code).strip().upper()
    if not key:
        return None
    return ISO2_TO_ISO3.get(key)


def build_country_choropleth(
    rows: list[dict[str, Any]],
    out_dir: Path,
    basename: str = DEFAULT_BASENAME,
    static: bool = True,
    title: str = MAP_TITLE,
) -> dict[str, Any]:
    """Render a choropleth for country_distribution rows.

    Writes ``<basename>.html`` (interactive) and, when possible, ``<basename>.svg``
    (static). Returns a result dict with produced files and any skipped codes.
    """
    import plotly.express as px  # local import: only needed when the map is built

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    locations: list[str] = []
    values: list[int] = []
    hover_codes: list[str] = []
    skipped: list[dict[str, Any]] = []
    label_lons: list[float] = []
    label_lats: list[float] = []
    label_texts: list[str] = []
    missing_centroid: list[str] = []
    regional_marks: list[tuple[str, int]] = []
    no_geo_notes: list[str] = []
    for row in rows:
        code = row.get("country_code")
        count = int(row.get("patent_count") or 0)
        iso3 = to_iso3(code)
        key = "" if code is None else str(code).strip().upper()
        if iso3 is None:
            # 區域專利局：無單國幾何可上色，但有地區座標 → 用區域標記畫在地圖上。
            if key in REGIONAL_AUTHORITY_CENTROIDS:
                regional_marks.append((key, count))
                continue
            # WO/IB 等無地域代碼：不畫點，落圖面下方註記，仍列 skipped 供追溯。
            if key in NON_COUNTRY_AUTHORITIES:
                no_geo_notes.append(f"{key}（{REGIONAL_AUTHORITY_NAMES.get(key, key)}）{count} 件")
                skipped.append({"country_code": key, "patent_count": count, "reason": "no_geography"})
                continue
            skipped.append({"country_code": key, "patent_count": count, "reason": "unmapped_code"})
            continue
        locations.append(iso3)
        values.append(count)
        hover_codes.append(key)
        centroid = COUNTRY_CENTROIDS.get(key)
        if centroid:
            label_lons.append(centroid[0])
            label_lats.append(centroid[1])
            label_texts.append(f"{key} {count}")
        else:
            missing_centroid.append(key)

    files: list[str] = []
    html_name = f"{basename}.html"
    svg_name = f"{basename}.svg"

    fig = px.choropleth(
        locations=locations,
        color=values,
        locationmode="ISO-3",
        color_continuous_scale="Blues",
        labels={"color": "Patents", "locations": "Country"},
        title=title,
    )
    # Only countries with data are filled; give them a dark outline so a light
    # blue (small count) stays distinct from the white "no data" land.
    fig.update_traces(
        customdata=hover_codes,
        hovertemplate="%{customdata}: %{z} patents<extra></extra>",
        marker_line_color="#1E3A8A",
        marker_line_width=0.6,
    )
    # No-data regions render as white land with light-grey borders (no colour).
    fig.update_geos(
        showframe=False,
        showcoastlines=False,
        showland=True,
        landcolor="white",
        showcountries=True,
        countrycolor="#D1D5DB",
        showocean=False,
        projection_type="natural earth",
        bgcolor="rgba(0,0,0,0)",
    )
    # On-map value labels: a white pill with "<code> <count>", readable over any fill.
    if label_lons:
        fig.add_scattergeo(
            lon=label_lons,
            lat=label_lats,
            text=label_texts,
            mode="markers+text",
            marker={"size": 30, "color": "white", "opacity": 0.9, "line": {"color": "#1E3A8A", "width": 1}},
            textfont={"color": "#0F172A", "size": 11, "family": "Arial"},
            textposition="middle center",
            hoverinfo="skip",
            showlegend=False,
        )

    # 區域專利局標記：橘色菱形＋「代碼 件數」，標在該局轄區位置——
    # 國家級展不開（如受理局口徑的 EP），至少讓地圖呈現「這個地區有佈局」。
    if regional_marks:
        fig.add_scattergeo(
            lon=[REGIONAL_AUTHORITY_CENTROIDS[code][0] for code, _ in regional_marks],
            lat=[REGIONAL_AUTHORITY_CENTROIDS[code][1] for code, _ in regional_marks],
            text=[f"{code} {count}" for code, count in regional_marks],
            mode="markers+text",
            marker={"size": 34, "color": "#F59E0B", "opacity": 0.85, "symbol": "diamond",
                    "line": {"color": "#92400E", "width": 1.5}},
            textfont={"color": "#451A03", "size": 11, "family": "Arial"},
            textposition="middle center",
            hovertext=[f"{REGIONAL_AUTHORITY_NAMES.get(code, code)}: {count} patents" for code, count in regional_marks],
            hoverinfo="text",
            showlegend=False,
        )
    # 無地域代碼（WO/IB＝PCT）畫不上地圖，直接註記在圖面下方。
    if no_geo_notes:
        fig.add_annotation(
            text="無地域代碼：" + "、".join(no_geo_notes),
            xref="paper", yref="paper", x=0.01, y=-0.04, showarrow=False,
            font={"size": 11, "color": "#6B7280"}, align="left",
        )

    fig.update_layout(
        margin={"r": 20, "t": 60, "l": 20, "b": 40},
        coloraxis_colorbar={"title": "Patents"},
        paper_bgcolor="white",
    )

    fig.write_html(out_dir / html_name, include_plotlyjs="inline", full_html=True)
    files.append(html_name)

    static_ok = False
    static_error: str | None = None
    if static:
        try:
            fig.write_image(out_dir / svg_name)
            files.append(svg_name)
            static_ok = True
        except Exception as exc:  # kaleido/chrome missing or failed
            static_error = f"{type(exc).__name__}: {exc}"

    return {
        "status": "ok",
        "files": files,
        "html_file": html_name,
        "svg_file": svg_name if static_ok else None,
        "static_ok": static_ok,
        "static_error": static_error,
        "drawn": len(locations),
        "labeled": len(label_lons),
        "label_missing_centroid": missing_centroid,
        "regional_marked": [{"country_code": code, "patent_count": count} for code, count in regional_marks],
        "skipped": skipped,
    }
