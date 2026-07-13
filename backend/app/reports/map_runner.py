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
    for row in rows:
        code = row.get("country_code")
        count = int(row.get("patent_count") or 0)
        iso3 = to_iso3(code)
        key = "" if code is None else str(code).strip().upper()
        if iso3 is None:
            reason = "regional_authority" if key in NON_COUNTRY_AUTHORITIES else "unmapped_code"
            skipped.append({"country_code": key, "patent_count": count, "reason": reason})
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
        title=MAP_TITLE,
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

    fig.update_layout(
        margin={"r": 20, "t": 60, "l": 20, "b": 20},
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
        "skipped": skipped,
    }
