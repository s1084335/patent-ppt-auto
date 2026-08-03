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

from backend.app.reports.cluster_analytics import (
    build_opportunity_matrix,
    build_pain_point_matrix,
    build_topic_effect_table,
)
from backend.app.reports.report_definitions import REPORT_DEFINITIONS
from backend.app.reports.report_engine import parse_json_arg, run_report


def _app_layer_connect():
    import psycopg
    from psycopg.rows import dict_row

    from backend.app.db.connection import get_connection_kwargs

    return psycopg.connect(**get_connection_kwargs(), row_factory=dict_row, connect_timeout=15)


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


def record_exports(
    analysis_id: int,
    run_dir: Path,
    files: list[str],
    parameters: dict[str, Any],
    file_metadata: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Write one app_layer.export_runs row per produced file (path + sha256)."""
    from psycopg.types.json import Jsonb

    inserted = 0
    file_metadata = file_metadata or {}
    with _app_layer_connect() as conn:
        with conn.cursor() as cur:
            for filename in files:
                file_path = run_dir / filename
                if not file_path.exists():
                    continue
                file_parameters = dict(parameters)
                file_parameters["artifact"] = file_metadata.get(filename, {})
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
                        Jsonb(file_parameters),
                    ),
                )
                inserted += 1
        conn.commit()
    return inserted


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"

# F-lite（2026-07-31 使用者核准）：SVG 只換配色與字體、圖型邏輯不動。
# 色票對齊 skill 的 theme.json（Slidesgo 系）——⚠ 值以常數對齊、不做 runtime
# 依賴（引擎不 import skill）；兩邊一致由 tests/test_chart_svg_flite.py 釘住。
COLOR_APPLICATION = "#006DF5"   # theme blue：申請線／長條主色
COLOR_PUBLICATION = "#C62828"   # theme alert：公告線（與藍線對比）
# ── 圖表畫布：以**最終顯示尺寸**設計（P-2，2026-08-03）──
#
# 🔴 實測：排名圖原本畫 980×724px（10.21×7.54 in），塞進 chart_hero 的
# 8.9×4.32 in 圖框被高度卡到 0.573 倍——13px 的公司名到投影片上只剩 5.6pt，
# 而組版原生文字的下限是 12pt。⚠ 根因不是字寫太小，是**圖畫太高**。
#
# 反推：要讓縮放 ≥0.9，畫布不得超過 9.89×4.80 in ＝ 949×461 px。
# 字級 18px（＝13.5pt）× 0.9 ＝ 12.2pt，剛好過線。
CHART_CANVAS_WIDTH = 949
CHART_CANVAS_MAX_HEIGHT = 460
CHART_LABEL_PX = 18          # 列標籤／數值；縮放後約 12.2pt
CHART_ROW_HEIGHT = 28        # 12 列 → 68+336+34 = 438px，仍在上限內
#: 年度矩陣泡泡的最小「大泡泡」半徑——格內兩位數（18px 字）放得下的下限。
#: ⚠ 比這更窄時不再縮泡泡（改為壓縮大小差異），否則數字會滿出來。
BUBBLE_MIN_RADIUS_PX = 14.0

# 年度矩陣顯示年數（2026-08-03 使用者定案：**16 年**，原 15）。
# ⚠ 固定值優於「放不下才砍」——後者讓同一份報表在不同資料量下顯示不同年距，
# 兩次產出無法對照。
# 🔴 改 16 的原因：本案資料橫跨 2011–2026 正好 16 年，15 會砍掉最舊那年。
# 而 15 這個數字當初是**拍的、不是量出來的**；實測 949px 畫布下 16 欄每欄 57px，
# 泡泡與數字都放得下。
CHART_YEAR_WINDOW = 16

COLOR_BAR = "#006DF5"
# ⚠ 不得與 COLOR_TEXT_SOFT 共用色值：轉色表以**色碼**為鍵，兩個角色撞同一個
# 字面值時下游無法分辨「這是次要文字還是次要資料」，只能一起換或一起不換
# （2026-07-31 獨立驗收：次要長條因此被換成裝飾色族，距 accent 僅 0.4°）。
# 🔴 G-8：分段長條的「區段」專用色（有最新受讓人）。
# 實機 p13 兩個圖例是同一個橘——`0A3A80`（總長條）與 `006DF5`（區段）
# 在 chart_recolor 都對到 `FFB74D`。⚠ 我加排名色階時沒檢查目標色是否已被占用。
# ⚠ 不改 `COLOR_APPLICATION`：它同時是趨勢圖的申請年線，動它會波及另一張圖。
# 區段是**另一個資料維度**（有／無受讓人），該用不同**色相**而非同系深淺：
# 白底青 `0891B2`（對白底 3.68），深底對照 `2F9FD8`（對背景 5.39、
# 對排名主色對比 1.72、色相差 165°）。
COLOR_SEGMENT = "#0891B2"
COLOR_BAR_ALT = "#C99A5B"       # 次要長條（暖中性，與資料暖色系一致）
COLOR_MAP = "#F8FAFC"
COLOR_GRID = "#DCE3F2"          # theme bar_track：格線
COLOR_TEXT = "#00094A"          # theme navy：標題與主文字
COLOR_TEXT_SOFT = "#869FB2"     # 次要文字（刻度、副標）
# 淺色填色上的圖元內文字色（見 readable_text_on）；不與 COLOR_TEXT 共用——
# 那是「頁面文字」，這是「畫在圖元上的文字」，底色來源不同。
TEXT_ON_LIGHT = "#1A1A1A"
# SVG 內建字體宣告：不宣告時瀏覽器與 PowerPoint 轉圖都退回襯線字（舊版視覺斷裂主因）。
SVG_FONT_STYLE = "<style>text{font-family:'Microsoft JhengHei','Segoe UI',sans-serif}</style>"


def xml_text(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")




def patent_snapshot_metadata(patent_ids: list[int] | None) -> dict[str, Any]:
    """產生 workspace/analysis 快照 metadata；不把大量專利 ID 重複塞進每列報表。"""
    if patent_ids is None:
        return {"scope": "full_database", "patent_ids_count": None, "patent_ids_sha256": None}
    normalized = [int(value) for value in patent_ids]
    digest_source = ",".join(str(value) for value in normalized).encode("utf-8")
    return {
        "scope": "patent_ids_snapshot",
        "patent_ids_count": len(normalized),
        "patent_ids_sha256": hashlib.sha256(digest_source).hexdigest(),
    }




CHART_FILE_REPORTS: dict[str, list[str]] = {
    "annual_trend.svg": ["application_trend", "publication_trend"],
    "jurisdiction_distribution.svg": ["country_distribution"],
    "family_country_distribution.svg": ["family_country_layout"],
    "ipc_main_distribution_L4.svg": ["ipc_main_distribution"],
    "ipc_main_distribution_L5.svg": ["ipc_main_distribution"],
    "cpc_main_distribution_L4.svg": ["cpc_main_distribution"],
    "cpc_main_distribution_L5.svg": ["cpc_main_distribution"],
    "applicant_ranking.svg": ["applicant_ranking"],
    "owner_ranking.svg": ["owner_ranking"],
    "applicant_country_matrix.svg": ["applicant_country_distribution"],
    "applicant_year_matrix.svg": ["applicant_year_matrix"],
    "applicant_year_matrix_more.svg": ["applicant_year_matrix"],
    "owner_year_matrix.svg": ["owner_year_matrix"],
    "owner_year_matrix_more.svg": ["owner_year_matrix"],
    "lifecycle.svg": ["lifecycle"],
    "family_quality.json": ["family_quality_detail"],
    # 三個分群 artifact 各自對回自己的報表名（供 manifest／解讀查找定位到正確報表）。
    "cluster_topic_table.html": ["cluster_topic_table"],
    "opportunity_quadrant.svg": ["opportunity_quadrant"],
    "pain_point_quadrant.svg": ["pain_point_quadrant"],
}


def report_names_for_artifact(filename: str) -> list[str]:
    """推回單一 artifact 對應的 report key。"""
    # .csv 分支保留：歷史 report_trial manifest 可能還含 .csv 路徑，
    # 若移除會使這些 manifest 的 artifact 無法對應回正確 report key；
    # 新版不再輸出 CSV，但保留此分支不影響行為且避免舊 manifest 讀取異常。
    if filename.endswith(".csv"):
        return [filename[:-4]]
    if filename == "report_data.json":
        return ["all_fetched_reports"]
    mapped = CHART_FILE_REPORTS.get(filename)
    if mapped is not None:
        return mapped
    # 分群產物多來源時帶 slug 後綴（opportunity_quadrant_tech.svg、
    # cluster_topic_table_effect.html 等）；對回基底報表名，讓 manifest／解讀
    # 查找不因分段檔名而落空。
    # ⚠ 2026-07-29 加入 cluster_topic_table：主題統計表改為依通道分檔後，
    # 舊的精確比對（CHART_FILE_REPORTS 只有無後綴的 .html）對不上，
    # manifest 會少掉這兩個檔的報表歸屬——靜默失敗，只有查 manifest 才發現。
    for base, ext in (("opportunity_quadrant", ".svg"),
                      ("pain_point_quadrant", ".svg"),
                      ("cluster_topic_table", ".html")):
        if filename.startswith(f"{base}_") and filename.endswith(ext):
            return [base]
    return []


def build_artifact_manifest(
    run_dir: Path,
    files: list[str],
    *,
    generated_at: str,
    version: str,
    report_names: list[str],
    filters: dict[str, Any] | None,
    analysis_id: int | None,
    patent_ids: list[int] | None,
) -> dict[str, Any]:
    """建立 artifact manifest；DB 只需記檔案路徑與 hash，完整追溯留在此 JSON。"""
    snapshot = patent_snapshot_metadata(patent_ids)
    base = {
        "generated_at": generated_at,
        "version": version,
        "analysis_id": analysis_id,
        "filters": filters or None,
        "report_names": report_names,
        **snapshot,
    }
    artifacts: list[dict[str, Any]] = []
    for filename in files:
        if not filename:
            continue
        path = run_dir / filename
        if not path.is_file():
            continue
        artifact_report_names = report_names_for_artifact(filename)
        artifacts.append({
            **base,
            "file": filename,
            "artifact_type": export_type_for(filename),
            "report_name": artifact_report_names[0] if len(artifact_report_names) == 1 else None,
            "report_names": artifact_report_names,
            "sha256": sha256_file(path),
        })
    return {"metadata": base, "artifacts": artifacts}


def scale(value: float, old_min: float, old_max: float, new_min: float, new_max: float) -> float:
    if old_max == old_min:
        return (new_min + new_max) / 2
    return new_min + (value - old_min) * (new_max - new_min) / (old_max - old_min)


def _int_or_none(value: Any) -> int | None:
    """將年份／件數欄轉成 int；非數字資料視為缺值，不中斷報表產製。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    return int(text)


def render_line_chart(
    path: Path,
    title: str,
    application_rows: list[dict[str, Any]],
    publication_rows: list[dict[str, Any]],
) -> None:
    app = {
        year: count
        for row in application_rows
        if (year := _int_or_none(row.get("application_year"))) is not None
        if (count := _int_or_none(row.get("patent_count"))) is not None
    }
    pub = {
        year: count
        for row in publication_rows
        if (year := _int_or_none(row.get("授權公告年"))) is not None
        if (count := _int_or_none(row.get("patent_count"))) is not None
    }
    years = sorted(set(app) | set(pub))
    max_count = max([*app.values(), *pub.values(), 1])
    width, height = CHART_CANVAS_WIDTH, CHART_CANVAS_MAX_HEIGHT
    left, right, top, bottom = 76, 34, 64, 72
    plot_w = width - left - right
    plot_h = height - top - bottom

    def points(series: dict[int, int]) -> str:
        return " ".join(
            f"{scale(year, years[0], years[-1], left, left + plot_w):.1f},{scale(series.get(year, 0), 0, max_count, top + plot_h, top):.1f}"
            for year in years
        )

    # F-11：刻度改用等差好讀值；⚠ 繪圖上限也要跟著刻度頂端，否則最上面那格畫不到。
    y_ticks = nice_ticks(max_count)
    max_count = max(y_ticks[-1], 1)
    x_labels = years if len(years) <= 12 else years[:: max(1, math.ceil(len(years) / 10))]
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">' + SVG_FONT_STYLE,
        f'<rect width="100%" height="100%" fill="white"/>',
        f'<text data-role="chart-title" x="{left}" y="34" font-size="24" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
    ]
    for tick in y_ticks:
        y = scale(tick, 0, max_count, top + plot_h, top)
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="{COLOR_GRID}" stroke-width="1"/>')
        svg.append(f'<text x="{left - LABEL_TEXT_OFFSET_PX}" y="{y + 4:.1f}" text-anchor="end" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT_SOFT}">{tick}</text>')
    svg.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="{COLOR_TEXT}"/>')
    svg.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="{COLOR_TEXT}"/>')
    for year in x_labels:
        x = scale(year, years[0], years[-1], left, left + plot_w)
        svg.append(f'<text x="{x:.1f}" y="{top + plot_h + 26}" text-anchor="middle" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT_SOFT}">{year}</text>')
    svg.append(f'<polyline points="{points(app)}" fill="none" stroke="{COLOR_APPLICATION}" stroke-width="3"/>')
    if pub:
        # 只有真的有公告序列才畫第二條線，避免單序列時出現一條 0 的假線。
        svg.append(f'<polyline points="{points(pub)}" fill="none" stroke="{COLOR_PUBLICATION}" stroke-width="3"/>')
    for year in years:
        x = scale(year, years[0], years[-1], left, left + plot_w)
        svg.append(f'<circle cx="{x:.1f}" cy="{scale(app.get(year, 0), 0, max_count, top + plot_h, top):.1f}" r="3.5" fill="{COLOR_APPLICATION}"/>')
        if pub:
            svg.append(f'<circle cx="{x:.1f}" cy="{scale(pub.get(year, 0), 0, max_count, top + plot_h, top):.1f}" r="3.5" fill="{COLOR_PUBLICATION}"/>')
    # G-5：圖例中文化。⚠ F-9 那次只清了英文副題、沒清圖例——同一種問題只掃了一半。
    # 圖例是讀者辨識兩條線的唯一依據，用英文等於這張圖有一半看不懂。
    svg.append(f'<rect x="{left + 10}" y="{top + 8}" width="12" height="12" fill="{COLOR_APPLICATION}"/>'
               f'<text x="{left + 28}" y="{top + 19}" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">申請年</text>')
    if pub:
        svg.append(f'<rect x="{left + 148}" y="{top + 8}" width="12" height="12" fill="{COLOR_PUBLICATION}"/>'
                   f'<text x="{left + 166}" y="{top + 19}" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">授權公告年</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def render_bar_chart(path: Path, title: str, rows: list[dict[str, Any]], label_key: str, value_key: str = "patent_count", limit: int = 20) -> None:
    data = rows[:limit]
    width = CHART_CANVAS_WIDTH
    top = 68
    # 🔴 G-7：列少時把列高撐開，否則圖只有一小條、框空掉一半
    # （實機 p9 CPC L4 只有 1 列，圖高 130px 放進 3.2in 的框，空 48%）。
    # ⚠ 有上限：無限放大會讓單列長條變成一整塊色帶，也不成圖。
    row_h = _fill_row_height(len(data), top=top, bottom=34)
    # G-3：標籤區依實際最長標籤決定（含 IPC 技術名），不寫死——否則長標籤被畫布裁掉。
    left = label_gutter([
        (lambda raw: raw if tech_name(raw) == raw else f"{raw}　{tech_name(raw)}")(
            str(row.get(label_key) or "")) for row in data])
    # right 留給列尾數值：18px 字要放得下「1,234」而不撞出畫布。
    right = 150
    bottom = 34
    height = top + bottom + max(1, len(data)) * row_h
    plot_w = width - left - right
    max_value = max([int(row[value_key]) for row in data] + [1])
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">' + SVG_FONT_STYLE,
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text data-role="chart-title" x="28" y="36" font-size="24" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
    ]
    # F-12：截斷了就要說——同一種圖不得一張標、一張不標。
    note = truncation_note(len(data), len(rows))
    if note:
        svg.append(f'<text x="{width - 40}" y="36" text-anchor="end" font-size="{CHART_LABEL_PX}" '
                   f'fill="{COLOR_TEXT_SOFT}">{xml_text(note)}</text>')
    for index, row in enumerate(data):
        y = top + index * row_h
        raw_label = str(row.get(label_key) or "")
        # 分類代碼補上技術意義（C-3）：只給 `A63B-069` 讀者不知道那是什麼技術。
        # ⚠ 深化而非加參數——本函式也畫申請人排名，`tech_name` 查不到就原樣回傳，
        # 公司名不會被誤加工，呼叫端一處都不用改。
        annotated = tech_name(raw_label)
        label = xml_text(raw_label if annotated == raw_label else f"{raw_label}　{annotated}")
        value = int(row[value_key])
        bar_w = scale(value, 0, max_value, 0, plot_w)
        # 🔴 2026-07-31：原本 `index % 2` 依奇偶列交替兩色——**沒有任何語意**，
        # 卻讓讀者以為長條分成兩類（獨立驗收在三張排名圖抓到，且沒有圖例可解釋）。
        # ⚠ 為這種交替補圖例等於為不存在的分類編故事；正解是拿掉交替。
        # 需要幫助讀者對齊列時該用斑馬紋**底色**，不是改資料本身的顏色。
        # 🔴 2026-08-02（W-2）：移除交替後五條變成完全同色，補上**依數值**的
        # 連續深淺——這個有語意，且同件數必同色。
        color = ranking_bar_color(value, max_value)
        # 🔴 I-3：列標籤**左對齊**——字寬估算猜三次仍被裁（實測真實寬度比估算多 13%），
        # 改成從左緣固定位置開始畫，標籤多長都不可能超出左界。
        svg.append(f'<text x="{LABEL_TEXT_OFFSET_PX}" y="{y + 20}" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">{label}</text>')
        svg.append(f'<rect x="{left}" y="{y + 5}" width="{bar_w:.1f}" height="18" rx="2" fill="{color}"/>')
        svg.append(f'<text x="{left + bar_w + 8:.1f}" y="{y + 20}" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">{value}</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def render_segmented_bar_chart(
    path: Path,
    title: str,
    rows: list[dict[str, Any]],
    label_key: str,
    total_key: str,
    segment_key: str,
    segment_label: str,
    limit: int = 20,
) -> None:
    """分段長條圖：總長代表 total_key，著色區段代表 segment_key。

    ⚠ 每列 50px，20 列會讓圖高到 1124px。塞進簡報的圖框後縮到 **0.37 倍**——
    13px 的公司名變成 5px，完全不可讀（2026-07-31 獨立驗收實測）。
    圖是給人「一眼看出誰領先」的，長尾留給附錄的完整表格。
    故 `limit` 預設收斂到 `CHART_ROW_LIMIT`；被截時圖上**明白標示**，
    不得讓讀者以為看到的就是全部。
    """
    total_rows = len(rows)
    data = rows[:limit]
    width = CHART_CANVAS_WIDTH
    # 🔴 P-2：row_h 由 50 壓到 28——原本每列固定 50px 讓 12 列的畫布高達 7.54in，
    # 塞進 4.32in 圖框被壓到 0.573 倍，字只剩 5.6pt（下限 12pt）。
    #
    # ⚠ 但 row_h 一律 28 會讓「最新受讓人」那行放不下——**移除它就是丟資訊**
    # （2026-08-03 使用者定案：資訊不能有被截斷的；既有測試也在守這件事）。
    # 故改為**動態列高**：只有帶受讓人註記的那幾列佔兩行，其餘一行。
    # 本案 12 列只有 1 列有受讓人，總高 466px，縮放仍 ≈0.89。
    row_h = CHART_ROW_HEIGHT
    top = 90
    left = label_gutter([str(row.get(label_key) or "") for row in data])
    right = 150
    bottom = 34

    def _assignees(row: dict[str, Any]) -> str:
        names = [n.strip() for n in str(row.get("recent_assignee_display_names") or "").split("; ") if n.strip()]
        return ("最新受讓人：" + "；".join(names)) if names else ""

    # ⚠ 列數必須跟著**畫布高度上限**走，不是固定 limit：多幾列有受讓人註記，
    # 畫布就變高、縮放變小、字又掉回不可讀。改為逐列累加到上限為止，
    # 少放的那幾列由「顯示前 N/M 名」誠實標示（完整名單在附錄）。
    kept: list[dict[str, Any]] = []
    notes: list[str] = []
    row_heights: list[int] = []
    used = top + bottom
    for row in data:
        note = _assignees(row)
        needed = row_h * (2 if note else 1)
        if kept and used + needed > CHART_CANVAS_MAX_HEIGHT:
            break
        kept.append(row)
        notes.append(note)
        row_heights.append(needed)
        used += needed
    data = kept
    height = top + bottom + (sum(row_heights) or row_h)
    plot_w = width - left - right
    max_value = max([int(row.get(total_key) or 0) for row in data] + [1])
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">' + SVG_FONT_STYLE,
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text data-role="chart-title" x="28" y="36" font-size="24" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
        f'<rect x="28" y="56" width="12" height="12" fill="{RANKING_BAR_SCALE[0]}"/><text x="46" y="67" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">全部專利</text>',
        f'<rect x="126" y="56" width="12" height="12" fill="{COLOR_SEGMENT}"/><text x="144" y="67" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">{xml_text(segment_label)}</text>',
        *([f'<text x="{width - 40}" y="67" text-anchor="end" font-size="{CHART_LABEL_PX}" '
           f'fill="{COLOR_TEXT_SOFT}">{xml_text(truncation_note(len(data), total_rows))}</text>']
          if truncation_note(len(data), total_rows) else []),
    ]
    y_cursor = top
    for index, row in enumerate(data):
        y = y_cursor
        y_cursor += row_heights[index]
        label = xml_text(row.get(label_key))
        total = int(row.get(total_key) or 0)
        segment = min(int(row.get(segment_key) or 0), total)
        total_w = scale(total, 0, max_value, 0, plot_w)
        segment_w = scale(segment, 0, max_value, 0, plot_w)
        segment_x = left + max(total_w - segment_w, 0)
        # 🔴 I-3：列標籤**左對齊**——字寬估算猜三次仍被裁（實測真實寬度比估算多 13%），
        # 改成從左緣固定位置開始畫，標籤多長都不可能超出左界。
        svg.append(f'<text x="{LABEL_TEXT_OFFSET_PX}" y="{y + 20}" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">{label}</text>')
        # 🔴 F-2：原本 fill="#CBD5E1"（白底淺灰藍）被 chart_recolor 當結構色轉成
        # 面板底 274A66，對深空背景只有 1.72——簡報上這根長條等於不存在。
        # 改用資料色階（依數值深淺，W-2），最淺一階對兩種背景都 ≥3.0。
        svg.append(f'<rect class="bar-total" x="{left}" y="{y + 5}" width="{total_w:.1f}" height="{BAR_HEIGHT_PX}" rx="2" fill="{ranking_bar_color(total, max_value)}"/>')
        svg.append(f'<rect class="bar-segment" x="{segment_x:.1f}" y="{y + 5}" width="{segment_w:.1f}" height="{BAR_HEIGHT_PX}" rx="2" fill="{COLOR_SEGMENT}"/>')
        # 🔴 H-7（2026-08-03 實機 p13）：原本一律印「0 / 13」，讀者看不出分子是什麼
        # ——分母是件數，分子是「有最新受讓人」的件數，但數字本身沒有任何線索。
        # 改為：主數字＝總件數；有分段時才用**圖例同色**把它括在後面，
        # 顏色自己會對應到圖例的「有最新受讓人」，不必再加一段說明文字。
        # ⚠ 分段為 0 時整個括號不印——印「(0)」只是噪音。
        segment_mark = (f'<tspan fill="{COLOR_SEGMENT}">（{segment}）</tspan>'
                        if segment > 0 else "")
        svg.append(f'<text x="{left + total_w + 8:.1f}" y="{y + 20}" font-size="{CHART_LABEL_PX}" '
                   f'fill="{COLOR_TEXT}">{total}{segment_mark}</text>')
        # 受讓人名單完整輸出、不截斷——這一列本來就多給了一行。
        # 🔴 I-8（2026-08-03 實機 p13）：原本 y 是 `y + 20 + row_h`，
        # 隔了**一整個列高**，視覺上飄到下一列旁邊，讀者以為那是下一家的註記。
        # 改為緊貼自己那列的長條下方（固定小間距），歸屬一眼可辨。
        if notes[index]:
            # 🔴 2026-08-03 使用者實機：註記壓在自己那列的長條上（實測 2.2px）。
            # 原本 `y + 20 + 12` 的 12 是憑感覺挑的，沒把**長條下緣**與**註記字高**
            # 算進去——長條佔 y+5～y+23，15px 字的上緣落在 y+20.75，必然壓上。
            # ⚠ 改為由幾何推導：長條下緣 ＋ 字身高 ＋ 最小間距。這樣改了長條高
            # 或字級都會自動跟上，不必第三次調那個數字。
            note_font = CHART_LABEL_PX - 3
            note_y = (y + 5 + BAR_HEIGHT_PX) + note_font * TEXT_ASCENT_RATIO + LABEL_MIN_GAP_PX
            svg.append(f'<text x="{left}" y="{note_y:.1f}" font-size="{note_font}" '
                       f'fill="{COLOR_TEXT_SOFT}">{xml_text(notes[index])}</text>')
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
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">' + SVG_FONT_STYLE,
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
        svg.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle" font-size="{CHART_LABEL_PX}" fill="{readable_text_on(fill)}" data-on-fill="{fill}" font-weight="700">{xml_text(code)}</text>')
        svg.append(f'<text x="{x:.1f}" y="{y + radius + 18:.1f}" text-anchor="middle" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">{value}</text>')
    footnote = "Bubble view: circle area is proportional to patent count. 橘色＝區域專利局（標轄區位置）。"
    if no_geo_notes:
        footnote += " 無地域代碼：" + "、".join(no_geo_notes)
    svg.append(f'<text x="50" y="505" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT_SOFT}">{xml_text(footnote)}</text>')
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
    width, height = CHART_CANVAS_WIDTH, CHART_CANVAS_MAX_HEIGHT
    left, right, top, bottom = 90, 40, 64, 84
    plot_w, plot_h = width - left - right, height - top - bottom
    xs = [float(r[x_key]) for r in rows] or [0.0]
    ys = [float(r[y_key]) for r in rows] or [0.0]
    sizes = [float(r[size_key]) for r in rows] or [1.0]
    x_max, y_max, s_max = max(xs + [1.0]) * 1.1, max(ys + [1.0]) * 1.15, max(sizes + [1.0])
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">' + SVG_FONT_STYLE,
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text data-role="chart-title" x="{left}" y="34" font-size="24" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
    ]
    # F-11：兩軸都用等差好讀刻度（原本 max*i/4 取整後印出 0/4/9/13/17）。
    y_ticks = nice_ticks(y_max)
    x_ticks = nice_ticks(x_max)
    y_max = max(y_ticks[-1], 1)
    x_max = max(x_ticks[-1], 1)
    for y_tick in y_ticks:
        y = scale(y_tick, 0, y_max, top + plot_h, top)
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="{COLOR_GRID}" stroke-width="1"/>')
        svg.append(f'<text x="{left - LABEL_TEXT_OFFSET_PX}" y="{y + 4:.1f}" text-anchor="end" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT_SOFT}">{y_tick}</text>')
    for x_tick in x_ticks:
        x = scale(x_tick, 0, x_max, left, left + plot_w)
        svg.append(f'<text x="{x:.1f}" y="{top + plot_h + 26}" text-anchor="middle" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT_SOFT}">{x_tick}</text>')
    svg.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="{COLOR_TEXT}"/>')
    svg.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="{COLOR_TEXT}"/>')
    svg.append(f'<text x="{left + plot_w / 2:.0f}" y="{height - 20}" text-anchor="middle" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">被引用總數（下載時點快照）</text>')
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
        label = str(row[label_key])
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
        svg.append(f'<text x="{x:.1f}" y="{label_y:.1f}" text-anchor="middle" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">{xml_text(label)}</text>')
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

    # 🔴 P-2（2026-08-03）：畫布以**最終顯示尺寸**設計。
    # 原本 240+54×欄、26×列，22 列讓畫布 480×688px；塞進 8.9×4.32in 圖框後
    # 被高度卡到 0.60 倍，11px 的申請人名到投影片上只剩 **5.4pt**（下限 12pt）。
    # ⚠ 瓶頸一律是高度：欄少時圖很窄，寬度再放大也沒用，因為高度先滿。
    #
    # 反推：可用高度 = CHART_CANVAS_MAX_HEIGHT - top_margin - 底部；
    # 列高固定為可讀值，**列數跟著可用高度走**（放不下的列由「顯示前 N」標示）。
    label_width, cell_w, cell_h, top_margin = 300, 66, 30, 96
    usable = CHART_CANVAS_MAX_HEIGHT - top_margin - 28
    max_visible_rows = max(1, usable // cell_h)
    rows_total_count = len(top_rows)
    top_rows = top_rows[:max_visible_rows]
    # 欄寬吃滿畫布：欄少時把剩餘寬度分給列標籤與格子，避免圖過窄而字被縮小。
    grid_w = CHART_CANVAS_WIDTH - label_width - 24
    cell_w = max(cell_w, grid_w // max(len(cols), 1))
    width = label_width + cell_w * max(len(cols), 1) + 24
    height = top_margin + cell_h * max(len(top_rows), 1) + 28
    max_value = max((cells[(r, c)] for r in top_rows for c in cols if (r, c) in cells), default=1)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="Segoe UI, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        f'<text data-role="chart-title" x="16" y="26" font-size="{CHART_LABEL_PX}" font-weight="bold" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
        f'<text x="16" y="56" font-size="{CHART_LABEL_PX}" font-weight="600" fill="#374151">{LEGEND_SCALE_PREFIX}</text>',
    ]
    # 🔴 F-13：格子有三階顏色卻沒有任何說明（實機 p6），讀者無從對照。
    # ⚠ 圖例與格子共用 `bubble_legend_spans`——各算各的會出現
    # 「圖例說 3–5、格子其實畫到 6」這種對不上的情況。
    # I-6：起點與步進都用算的，不寫死（三支渲染函式共用同一對 helper）。
    legend_x = legend_start_x(16, LEGEND_SCALE_PREFIX)
    for legend_color, legend_label, legend_span in bubble_legend_spans(max_value):
        text = f"{legend_label} {legend_span}"
        parts.append(f'<rect x="{legend_x}" y="{44}" width="12" height="12" rx="2" fill="{legend_color}"/>')
        parts.append(f'<text x="{legend_x + 20}" y="{56}" font-size="{CHART_LABEL_PX}" fill="#4B5563">'
                     f'{xml_text(text)}</text>')
        legend_x += legend_step(text, mark_gap=8)
    for col_index, col in enumerate(cols):
        x = label_width + col_index * cell_w + cell_w / 2
        parts.append(
            f'<text x="{x}" y="{top_margin - 12}" font-size="{CHART_LABEL_PX}" text-anchor="middle" fill="{COLOR_TEXT}">{xml_text(col)}</text>'
        )
    for row_index, row_label in enumerate(top_rows):
        y = top_margin + row_index * cell_h
        display = row_label
        parts.append(
            f'<text x="{label_width - 8}" y="{y + cell_h / 2 + 6}" font-size="{CHART_LABEL_PX}" text-anchor="end" fill="{COLOR_TEXT}">{xml_text(display)}</text>'
        )
        for col_index, col in enumerate(cols):
            x = label_width + col_index * cell_w
            value = cells.get((row_label, col))
            if value is None:
                parts.append(
                    f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" fill="#F1F5F9"/>'
                )
                continue
            # 🔴 2026-07-31：原本用 `fill-opacity` 0.12–0.90 疊在同一色上表達大小。
            # ⚠ **不透明度編碼會隨背景明暗翻轉語意**：白底上低不透明＝淡（小），
            # 但深色主題移除白底後，低不透明露出的是深背景、高不透明是亮暖色——
            # 方向整個反過來，與同一份簡報裡的泡泡矩陣（值大＝深）互相矛盾。
            # 改用與泡泡矩陣**同一套離散色階**：色本身帶大小語意，不依賴背景。
            fill = year_bubble_color(value, max_value)[0]
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" fill="{fill}"/>'
            )
            parts.append(
                f'<text x="{x + (cell_w - 2) / 2}" y="{y + cell_h / 2 + 6}" font-size="{CHART_LABEL_PX}" '
                f'text-anchor="middle" fill="{readable_text_on(fill)}" data-on-fill="{fill}">{value}</text>'
            )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")
    return {"rows_drawn": len(top_rows), "rows_total": len(row_totals), "cols": cols}


def year_bubble_matrix_layout(
    rows: list[dict[str, Any]],
    row_key: str,
    year_key: str = "application_year",
    value_key: str = "patent_count",
    row_limit: int = 20,
) -> dict[str, Any]:
    """年度矩陣泡泡圖版面資料：依公司總量取前 20，缺值視為 0。"""
    totals: dict[str, int] = {}
    values: dict[tuple[str, int], int] = {}
    years: set[int] = set()
    for row in rows:
        company = str(row.get(row_key) or "")
        year_value = row.get(year_key)
        if not company or year_value is None:
            continue
        year = int(year_value)
        value = int(row.get(value_key) or 0)
        years.add(year)
        values[(company, year)] = values.get((company, year), 0) + value
        totals[company] = totals.get(company, 0) + value
    top_rows = [name for name, _ in sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:row_limit]]
    # 🔴 橫軸補齊**連續年度**（2026-08-03 使用者：「你把我的 2012、2014 犧牲掉幹嘛？」）。
    # 原本只列「有資料的年」，空年直接消失——2011 的下一欄就是 2013，
    # **時間軸的間距是騙人的**：「連續三年布局」與「隔年才有一次」在圖上長得一樣。
    # ⚠ 那兩年其實全庫無資料（不是被砍），但軸不連續一樣會誤導趨勢判讀。
    ordered_years = _continuous_years(years)[-25:]
    max_value = max([values.get((company, year), 0) for company in top_rows for year in ordered_years] + [1])
    return {"top_rows": top_rows, "years": ordered_years, "values": values, "max_value": max_value, "rows_total": len(totals)}


YEAR_BUBBLE_COLOR_BANDS: tuple[tuple[float, str, str], ...] = (
    (0.25, "#93C5FD", "低"),
    (0.50, "#14B8A6", "中"),
    (0.75, "#F59E0B", "高"),
    (1.00, "#DC2626", "最高"),
)


def readable_text_on(fill: str) -> str:
    """在指定底色上可讀的文字色——**依底色亮度決定**，不得寫死白字。

    🔴 2026-07-31：象限泡泡與地圖泡泡寫死 `fill="white"`，實測對比 1.24–2.82
    （WCAG 門檻 4.5），render 上實質看不見。
    ⚠ 更關鍵的是**下游轉色**：PPT 組版端會把圖表換成深色主題配色，此時底色變了、
    字色沒跟著變。故凡是「畫在圖元上的文字」都要輸出 `data-on-fill` 標記配對的
    底色，下游轉色後才能依新底色重算字色（見 build_ppt.recolor_svg）。

    亮度門檻取 WCAG 相對亮度 0.4：高於此用深字、低於此用白字，兩側皆 ≥4.5。
    """
    value = fill.lstrip("#")
    if len(value) != 6:
        return "#FFFFFF"
    channels = []
    for offset in (0, 2, 4):
        component = int(value[offset:offset + 2], 16) / 255
        channels.append(component / 12.92 if component <= 0.03928
                        else ((component + 0.055) / 1.055) ** 2.4)
    luminance = 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    return TEXT_ON_LIGHT if luminance > 0.4 else "#FFFFFF"


def bubble_legend_spans(max_value: int) -> list[tuple[str, str, str]]:
    """圖例級距：回傳 (色, 標籤, 件數範圍)；本圖用不到的級距**直接不列**。

    級距是把 `YEAR_BUBBLE_COLOR_BANDS` 的比例分界換算回整數件數。

    🔴 2026-08-02 實機 p17（max_value=3）印出「低 **1–0**」——下限大於上限。
    根因：`max_value` 小的時候某一階可能完全落不到任何整數上（最低階涵蓋
    0–0.75 件），此時 `floor(上界)` 就小於 `ceil(下界)`。那一階本圖根本沒有
    任何泡泡，硬印出來只會讓讀者對照不到東西——正確作法是不列。
    ⚠ 同時：`lo == hi` 時寫單一數字，不寫「1–1」這種假區間（實機 p16）。
    """
    spans: list[tuple[str, str, str]] = []
    previous = 0.0
    for upper_bound, color, label in YEAR_BUBBLE_COLOR_BANDS:
        lo = max(1, math.ceil(previous * max_value + 0.001))
        hi = math.floor(upper_bound * max_value)
        previous = upper_bound
        if hi < lo:
            continue
        spans.append((color, label, f"{lo}" if lo == hi else f"{lo}–{hi}"))
    return spans


def year_bubble_color(value: int, max_value: int) -> tuple[str, str]:
    """依全體前 20 家的共同尺度回傳明顯色階，確保上下兩區可直接比較。"""
    ratio = value / max(max_value, 1)
    for upper_bound, color, label in YEAR_BUBBLE_COLOR_BANDS:
        if ratio <= upper_bound:
            return color, label
    return YEAR_BUBBLE_COLOR_BANDS[-1][1], YEAR_BUBBLE_COLOR_BANDS[-1][2]


def render_year_bubble_matrix_chart(
    path: Path,
    title: str,
    layout: dict[str, Any],
    row_names: list[str],
    *,
    year_key_label: str = "application_year",
) -> None:
    """年度 × 公司泡泡矩陣；0 件不畫泡泡，tooltip 保留公司、年份、件數。"""
    years: list[int] = layout["years"]
    values: dict[tuple[str, int], int] = layout["values"]
    max_value = int(layout["max_value"] or 1)
    # 🔴 P-2：畫布以最終顯示尺寸設計。原本 340+82×年、56×列，10 列讓畫布高 719px，
    # 塞進 4.32in 圖框被壓到 0.60 倍，11px 的申請人名只剩 4.8pt（下限 12pt）。
    left, top = 300, 132
    usable = CHART_CANVAS_MAX_HEIGHT - top - 34
    row_h = max(26, usable // max(1, len(row_names)))
    if row_h * len(row_names) > usable:          # 列太多時砍列，不是縮字
        row_names = row_names[:max(1, usable // 26)]
        row_h = 26
    # ⚠ 欄寬有下限（泡泡要放得下），年份多到撐破畫布時**砍年份**而不是繼續縮——
    # 縮到看不清楚等於資訊沒了。砍掉的是最舊的年份，圖上仍是連續區間。
    grid_w = CHART_CANVAS_WIDTH - left - 34
    years_total = len(years)
    # 固定顯示最新 CHART_YEAR_WINDOW 年（使用者定案）。少了的年份必須在圖上標明——
    # 靜默切掉才是不能接受的。
    years = years[-CHART_YEAR_WINDOW:]
    cell_w = max(36, grid_w // max(1, len(years)))
    # 🔴 泡泡半徑上限由**欄寬推導**，不寫死（2026-08-03 補齊連續年度後的迴歸）。
    # 原本固定 9+19=28：欄數 14→16 讓欄距由 43px 縮到 38px，泡泡沒跟著縮，
    # 相鄰兩格直接撞在一起（實測 4 處、最深 5.5px）。
    # ⚠ 半徑與欄寬是同一件事的兩個落點——各寫各的就會靜默撞上。
    # 下限 14 是格內兩位數（18px 字）放得下的最小值；再窄寧可讓大小差異壓縮，
    # 也不能讓數字滿出泡泡。
    bubble_max = max(BUBBLE_MIN_RADIUS_PX, min(28.0, (cell_w - LABEL_MIN_GAP_PX) / 2))
    width = left + max(1, len(years)) * cell_w + 34
    height = top + max(1, len(row_names)) * row_h + 34
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="Segoe UI, sans-serif">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text data-role="chart-title" x="16" y="28" font-size="18" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
        f'<text x="16" y="90" font-size="{CHART_LABEL_PX}" font-weight="600" fill="#374151">{LEGEND_SCALE_PREFIX}</text>',
        *([f'<text x="{width - 34}" y="{top - 14}" text-anchor="end" font-size="{CHART_LABEL_PX - 3}" '
           f'fill="{COLOR_TEXT_SOFT}">僅顯示 {years[0]}–{years[-1]}（共 {years_total} 年）</text>']
          if years_total > len(years) else []),
    ]
    # 🔴 圖例標出**本圖實際的級距數值**，不只寫「低／中／高」。
    # 兩張年度矩陣各自正規化，同樣是「1 件」在兩頁可能落在不同色階；
    # 只寫抽象標籤時讀者無從得知，會把一頁的 1 誤讀成比另一頁的 1 更多。
    # ⚠ 不改成共用尺度：泡泡半徑也吃 max_value，共用會讓小值那張全部縮小
    # （見 shared_matrix_max 的否決理由）。標出級距是不傷鑑別度的作法。
    # I-6：年度矩陣的步進原本只有 74px，比一個圖例項還窄——必然疊字。
    # ⚠ 圓形記號的中心點就是 legend_x，所以起點要再讓出半徑，否則圓會壓到前綴。
    legend_x = legend_start_x(16, LEGEND_SCALE_PREFIX) + 9
    for color, label, span in bubble_legend_spans(max_value):
        text = f"{label} {span}"
        parts.append(f'<circle cx="{legend_x}" cy="86" r="9" fill="{color}"/>')
        parts.append(f'<text x="{legend_x + 14}" y="90" font-size="{CHART_LABEL_PX}" fill="#4B5563">'
                     f'{xml_text(text)}</text>')
        legend_x += legend_step(text, mark_width=18, mark_gap=5)
    year_labels = [_year_axis_label(year, cell_w) for year in years]
    # 縮成兩位數時要說出世紀，否則 '11 讀者無從判斷是哪個一百年。
    if any(label.startswith("'") for label in year_labels):
        # ⚠ 字級不得低於 CHART_LABEL_PX：圖會被縮進 PPT 圖框（縮了兩次），
        # 14px 在 949px 畫布下只剩 9.5pt，低於 12pt 下限（AGENTS.md「SVG 文字的最終字級下限」）。
        parts.append(f'<text x="{left - 10}" y="{top - 14}" font-size="{CHART_LABEL_PX}" '
                     f'text-anchor="end" fill="{COLOR_TEXT_SOFT}">申請年 20—</text>')
    for col_index, label in enumerate(year_labels):
        x = left + col_index * cell_w + cell_w / 2
        parts.append(f'<text x="{x:.1f}" y="{top - 14}" font-size="{CHART_LABEL_PX}" text-anchor="middle" fill="{COLOR_TEXT}">{label}</text>')
    for row_index, company in enumerate(row_names):
        y = top + row_index * row_h
        display = company
        parts.append(f'<text x="{left - 10}" y="{y + 20}" font-size="{CHART_LABEL_PX}" text-anchor="end" fill="{COLOR_TEXT}">{xml_text(display)}</text>')
        for col_index, year in enumerate(years):
            value = values.get((company, year), 0)
            if value <= 0:
                continue
            x = left + col_index * cell_w + cell_w / 2
            radius = 9 + (bubble_max - 9) * math.sqrt(value / max_value)
            fill, color_band = year_bubble_color(value, max_value)
            # ⚠ 位數多時縮小是為了塞進泡泡，但縮到 8px 縮放後只剩 5pt——
            # 下限拉到 CHART_LABEL_PX-4（縮放後仍 ≈9.5pt），再小就不如不標。
            value_font_size = (CHART_LABEL_PX if value < 100
                               else CHART_LABEL_PX - 2 if value < 1000
                               else CHART_LABEL_PX - 4)
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y + 16:.1f}" r="{radius:.1f}" fill="{fill}" '
                f'data-value-band="{color_band}" stroke="#374151" stroke-width="1.1">'
                f'<title>{xml_text(company)} / {year} / {value}</title></circle>'
            )
            parts.append(
                f'<text x="{x:.1f}" y="{y + 20:.1f}" font-size="{value_font_size}" font-weight="700" '
                f'text-anchor="middle" fill="{readable_text_on(fill)}" data-on-fill="{fill}" pointer-events="none">{value}</text>'
            )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


LABEL_FONT_SIZE = CHART_LABEL_PX  # P-2：縮放後 ≥12pt
# 中文與數字混排時每字約 0.6 個字級寬；年份是四位數字，估寬足夠準（只用來避讓）。
LABEL_CHAR_WIDTH = 0.6


def label_box(x: float, y: float, text: str) -> tuple[float, float, float, float]:
    """標籤的外接矩形 (x1, y1, x2, y2)。y 是 SVG baseline，故上緣要往回推一個字級。"""
    w = len(text) * LABEL_FONT_SIZE * LABEL_CHAR_WIDTH
    return (x, y - LABEL_FONT_SIZE, x + w, y + 3)


def boxes_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    """兩個矩形是否相交（碰到邊界不算重疊）。"""
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


#: 象限板畫布的目標長寬比——對齊 `chart_hero` 圖框（8.9×5.0 in ≈ 1.78）。
#: ⚠ 圖比框「更扁」時等比縮放會在上下留白，而那是**版面沒用滿**，不是內容少。
QUADRANT_TARGET_ASPECT = 1.78



#: 象限板圖例前綴（唯一來源——量寬度與畫出來必須是同一個字串，
#: 否則改了文字卻沒改寬度，又會壓在一起）。
LEGEND_PREFIX_TEXT = "色＝龍頭涉入｜數字＝件/家"

#: 圖例項之間的間距（px）。
LEGEND_ITEM_GAP_PX = 24

#: 矩陣類圖表的圖例前綴（唯一來源——量寬度與畫出來必須同一個字串）。
LEGEND_SCALE_PREFIX = "件數色階"


def _text_px(text: str) -> int:
    """文字在 `CHART_LABEL_PX` 字級下的估算寬度（px）。"""
    return int(_display_width(text) * CHART_LABEL_PX)


def legend_start_x(prefix_x: float, prefix_text: str, gap: float | None = None) -> float:
    """圖例第一個色塊的 x：跨過前綴文字的右緣再留間距。

    🔴 I-6（2026-08-03 使用者實機「混到了」）：三支渲染函式各自寫死起點
    （象限板 `margin_l + 200`、泡泡矩陣與年度矩陣 `82`），而「件數色階」四個字
    從 x=16 起算實際到 88——**必壓**。寫死的數字不會跟著文案改。
    """
    return prefix_x + _text_px(prefix_text) + (LEGEND_ITEM_GAP_PX if gap is None else gap)


def legend_step(label: str, *, mark_width: float = 12, mark_gap: float = 8) -> float:
    """一個圖例項要佔的水平距離：色塊 ＋ 間隙 ＋ 文字 ＋ 項間距。"""
    return mark_width + mark_gap + _text_px(label) + LEGEND_ITEM_GAP_PX


#: 排名圖長條的高度（px）。註記位置由它推導，不得各寫各的。
BAR_HEIGHT_PX = 18

#: 文字「字身上緣到 baseline」佔字級的比例，用來判斷文字會不會壓到上方元素。
#: ⚠ 這是估算值——量的是能不能「看起來壓到」，不是精確排版。
TEXT_ASCENT_RATIO = 0.75

#: （已停用）附註距離長條基線的固定距離。
#: 🔴 I-8：原本用整個 `row_h`（56px），註記落在兩列正中間、看起來像下一家的。
#: ⚠ 也不能太大：實測 18 時註記距下一列只剩 23px、距自己那列 33px，仍偏向下方。
#: 14 讓它明顯靠著自己那一列（距自己 34、距下一列 22 → 比例上更靠上），
#: 且仍在該列多給的第二行之內。
NOTE_LINE_OFFSET_PX = 12


def _year_axis_label(year: int, cell_w: float, font_px: float = CHART_LABEL_PX) -> str:
    """欄寬放得下四位數就印四位數，否則印兩位數（`'11`）。

    🔴 2026-08-03：橫軸補齊連續年度後欄距由 43px 縮到 38px，四位數字寬
    ≈ 4 × 18 × 0.62 = 44.6px **放不下**，實機印出 `201120122013…` 黏成一整串。
    ⚠ 欄距與標籤寬是同一件事的兩個落點——改了欄數卻沒改標籤，於是靜默黏住。
    這裡由欄寬**推導**要印幾位數，呼叫端不必判斷。
    """
    if _display_width(str(year)) * font_px + LABEL_MIN_GAP_PX <= cell_w:
        return str(year)
    return f"'{year % 100:02d}"


def _continuous_years(years: set[int] | list[int]) -> list[int]:
    """把年份集合補成連續區間（最小年 → 最大年），空年也列出來。

    ⚠ 空年要有欄位（留白），不能跳過——跳過會讓橫軸的間距失真。
    """
    if not years:
        return []
    lo, hi = min(years), max(years)
    return list(range(lo, hi + 1))


#: 兩個資料點相距多少 px 以內視為「同一個位置」。
#: 🔴 I-4（2026-08-03）：實機 lifecycle 圖 14 個點中，**5 個完全落在同一座標**
#: （那幾年都是「1 家、1 件」）。點重疊時標籤怎麼避讓都沒用——
#: 讀者看到兩個並排的年份無法判斷哪個屬於哪個點，**因為它們屬於同一個點**。
#: ⚠ 前兩輪（E7、H-5）我都在調避讓演算法，那是在解錯的問題。
COLOCATED_TOLERANCE_PX = 3.0


def merge_colocated_labels(
    items: list[tuple[float, float, str]],
) -> list[tuple[float, float, str]]:
    """把座標相同（或極近）的標籤合併成一個，文字以「、」連接。

    ⚠ 這是**換呈現方式**不是調參數：同一個位置本來就只該有一個標籤，
    硬把多個標籤擠在附近，讀者仍然對不出歸屬。
    ⚠ 順序照原輸入，不用 set——年份要照時間排，且每次執行結果要一致。
    """
    groups: list[tuple[float, float, list[str]]] = []
    for x, y, text in items:
        for index, (gx, gy, texts) in enumerate(groups):
            if abs(gx - x) <= COLOCATED_TOLERANCE_PX and abs(gy - y) <= COLOCATED_TOLERANCE_PX:
                texts.append(text)
                break
        else:
            groups.append((x, y, [text]))
    return [(x, y, "、".join(texts)) for x, y, texts in groups]


#: 兩個標籤之間至少要留的間距（px）。
#: 🔴 H-5：原本只判「框有沒有相交」，相切不算相交 → 標籤緊貼、讀者分不出歸屬。
LABEL_MIN_GAP_PX = 3


def place_point_labels(
    items: list[tuple[float, float, str]],
    obstacles: list[tuple[float, float, float]],
) -> list[tuple[float, float] | None]:
    """替資料點標籤挑位置：四個候選方位輪流試，都撞就放棄該標籤。

    🔴 2026-08-02 實機 p4：左下兩個年份疊成「20**」讀不出來。
    07-31 的第一版避讓只看「折線走向」把標籤放到線的另一側——那解的是
    「被折線壓過」，解不了**標籤彼此重疊**與**標籤壓到別的資料點**。
    在本案這種資料（多年落在 1–2 家、1–2 件，點擠成一團）後兩者才是主因。

    ⚠ 放不下時回 `None`（該點不標），不硬放。少一個年份標籤讀者仍看得懂軌跡；
    兩個字疊在一起則是兩個都讀不出來，更糟。

    Parameters
    ----------
    items : [(x, y, text)]  要標的點與文字（y 為資料點座標，非 baseline）
    obstacles : [(x, y, r)] 不可被覆蓋的圓（所有資料點）
    """
    # 🔴 H-5（2026-08-03 實機 p3）：四個方位不夠。年份點擠成一團時位置很快用完，
    # 就退回「不標」——那是丟資訊。改為**兩圈 × 四方位**，先近後遠：
    # 近圈讀起來與點的關聯最清楚，實在放不下才往外退。
    # ⚠ 同時把判定從「有沒有相交」改成「外擴 LABEL_MIN_GAP_PX 後有沒有相交」：
    # 相切（間距 0）不算相交，但視覺上就是黏在一起——p3 的 2021／2011 正是如此。
    candidates = (
        (6, -6), (6, 16), (-6, -6), (-6, 16),        # 近圈
        (14, -14), (14, 24), (-14, -14), (-14, 24),  # 遠圈
    )
    blocked = [(ox - r, oy - r, ox + r, oy + r) for ox, oy, r in obstacles]
    placed: list[tuple[float, float] | None] = []
    for x, y, text in items:
        width = len(text) * LABEL_FONT_SIZE * LABEL_CHAR_WIDTH
        chosen: tuple[float, float] | None = None
        for dx, dy in candidates:
            lx = x + dx if dx > 0 else x + dx - width
            ly = y + dy
            box = label_box(lx, ly, text)
            grown = (box[0] - LABEL_MIN_GAP_PX, box[1] - LABEL_MIN_GAP_PX,
                     box[2] + LABEL_MIN_GAP_PX, box[3] + LABEL_MIN_GAP_PX)
            if any(boxes_overlap(grown, other) for other in blocked):
                continue
            chosen = (lx, ly)
            blocked.append(box)
            break
        placed.append(chosen)
    return placed


def render_lifecycle_chart(path: Path, title: str, rows: list[dict[str, Any]]) -> None:
    """生命週期軌跡圖：X=申請人家數、Y=件數，依年份連線（技術生命週期判讀用）。"""
    width, height = CHART_CANVAS_WIDTH, CHART_CANVAS_MAX_HEIGHT
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
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">' + SVG_FONT_STYLE,
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text data-role="chart-title" x="{left}" y="34" font-size="24" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
    ]
    # F-11：兩軸都用等差好讀刻度（原本 max*i/4 取整後印出 0/4/9/13/17）。
    y_ticks = nice_ticks(y_max)
    x_ticks = nice_ticks(x_max)
    y_max = max(y_ticks[-1], 1)
    x_max = max(x_ticks[-1], 1)
    for y_tick in y_ticks:
        y = scale(y_tick, 0, y_max, top + plot_h, top)
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="{COLOR_GRID}" stroke-width="1"/>')
        svg.append(f'<text x="{left - LABEL_TEXT_OFFSET_PX}" y="{y + 4:.1f}" text-anchor="end" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT_SOFT}">{y_tick}</text>')
    for x_tick in x_ticks:
        x = scale(x_tick, 0, x_max, left, left + plot_w)
        svg.append(f'<text x="{x:.1f}" y="{top + plot_h + 26}" text-anchor="middle" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT_SOFT}">{x_tick}</text>')
    svg.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="{COLOR_TEXT}"/>')
    svg.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="{COLOR_TEXT}"/>')
    svg.append(f'<text x="{left + plot_w / 2:.0f}" y="{height - 20}" text-anchor="middle" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">申請人家數</text>')
    points = " ".join(
        f"{scale(a, 0, x_max, left, left + plot_w):.1f},{scale(c, 0, y_max, top + plot_h, top):.1f}"
        for _y, a, c in data
    )
    svg.append(f'<polyline points="{points}" fill="none" stroke="#94A3B8" stroke-width="1.5"/>')
    coords = [
        (scale(a, 0, x_max, left, left + plot_w), scale(c, 0, y_max, top + plot_h, top))
        for _y, a, c in data
    ]
    for x, y in coords:
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{COLOR_APPLICATION}"/>')
    # 🔴 標籤避讓改用碰撞偵測（2026-08-02）。
    # 第一版（07-31）只依折線走向把標籤放到線的另一側——解了「被折線壓過」，
    # 但解不了標籤彼此重疊、標籤壓到別的資料點。實機 p4 仍有兩個年份疊成「20**」。
    # ⚠ 所有資料點都是障礙物（不只自己那一個），放不下就不標。
    label_step = max(1, math.ceil(len(data) / 12))
    wanted = [i for i in range(len(data)) if i % label_step == 0 or i == len(data) - 1]
    # 🔴 I-4：先合併同座標的年份，再避讓。
    # ⚠ 順序不能反：先避讓會讓同一個點的多個年份各自找位置、彼此推開，
    # 看起來像好幾個不同的點——那正是實機 p3「2021 2011」並排的成因。
    items = merge_colocated_labels(
        [(coords[i][0], coords[i][1], str(data[i][0])) for i in wanted])
    obstacles = [(x, y, 4.0) for x, y in coords]
    for (_x, _y, text), position in zip(items, place_point_labels(items, obstacles)):
        if position is None:
            continue
        svg.append(f'<text x="{position[0]:.1f}" y="{position[1]:.1f}" font-size="{LABEL_FONT_SIZE}" '
                   f'fill="{COLOR_TEXT_SOFT}">{xml_text(text)}</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def merge_annual_trend_rows(
    application_rows: list[dict[str, Any]],
    publication_rows: list[dict[str, Any]],
) -> list[dict[str, int]]:
    """將申請年與授權公告年趨勢合併成前端表格可直接交叉對照的 rows。"""
    app = {
        year: count
        for row in application_rows
        if (year := _int_or_none(row.get("application_year"))) is not None
        if (count := _int_or_none(row.get("patent_count"))) is not None
    }
    pub = {
        year: count
        for row in publication_rows
        if (year := _int_or_none(row.get("授權公告年"))) is not None
        if (count := _int_or_none(row.get("patent_count"))) is not None
    }
    years = sorted(set(app) | set(pub))
    return [
        {
            "year": year,
            "application_count": app.get(year, 0),
            "授權公告件數": pub.get(year, 0),
        }
        for year in years
    ]


def render_chart_embed(file: str) -> str:
    """Generic embed: SVG/PNG as <img>, HTML as <iframe>."""
    lower = file.lower()
    if lower.endswith((".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return f'<img class="chart-media" src="{xml_text(file)}" alt="{xml_text(file)}" loading="lazy">'
    if lower.endswith((".html", ".htm")):
        return f'<iframe class="chart-media chart-frame" src="{xml_text(file)}" loading="lazy"></iframe>'
    return f'<a class="chart-fallback" href="{xml_text(file)}">{xml_text(file)}</a>'


# ── 排名長條色階（F-2＋W-2，2026-08-02 使用者選定「依數值連續深淺」）──
#
# 🔴 F-2：p14 的主長條原本是 `CBD5E1`（白底的淺灰藍），被 chart_recolor 歸進
# 「淺灰＝結構色」那一組轉成面板底色 `274A66`，對深空背景實測只有 **1.72**——
# 那根長條在簡報上幾乎不存在。⚠ `CBD5E1` 在排名圖裡是**資料**不是結構，
# 這是批 2「資料色與裝飾色分離」漏掉的一個。
#
# 🔴 W-2 硬約束：**最淺一階也要 ≥3.0**（WCAG 圖形元素門檻）。色階從這個下限
# 往上推，不是從主色往下淡——後者就是再做一次 F-2。
# 白底（網頁報表）實測對比：10.88／7.43／5.28／3.75／3.08，全數過關。
# 對應的深底階由 theme.json 的 chart_recolor 映射（對背景 9.24→3.53）。
RANKING_BAR_SCALE: tuple[str, ...] = ("#0A3A80", "#0B4FB8", "#1268D6", "#2E86E0", "#4A97E3")




def ranking_bar_color(value: float, max_value: float) -> str:
    """依「佔最大值的比例」挑色階；同數值必得同色。

    ⚠ 分階依**數值**不依名次：名次相鄰但件數差很多（13 vs 5）該有明顯色差，
    件數相同（5 vs 5）則必須同色——依名次上色會讓並列者看起來有高下之分。
    """
    if max_value <= 0:
        return RANKING_BAR_SCALE[-1]
    ratio = max(0.0, min(1.0, value / max_value))
    for index, threshold in enumerate((0.8, 0.6, 0.4, 0.2)):
        if ratio > threshold:
            return RANKING_BAR_SCALE[index]
    return RANKING_BAR_SCALE[-1]


def truncation_note(shown: int, total: int) -> str:
    """被截斷時的圖上註記；沒截斷回空字串。

    ⚠ 唯一來源：兩張排名圖各寫各的會漂移——實機 p14 有這行、p15 沒有，
    同一種圖兩套規則，讀者以為 p15 就是全部（F-12）。
    """
    if total <= shown:
        return ""
    return f"顯示前 {shown}/{total} 名，完整名單見附錄"


#: 列數少於 3 時的列高上限倍率。
#: ⚠ 與一般情況（4 倍）分開：1–2 列撐到 4 倍就變成色帶（H-6）。
SPARSE_ROW_CEILING_FACTOR = 2


def _fill_row_height(row_count: int, *, top: int, bottom: int,
                     base: int = CHART_ROW_HEIGHT, ceiling_factor: int = 4) -> int:
    """列高：列少時撐開填滿畫布，列多時維持基準值。

    🔴 G-7：列高固定 28px 時，1–2 列的圖只有 130–158px 高，放進 3.2in 的框
    空掉 37–48%。⚠ 那不是版型給太多空間，是圖本身太矮。

    ⚠ 兩端都要守：
    - 上限 `base × ceiling_factor`——再撐下去單列長條會變成一整塊色帶，不成圖。
    - 下限 `base`——列多時撐開會讓畫布爆高，整張圖反而被縮小（P-2 的老問題）。
    """
    if row_count <= 0:
        return base
    usable = CHART_CANVAS_MAX_HEIGHT - top - bottom
    # 🔴 H-6（2026-08-03 實機 p9）：CPC 四階只有 1 列，撐到 base×4＝112px 後
    # 那根長條橫貫全寬、粗到變成一整塊色帶，已經不像圖表了。
    # ⚠ 列數極少時**不追求填滿**：填滿是為了避免大片留白，但把單一長條撐成色帶
    # 是用一個可讀性問題換另一個。少列時上限收到 base×2，留白改由版型處理。
    factor = ceiling_factor if row_count >= 3 else SPARSE_ROW_CEILING_FACTOR
    return int(max(base, min(base * factor, usable // row_count)))


#: 標籤文字與畫布左緣之間的留白（px）。
#: 🔴 H-3（2026-08-03）：原本是寫在 `label_gutter` 算式裡的 `+ 24`，
#: 沒有名字也沒有測試——改動時無從得知它代表什麼、夠不夠。
#: 提高到 32 是因為實機仍被裁：估算再準也要留誤差空間（字型不同、字距不同）。
#: 標籤區右緣到文字右緣的距離（px）——文字 `text-anchor="end"` 就落在這裡。
#: 原本以字面 `left - 12` 散在五處渲染函式裡，改動時看不出它與 gutter 的關係。
LABEL_TEXT_OFFSET_PX = 12

#: 標籤區在「估算寬度」之外要多留的總量（px）＝文字偏移 ＋ 左側安全邊界。
#: 🔴 H-3（2026-08-03）：原本是算式裡的 `+ 24`，實機仍把 `A63B-021` 的開頭裁掉。
#: ⚠ 為什麼要留到 48：`_display_width` 是**估算**（SVG 沒有 text metrics），
#: 實測誤差可達 30px（估 259 而實際 >271）。字型 fallback、字距差異都會放大它。
#: 估準一點（0.55→0.62）與留夠邊界，兩件事都要做——只做前者仍會擦邊。
LABEL_GUTTER_PADDING_PX = 48


def label_gutter(labels: list[str], *, minimum: int = 180, maximum: int = 480) -> int:
    """列標籤區寬度——依**實際最長標籤**算，不寫死。

    🔴 G-3（2026-08-03 實機 p10）：「A63B-0022　心肺與協調訓練器械」開頭的字
    被畫布左緣裁掉。⚠ 這是修 F-3 時造成的——移除 `label[:42]` 硬切之後，
    技術名接上去讓標籤變長，但標籤區還是寫死 310px。
    截斷沒有消失，只是從「切字串」變成「被邊界裁掉」。

    ⚠ 上限存在的理由：標籤再長也不能把畫布撐爆，否則整張圖會被縮小（P-2）。
    真的超過上限時由呼叫端縮字或換行處理，不是靜靜切掉。
    """
    if not labels:
        return minimum
    widest = max(_display_width(text) for text in labels)
    return int(max(minimum, min(maximum,
                                widest * CHART_LABEL_PX + LABEL_GUTTER_PADDING_PX)))


def _display_width(text: str) -> float:
    """字串的顯示寬度，單位是「全形字」。

    🔴 H-3（2026-08-03 實機）：原本英數用 0.55，實測**低估**——
    `A63B-021` 這種大寫字母＋數字的分類碼，實際約 0.62 em／字元
    （大寫字母約 0.7、數字約 0.56，混排平均高於 0.55）。
    低估的症狀與「標籤區寫死太窄」一模一樣：開頭的字被畫布左緣裁掉，
    而 G-3 那輪只把寫死改成依內容算，沒驗算出來的值準不準。

    ⚠ 這是估算不是量測——SVG 沒有 text metrics，只能用係數逼近。
    因此 `LABEL_GUTTER_PADDING_PX` 要留誤差空間，兩者搭配才安全。
    """
    return sum(0.62 if ord(ch) < 0x2E80 else 1.0 for ch in str(text))


def nice_ticks(max_value: float, count: int = 5) -> list[int]:
    """回傳等差且好讀的座標軸刻度（含 0，最後一格不低於 max_value）。

    🔴 2026-08-02 實機：p2 縱軸印出 0／4／8／**11**／15、p4 印出 0／4／**9**／13／17。
    根因是刻度用 `max * i / (count-1)` 直接取整——間距忽 3 忽 4，讀者無法心算比例。

    步進限制在 1／2／2.5／5 的 10 次方倍，這就是「好讀」的定義；
    ⚠ 最後一格可能高於實際最大值（15 → 0/5/10/15/20），那是刻意的：
    寧可頂端留白，也不要為了頂到滿而用 3.75 這種步進。
    """
    span = max(float(max_value), 0.0)
    if span <= 0 or count < 2:
        return list(range(count))
    # ⚠ 資料量小於刻度數時每格給 1：算出來的步進會是 0.25 這種小數，
    # 取整後變成 0,0,0,1,1（重複且不等差）。件數是整數，刻度不該有小數。
    if span < count - 1:
        return list(range(int(math.ceil(span)) + 1))
    raw_step = span / (count - 1)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    for multiple in (1, 2, 2.5, 5, 10):
        step = multiple * magnitude
        if step >= raw_step:
            break
    return [int(round(step * i)) for i in range(count)]


def _load_ipc_tech_names() -> dict[str, dict[str, str]]:
    """載入 IPC/CPC → 技術意義對照（資料與程式分離，非工程師也改得動）。

    ⚠ 檔案缺失或壞掉時回空 dict 而非 raise：技術名是**加值**，
    缺了退回顯示代碼本身仍可讀；為了它讓整張報表產不出來是本末倒置。
    """
    import json

    path = Path(__file__).with_name("data") / "ipc_tech_names.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in (payload.get("codes") or {}).items() if isinstance(v, dict)}


IPC_TECH_NAMES: dict[str, dict[str, str]] = _load_ipc_tech_names()


def _normalize_class_code(code: str) -> str:
    """把分類代碼正規化成對照表的鍵。

    ⚠ IPC 寫 `A63B-021`、CPC 寫 `A63B-0021`——前導零位數不同，**卻是同一個主目**。
    不正規化就會變成同一分類在 IPC 頁與 CPC 頁顯示不同名字（或其中一頁查不到）。
    subclass（`A63B`）原樣；main group 統一成 `A63B/21`。
    """
    text = str(code or "").strip().upper()
    if "-" not in text:
        return text
    subclass, _, group = text.partition("-")
    digits = group.lstrip("0") or "0"
    return f"{subclass}/{digits}"


def tech_name(code: str) -> str:
    """分類代碼 → 濃縮技術短名；查不到回**代碼本身**。

    🔴 使用者：「IPC/CPC 沒有轉換成技術意義」——只給 `A63B-069` 讀者不知道那是什麼。
    ⚠ 查不到時不留空、不猜：顯示 `G05B` 讀者仍知道那是分類碼，空白只會像壞掉。
    """
    entry = IPC_TECH_NAMES.get(_normalize_class_code(code))
    return (entry or {}).get("short") or str(code or "")


DATA_COLUMN_LABELS: dict[str, str] = {
    "patent_count": "專利件數",
    "year": "年份",
    "application_count": "申請件數",
    "授權公告件數": "授權公告件數",
    "applicant_count": "申請人家數",
    "application_year": "申請年份",
    "授權公告年": "授權公告年",
    "applicant_display_name": "申請人",
    "current_assignee_display_name": "專利權人",
    "recent_assignee_display_name": "最新受讓人",
    # ⚠ 複數那個是 string_agg 的輸出別名，與單數欄不同名；漏登記時表頭會直接印
    # `recent_assignee_display_names`（2026-07-31 實機 PPT 附錄 2 抓到）。
    "recent_assignee_display_names": "最新受讓人名單",
    "inventor_count": "發明人數",
    "family_size": "專利家族規模",
    "ipc_main_group_symbol": "IPC 主群組",
    "cpc_main_group_symbol": "CPC 主群組",
    "jurisdiction": "專利局",
    "country": "國家",
    "applicant_country": "申請人國籍",
    "pub_date": "公開日",
    "topic_code": "主題代碼",
    "label": "主題標籤",
    "source_field": "來源欄位",
    "top_applicants": "前三大申請人",
    "quadrant": "象限",
    "leading_applicants": "龍頭公司",
    "top3_share": "前三大占比(%)",
    "max_share": "最大一家(%)",
    "acquired_count": "受讓取得",
    "leading_applicant_count": "龍頭涉入(家)",
    "leading_applicants_involved": "龍頭涉入名單",
    "doc_count": "專利件數",
    "applicant_names": "申請人",
    "top3_applicants": "前三大申請人",
    "patent_count_median": "專利件數中位數",
    "applicant_count_median": "申請人家數中位數",
    # 技術狀態五類（2026-08-02 定案）：狀態說「是什麼型態」，意義說「所以呢」。
    # 只有狀態沒有意義時，讀者仍得自己翻譯「競爭集中技術」代表什麼。
    "status": "技術狀態",
    # 功效列專用：這個功效是用哪些技術做出來的（2026-08-03 使用者定案）。
    "tech_means": "主要技術手段",
    "status_meaning": "意義",
    # ⚠ 加欄位就要同時登記顯示規則——不登記就會以英文欄名印給讀者看
    # （批 1 修過 recent_assignee_display_names，2026-08-03 我在加這幾欄時又犯一次）。
    "representative": "代表專利",
}


def _read_narratives(run_dir: Path, version: str) -> dict[str, Any]:
    """Read narratives.json; return dict keyed by report name or empty on failure."""
    nf = run_dir / "narratives.json"
    if not nf.exists():
        return {}
    import json
    try:
        narr = json.loads(nf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if narr.get("based_on_version") != version:
        return {"_expired": True}
    return narr.get("reports", {})


# 數據卡顯示時排除的欄（rows 本身保留該鍵供分段/入庫；只影響顯示）。
# cluster_topic_table：source_field 原始欄名不出現在使用者介面（2026-07-21 定案，
# 技術/功效已由統計表分段標題表達）。
DATA_TABLE_EXCLUDED_COLUMNS: dict[str, tuple[str, ...]] = {
    # 2026-07-29 使用者定案：
    #   topic_code  → 「機制能識別就好，表格和報告不用顯示」（資料仍帶著，供合併/拆分識別）
    #   leading_*   → 「有前三大申請人好像就不用龍頭涉入了」（改以集中度兩欄表達競爭結構）
    #   狀態的中間量 → 過程不是結論。recent_count／share_*／concentration_* 是
    #                   算出狀態用的，印在表上就是又一次「把程式中間值給讀者看」
    #                   （批 1 修過同型問題）。資料仍在 rows，供驗證與下游使用。
    "cluster_topic_table": (
        "source_field", "topic_code",
        "leading_applicant_count", "leading_applicants_involved",
        "recent_count", "early_count", "recent_applicants", "early_applicants",
        "share_recent", "share_early", "concentration_recent", "concentration_early",
        # 「意義」是 status 的固定對照，逐列重複一次很佔寬度；改由頁尾統一說明。
        "status_meaning",
        # 🔴 2026-08-03 欄位精簡（使用者：「11 欄附錄那裡也放不下，勢必要精簡欄位」）：
        # max_share 與 top_applicants 重複——後者的第一筆就是最大一家的件數與名字，
        # 且帶了「是誰」這個 max_share 沒有的資訊。留資訊多的那個。
        "max_share",
    ),
    # recent_assignee_count → 使用者：「這欄可以不用，後面欄都列出公司了」。
    # ⚠ 只排除**顯示**，資料仍在 rows——applicant_ranking 的圖表用它當
    # segment_key 畫藍色區段（轉出件數），移掉資料會讓圖表退化成單色長條。
    # 使用者定案：「欄位移除，圖表保留分段」。
    "applicant_ranking": ("recent_assignee_count",),
}

# 總計列可加總的欄（加總有意義＝件數類）；其餘一律「—」——applicant_count 跨主題
# distinct 不可加、龍頭涉入(家) 是各主題自己的 distinct 數、年份加總無意義（2026-07-21）。
# 表格欄位顯示優先序（2026-08-03）。欄位放不下時砍尾巴，不是砍中間。
# ⚠ 上一輪 `status`（技術狀態）排在第 7 位、被 max_columns=6 擋掉——
# 這一輪的重點功能一格都沒顯示出來。順序＝重要性，識別與量級在前、佐證在後。
DATA_TABLE_PRIORITY_COLUMNS: dict[str, tuple[str, ...]] = {
    "cluster_topic_table": (
        "label",            # 這是哪個主題
        "patent_count",     # 多大
        "applicant_count",  # 多少人在做
        "status",           # 什麼型態  ← 技術列
        "tech_means",       # 用什麼做的 ← 功效列（與 status 互斥，各通道各有一個）
        "top3_share",       # 多集中
        "top_applicants",   # 誰在做
        "representative",   # 證據
    ),
}


DATA_TABLE_SUMMABLE_COLUMNS = ("patent_count", "doc_count", "recent_assignee_count")


def _humanize_cell(value: Any) -> str:
    """數據卡儲存格人類化（2026-07-21 使用者回饋：嚴禁 raw repr）。

    list[dict 含 name/count]→「名稱 數字」分號連接；list[str]→頓號連接；
    空 list／None／空 dict→「—」；dict→「key: value」逗號連接（保底）。
    """
    if value is None or (isinstance(value, (list, dict)) and not value):
        return "—"
    if isinstance(value, list):
        if all(isinstance(item, dict) for item in value):
            if all("name" in item for item in value):
                return "；".join(
                    f'{item["name"]} {item["count"]}' if "count" in item else str(item["name"])
                    for item in value
                )
            return "；".join(_humanize_cell(item) for item in value)
        return "、".join(str(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in value.items())
    return str(value)


def table_display_spec(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """report_data.json 的 `table_display` 區塊——表格呈現規格的唯一來源。

    三樣東西一起走這個區塊，因為它們是同一件事的三面：欄要不要顯示
    （`excluded_columns`）、欄頭寫什麼（`column_labels`）、儲存格的值長什麼樣
    （`display_rows`）。

    ⚠ 為什麼值也要由引擎輸出：`top_applicants` 這類欄的值是物件陣列，呈現規則在
    `_humanize_cell`。組版 skill 會被 Installer 打包到使用者電腦、不得 import 本模組，
    只能走檔案傳遞；讓 PPT 端自己再寫一份轉換，就是本 repo 已重演多次的
    「同一規則兩處落點各自漂移」。

    `display_rows` 只收**含物件值**的報表：純量報表原樣就能印，多存一份只是讓
    report_data.json 白白膨脹一倍。
    """
    display_rows: dict[str, list[dict[str, str]]] = {}
    for name, report in reports.items():
        rows = report.get("rows")
        if not isinstance(rows, list) or not rows:
            continue
        if not any(isinstance(value, (list, dict))
                   for row in rows if isinstance(row, dict)
                   for value in row.values()):
            continue
        display_rows[name] = [
            {str(column): _humanize_cell(value) for column, value in row.items()}
            for row in rows
        ]
    return {
        "column_labels": dict(DATA_COLUMN_LABELS),
        "excluded_columns": {
            name: list(columns) for name, columns in DATA_TABLE_EXCLUDED_COLUMNS.items()
        },
        # 欄位放不下時砍尾巴不砍中間——組版端要讀得到這個順序，
        # 否則「技術狀態」又會像上一輪那樣被卡在 max_columns 之外（G-2）。
        "priority_columns": {
            name: list(columns) for name, columns in DATA_TABLE_PRIORITY_COLUMNS.items()
        },
        "display_rows": display_rows,
        # 編碼說明沿用同一條傳遞通道，不另開新鍵——同一件事（「組版端需要知道
        # 引擎怎麼畫的」）不該有兩個落點。
        "encoding_notes": dict(CHART_ENCODING_NOTES),
    }


def _data_table_html(rows: list[dict[str, Any]], report_name: str) -> str:
    """數據區：最多 20 筆＋總計列；不提供全量展開（2026-07-21 使用者補充——
    不讓人看百筆數據），超出只註記共幾列；完整 rows 由 DB／report_data.json 保存。"""
    if not rows:
        return '<p class="data-empty">無資料</p>'
    excluded = DATA_TABLE_EXCLUDED_COLUMNS.get(report_name, ())
    columns = [c for c in rows[0].keys() if c not in excluded]
    header = "".join(f"<th>{xml_text(DATA_COLUMN_LABELS.get(c, c))}</th>" for c in columns)
    body_rows = []
    for r in rows[:20]:
        cells = "".join(f"<td>{xml_text(_humanize_cell(r.get(c, '')))}</td>" for c in columns)
        body_rows.append(f"<tr>{cells}</tr>")
    # Totals row（class 放 td：列本身維持素 <tr>，與一般資料列同構）；
    # 只對加總有意義的欄出值，其餘「—」避免誤導。
    total_cells = []
    for c in columns:
        if c in DATA_TABLE_SUMMABLE_COLUMNS and any(str(r.get(c, "")).isdigit() for r in rows):
            total = sum(int(r.get(c, 0)) for r in rows if str(r.get(c, "")).isdigit())
            total_cells.append(f'<td class="totals-cell"><strong>{total}</strong></td>')
        else:
            total_cells.append('<td class="totals-cell"><strong>—</strong></td>')
    body_rows.append(f"<tr>{''.join(total_cells)}</tr>")
    table = f'<table><thead><tr>{header}</tr></thead><tbody>{"".join(body_rows)}</tbody></table>'
    if len(rows) > 20:
        # 2026-07-21 定案修正：排名類「保存」也只留前 20（長尾不落庫），完整可由引擎重算
        table += f'<p class="data-note">顯示前 20 列｜總列數 {len(rows)}（入庫同前 20，完整可重算）</p>'
    return f'<div class="data-table-wrap">{table}</div>'


def _section_report_name(section: dict[str, Any]) -> str:
    """卡片對應的 report key（解讀 narratives 與數據 rows 查找共用）：
    有 report_key 用之，否則以第一個 variant 檔名去副檔名。"""
    variants = section.get("variants", [])
    fallback = variants[0]["file"].replace(".svg", "").replace(".html", "") if variants else ""
    return section.get("report_key", fallback)


# sections 持久化欄位白名單（report_data.json["sections"]，--refresh-index 重建 index 用；
# 只收可 JSON 序列化的顯示欄位）。
# 圖表編碼說明（「這張圖的長度／位置／顏色各代表什麼」）的**唯一來源**。
#
# 🔴 2026-07-31：這份說明原本寫在組版端（build_ppt.ENCODING_NOTES），與畫圖的這裡
# 各自演進，實測三張對不上——`annual_trend` 是折線卻寫「條長」、`application_growth`
# 縱軸是年增率 % 卻寫「件數」、`lifecycle` 橫軸是申請人家數卻寫「申請年」。
# ⚠ 只有畫圖的這一端知道自己畫了什麼，故說明必須從這裡輸出，組版端讀取即可。
#
# 🔴 2026-08-02：搬過來之後**又寫錯一次**——`lifecycle` 改寫成「連線＝同一技術群」，
# 但同檔 `render_lifecycle_chart` 的 docstring 就寫著「依年份連線」，SVG 副題也是
# `connected by year`。搬家沒讓說明變正確，只是換了個地方憑印象寫。
# ⚠ 新增或修改任何一條前，**先去看那張圖實際怎麼畫**，不要照著鍵名想像。
CHART_ENCODING_NOTES: dict[str, str] = {
    "application_trend": "折線＝當年件數｜橫軸＝年份｜兩線分別為申請與授權公告",
    "publication_trend": "折線＝當年公告件數｜橫軸＝公告年",
    "country_distribution": "條長＝佔全體比例（軌道＝100%）｜數值＝件數與佔比",
    "jurisdiction_distribution": "條長＝件數｜排序＝件數由高至低",
    # ⚠ 拆頁後每頁只有一個階層，說明不得再提「左右」（階層寫在圖表標題裡）。
    "ipc_main_distribution": "條長＝件數｜縱軸＝分類代碼｜本頁為單一階層",
    "cpc_main_distribution": "條長＝件數｜縱軸＝分類代碼｜本頁為單一階層",
    "opportunity_quadrant": "橫軸＝申請人家數｜縱軸＝專利件數｜點＝技術主題",
    "cluster_topic_table": "條長＝主題件數｜家數＝投入該主題的申請人數",
    "applicant_ranking": "條長＝件數｜排序＝件數由高至低",
    "owner_ranking": "條長＝件數｜排序＝件數由高至低",
    "applicant_country_distribution": "格值＝件數｜列＝申請人、欄＝受理國",
    "applicant_year_matrix": "泡泡大小與顏色＝件數｜列＝申請人、欄＝申請年",
    "owner_year_matrix": "泡泡大小與顏色＝件數｜列＝專利權人、欄＝申請年",
    "lifecycle": "橫軸＝申請人家數｜縱軸＝專利件數｜連線＝依年份先後",
    "family_country_layout": "條長＝存活家族數｜分組＝受理國",
    "family_quality_detail": "條長＝家族成員件數｜分組＝家族",
}


# 卡片變體 → 解讀掛點的對照。**唯一定義處**。
#
# 🔴 2026-08-03：只列「兩邊不同名」的，同名不必列。
# 兩個 key 空間會不同名是歷史造成的：
# - `annual_trend` 是一張圖合併兩份報表（申請＋公告），解讀掛在 `application_trend`
# - 機會板的解讀由 ai:narrative 掛在 `opportunity_quadrant`，但圖排在分群卡裡
#
# ⚠ 這份對照原本只寫在 PPT 端（`build_ppt.NARRATIVE_ALIASES`），網頁端沒有，
# 於是同樣三張卡在 PPT 有解讀、在網頁顯示「AI 解讀尚未產生」。
# **誰把解讀掛上這張卡，誰才知道掛在哪**——所以定義處在引擎，消費端只讀不推導。
SECTION_NARRATIVE_SOURCES: dict[tuple[str, str], str] = {
    ("annual_trend", "default"): "application_trend:default",
    ("cluster_topic_table", "opportunity_tech"): "opportunity_quadrant:tech",
    ("cluster_topic_table", "opportunity_effect"): "opportunity_quadrant:effect",
}


def variant_narrative_ref(report_key: str, variant_key: str) -> str:
    """這個卡片變體的解讀掛在 narratives 的哪裡，回傳 `"key:variant"`。

    三段規則：① 顯式對照 ② 鍵帶層級尾巴（`_L<n>`）時退基底鍵 ③ 其餘原樣。
    ⚠ 第 ② 段是因為沒寫 report_key 的 IPC／CPC 卡由檔名 fallback 帶了 `_L4`，
    而 narratives 契約鍵不帶層級。
    """
    explicit = SECTION_NARRATIVE_SOURCES.get((report_key, variant_key))
    if explicit:
        return explicit
    base, sep, tail = report_key.rpartition("_L")
    if sep and tail.isdigit():
        report_key = base
    return f"{report_key}:{variant_key}"


SECTION_PERSIST_KEYS = (
    "title", "report_key", "variants", "more_variants", "more_label", "note", "stacked", "links",
)


def persistable_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 sections 過濾成可序列化的持久化形狀。

    ⚠ 兩個欄位會**寫實**而非原樣搬運，讓消費端（網頁 API、PPT 組版）不必各自推導：
    - `report_key`：沒寫的用第一個圖檔主檔名補上
    - 每個變體的 `narrative_key`：解讀實際掛在 narratives 的哪個 `key:variant`
    其餘欄位缺就是缺，不補不猜。
    """
    out: list[dict[str, Any]] = []
    for section in sections:
        item = {key: section[key] for key in SECTION_PERSIST_KEYS if key in section}
        report_key = _section_report_name(section)
        item["report_key"] = report_key
        for field in ("variants", "more_variants"):
            if field not in item:
                continue
            item[field] = [
                {**variant,
                 "narrative_key": variant_narrative_ref(report_key, variant.get("variant_key", "default"))}
                for variant in item[field]
            ]
        out.append(item)
    return out


def refresh_index(run_dir: Path) -> dict[str, Any]:
    """從 run_dir/report_data.json["sections"] 重建 index.html（解讀回填後重渲染）。

    render_index 內部會讀同目錄 narratives.json：版本相符即嵌入解讀、不符顯示
    「解讀版本過期」。舊 run（無 sections 鍵）明確報錯，不做推測重建。
    回傳統計：sections 數、有解讀數、缺漏 report_key 清單、是否過期。
    """
    run_dir = Path(run_dir)
    rd_path = run_dir / "report_data.json"
    if not rd_path.exists():
        raise FileNotFoundError(f"{rd_path} 不存在（不是有效的報表輸出目錄）")
    rd = json.loads(rd_path.read_text(encoding="utf-8"))
    sections = rd.get("sections")
    if sections is None:
        raise ValueError(
            "report_data.json 缺 'sections' 鍵（舊版產出）：請以新版 run_chart_trial 重產報表後再 refresh，"
            "不支援對舊 run 推測重建 sections"
        )
    parameters = rd.get("parameters", {})
    render_index(
        run_dir / "index.html",
        sections,
        meta={
            "ranking_limit": parameters.get("ranking_limit", ""),
            "ipc_levels": " ".join(str(v) for v in parameters.get("ipc_levels", [])),
            "cpc_levels": " ".join(str(v) for v in parameters.get("cpc_levels", [])),
        },
    )
    # 統計解讀覆蓋：按變體計（v2 契約 each variant = one narrative）
    narratives = _read_narratives(run_dir, run_dir.name)
    expired = bool(narratives.pop("_expired", False))
    total_variants = 0
    narrated_variants = 0
    pending_variants: list[str] = []
    for s in sections:
        report_key = _section_report_name(s)
        all_variants = list(s.get("variants", [])) + list(s.get("more_variants", []))
        for v in all_variants:
            total_variants += 1
            vk = v.get("variant_key", "default")
            text = None if expired else _variant_narrative_text(narratives, report_key, vk)
            if text:
                narrated_variants += 1
            else:
                pending_variants.append(f"{report_key}:{vk}")
    return {
        "status": "ok",
        "run_dir": str(run_dir),
        "sections": len(sections),
        "variants_total": total_variants,
        "narrated": narrated_variants,
        "pending": pending_variants,
        "narratives_expired": expired,
    }


def _variant_narrative_text(
    narratives: dict[str, Any], report_key: str, variant_key: str
) -> str | None:
    """取這個卡片變體的解讀文字。掛點一律問 `variant_narrative_ref`（唯一來源）。

    ⚠ 不要改成「整張卡取一個 entry、變體再從裡面挑」——`annual_trend` 與機會板
    兩個變體的解讀掛在**別的 report key** 底下，整卡共用 entry 就永遠查不到。

    ⚠ 精確鍵優先於對照：narratives 真的有這個鍵時就用它，別繞去對照表指的地方。
    對照是「本來查不到才要的橋」，不是覆寫。
    """
    direct = _narrative_text(narratives.get(report_key) or {}, variant_key)
    if direct:
        return direct
    ref = variant_narrative_ref(report_key, variant_key)
    key, _, variant = ref.rpartition(":")
    return _narrative_text(narratives.get(key) or {}, variant)


def _narrative_text(entry: dict[str, Any] | None, variant_key: str) -> str | None:
    if not entry:
        return None
    if "variants" in entry:
        v = entry["variants"].get(variant_key)
        return v.get("text") if v and v.get("text") else None
    if entry.get("text"):
        return entry.get("text")
    return None


def render_index(path: Path, sections: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> None:
    """Card-style report index with data table, chart, and explanation.

    Sections order is fixed by SECTION_SPECS. Each card shows:
    1. Data table (first 20 rows + totals, expandable)
    2. Chart (SVG embed) + per-variant explanation
    3. Explanation (from narratives.json, or placeholder)

    v2 narratives: narratives[report_name]["variants"][variant_key]["text"].
    v1 backward compat: direct "text" serves as default for all variants.
    """
    meta = meta or {}
    run_dir = path.parent
    version = run_dir.name if run_dir.name else ""
    narratives = _read_narratives(run_dir, version)
    narr_expired = narratives.pop("_expired", False)

    blocks: list[str] = []
    for index, section in enumerate(sections):
        variants = section.get("variants", [])
        if not variants:
            continue
        title = section.get("title", "")
        report_name = _section_report_name(section)

        # 1. Data table
        report_data_json = run_dir / "report_data.json"
        rows = []
        if report_data_json.exists():
            import json
            try:
                rd = json.loads(report_data_json.read_text(encoding="utf-8"))
                report_rows = rd.get("reports", {}).get(report_name, {}).get("rows", [])
                if not report_rows:
                    report_rows = rd.get("family_reports", {}).get(report_name, {}).get("rows", [])
                if not report_rows:
                    chart_rows_entry = rd.get("chart_rows", {}).get(report_name, [])
                    if isinstance(chart_rows_entry, list):
                        report_rows = chart_rows_entry
                rows = report_rows
            except (json.JSONDecodeError, OSError):
                rows = []

        data_html = _data_table_html(rows, report_name)

        # 2. Chart panels + per-variant explanation
        group_id = f"sec{index}"
        buttons = ""
        if len(variants) > 1:
            btns = "".join(
                f'<button type="button" class="toggle-btn{" active" if v_i == 0 else ""}" '
                f'data-group="{group_id}" data-target="{group_id}-{v_i}">{xml_text(variant["label"])}</button>'
                for v_i, variant in enumerate(variants)
            )
            buttons = f'<div class="toggle-bar">{btns}</div>'

        def _panel_narrative(variant: dict[str, Any]) -> str:
            vk = variant.get("variant_key", "default")
            if narr_expired:
                return '<div class="explanation expired">⚠️ 解讀版本過期</div>'
            text = _variant_narrative_text(narratives, report_name, vk)
            if text:
                return f'<div class="explanation"><p>{xml_text(text)}</p></div>'
            return '<div class="explanation pending">⏳ 待解讀</div>'

        panels = "".join(
            f'<div class="chart-panel" id="{group_id}-{v_i}"{"" if v_i == 0 else " hidden"}>'
            f'{render_chart_embed(variant["file"])}'
            f'{_panel_narrative(variant)}</div>'
            for v_i, variant in enumerate(variants)
        )
        more_variants = section.get("more_variants", [])
        more_html = ""
        if more_variants:
            more_panels = "".join(
                f'<div class="chart-panel" id="{group_id}-more-{v_i}">'
                f'{render_chart_embed(variant["file"])}'
                f'{_panel_narrative(variant)}</div>'
                for v_i, variant in enumerate(more_variants)
            )
            more_label = xml_text(section.get("more_label", "＋查看全部（第 11～20 名）"))
            more_html = (
                f'<button type="button" class="expand-btn" data-expand-target="{group_id}-more" '
                f'data-label="{more_label}">{more_label}</button>'
                f'<div class="chart-more" id="{group_id}-more" hidden>{more_panels}</div>'
            )

        links = section.get("links", [])
        link_html = ""
        if links:
            items = " ".join(
                f'<a class="section-link" href="{xml_text(link["file"])}" target="_blank" rel="noopener">{xml_text(link["label"])} ↗</a>'
                for link in links
            )
            link_html = f'<div class="section-links">{items}</div>'
        note = f'<p class="section-note">{xml_text(section["note"])}</p>' if section.get("note") else ""

        blocks.append(
            f'<section class="report-section">'
            f'<div class="section-head"><h2>{xml_text(title)}</h2>{link_html}</div>'
            f'{note}'
            f'<div class="card-data">{data_html}</div>'
            f'{buttons}<div class="chart-stage">{panels}{more_html}</div>'
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
    .data-table-wrap {{ overflow-x: auto; margin: 0 0 14px; }}
    .data-table-wrap table {{ border-collapse: collapse; font-size: 12px; width: 100%; }}
    .data-table-wrap th {{ background: #F1F5F9; padding: 6px 8px; text-align: left; font-weight: 600; white-space: nowrap; border: 1px solid #E2E8F0; }}
    .data-table-wrap td {{ padding: 4px 8px; border: 1px solid #F1F5F9; white-space: nowrap; }}
    .data-table-wrap td.totals-cell {{ border-top: 2px solid #CBD5E1; font-weight: 600; background: #F8FAFC; }}
    .data-table-wrap details {{ margin-top: 8px; }}
    .data-table-wrap summary {{ cursor: pointer; font-size: 13px; color: #2563EB; }}
    .toggle-bar {{ display: inline-flex; gap: 4px; padding: 4px; background: #F1F5F9; border-radius: 9px; margin: 0 0 14px; }}
    .toggle-btn {{ border: none; background: transparent; color: #334155; font-size: 14px; font-weight: 600; padding: 7px 16px; border-radius: 7px; cursor: pointer; }}
    .toggle-btn:hover {{ background: #E2E8F0; }}
    .toggle-btn.active {{ background: #2563EB; color: #FFFFFF; }}
    .expand-btn {{ border: 1px solid #CBD5E1; background: #FFFFFF; color: #2563EB; font-size: 14px; font-weight: 600; padding: 8px 14px; border-radius: 8px; cursor: pointer; margin: 12px 0; }}
    .expand-btn:hover {{ background: #EFF6FF; }}
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
    document.querySelectorAll('.expand-btn').forEach(function (btn) {{
      btn.addEventListener('click', function () {{
        var target = document.getElementById(btn.getAttribute('data-expand-target'));
        if (!target) return;
        var show = target.hidden;
        target.hidden = !show;
        btn.textContent = show ? '－收合' : btn.getAttribute('data-label');
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

# 圖表可讀的列數上限。⚠ 不是資料上限——附錄表格仍列完整名單。
# 每列 50px，20 列＝1124px，塞進簡報圖框縮到 0.37 倍、字剩 5px 不可讀。
# 12 列＝702px，縮放約 0.58 倍（+57%），公司名讀得出來。
CHART_ROW_LIMIT = 12

# ---------------------------------------------------------------------------
# 入庫截取（2026-07-21 定案修正）：排名類「保存」也只留前 20、年度序列只留最新
# 25 年——長尾不落庫（report_data.json／analysis_outputs 不膨脹），完整排名／
# 序列可隨時由引擎自 raw/core 重算；聚合摘要（總計、中位數、rows_total）照存。
# 例外：正式主題相關數據（cluster_topic_table 等）不截。
# ---------------------------------------------------------------------------
PERSIST_RANKING_ROWS = 20   # 排名類入庫列數上限
PERSIST_YEAR_SPAN = 25      # 年度序列入庫年份數上限（取最新）

# 排名類報表：入庫 rows 截前 20（含 IPC/CPC 分布與公司×國家交叉）
PERSIST_TOP20_REPORTS = (
    "applicant_ranking", "owner_ranking",
    "applicant_country_distribution", "ipc_main_distribution", "cpc_main_distribution",
)
# 年度序列報表：入庫只留最新 25 年（value＝該報表的年份欄位名）
PERSIST_YEAR_KEYS = {
    "application_trend": "application_year",
    "publication_trend": "授權公告年",
    "applicant_year_matrix": "application_year",
    "owner_year_matrix": "application_year",
}
# chart_rows 中需截前 20 的鍵（IPC/CPC 各階聚合列）
_CHART_ROWS_TOP20_PREFIXES = ("ipc_main_distribution_L", "cpc_main_distribution_L")


def _latest_years_rows(rows: list[dict[str, Any]], year_key: str, span: int = PERSIST_YEAR_SPAN) -> list[dict[str, Any]]:
    """保留最新 span 個年份的 rows（年度序列入庫截取用；年份缺值列一併剔除）。"""
    years = sorted(
        {
            year
            for r in rows
            if (year := _int_or_none(r.get(year_key))) is not None
        }
    )
    keep = set(years[-span:])
    return [r for r in rows if (year := _int_or_none(r.get(year_key))) is not None and year in keep]


def truncate_rows_for_persistence(
    reports: dict[str, dict[str, Any]],
    chart_rows: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, int]]:
    """report_data.json 落檔前的入庫截取；不改動輸入（圖表已渲染完，只影響保存）。

    回傳 (reports_out, chart_rows_out, chart_rows_total)：
    - 排名類報表 rows[:20]、年度序列報表留最新 25 年，皆附 rows_total（截取前總數）。
    - chart_rows：IPC/CPC 各階前 20、年增率序列最新 25 年，截取前總數收進 chart_rows_total；
      主題類（cluster_topic_table／機會板／痛點板）與其餘鍵原樣保存。
    """
    reports_out: dict[str, dict[str, Any]] = {}
    for name, report in reports.items():
        rows = report.get("rows", [])
        if name in PERSIST_TOP20_REPORTS:
            reports_out[name] = {**report, "rows": rows[:PERSIST_RANKING_ROWS], "rows_total": len(rows)}
        elif name in PERSIST_YEAR_KEYS:
            reports_out[name] = {
                **report,
                "rows": _latest_years_rows(rows, PERSIST_YEAR_KEYS[name]),
                "rows_total": len(rows),
            }
        else:
            reports_out[name] = report

    chart_rows_out: dict[str, Any] = {}
    chart_rows_total: dict[str, int] = {}
    for key, value in chart_rows.items():
        if isinstance(value, list) and key.startswith(_CHART_ROWS_TOP20_PREFIXES):
            chart_rows_total[key] = len(value)
            chart_rows_out[key] = value[:PERSIST_RANKING_ROWS]
        else:
            chart_rows_out[key] = value
    return reports_out, chart_rows_out, chart_rows_total


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
    # 分群分析資料（由呼叫端注入，不含 DB SQL）。
    # 結構：{topics, assignments, normalized_applicants, pain_data?, top_applicants_ws?}
    cluster_data: dict[str, Any] | None = None
    # 分群報表的 report 形狀（cluster_topic_table／opportunity_quadrant），由
    # _build_cluster_analytics_section 填入、組檔時顯式併進 report_data["reports"]。
    # ⚠ 2026-07-30 實機：這兩份不是 SQL 報表、進不了 fetched → reports bucket 一直
    # 缺它們 → build_ppt 判無資料跳頁（PPT 只剩 11 頁）。SVG 有產、資料卻不在，
    # 消費端無從發現。
    cluster_reports: dict[str, dict[str, Any]] = field(default_factory=dict)
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
    ctx.chart_rows["annual_trend"] = merge_annual_trend_rows(application["rows"], publication["rows"])
    # report_key 指向 chart_rows.annual_trend，讓表格可同列對照申請年與授權公告年；
    # 圖檔仍由 application_trend + publication_trend 兩份報表共同產生。
    ctx.sections.append({
        "title": trend_title,
        "report_key": "annual_trend",
        "variants": [{"label": "Trend", "file": "annual_trend.svg", "variant_key": "default"}],
    })


def _build_country_map_section(ctx: ChartContext) -> None:
    """專利受理局分布：第一版改用長條圖，口徑仍走 country_code。"""
    report = ctx.report("country_distribution")
    render_bar_chart(
        ctx.run_dir / "jurisdiction_distribution.svg",
        report["label_zh"],
        report["rows"],
        "country_code",
    )
    ctx.chart_rows["jurisdiction_distribution"] = report["rows"]
    # 檔名 jurisdiction_distribution ≠ 報表鍵 country_distribution，須顯式宣告查找鍵。
    ctx.sections.append({
        "title": report["label_zh"],
        "report_key": "country_distribution",
        "variants": [{"label": "Bar", "file": "jurisdiction_distribution.svg", "variant_key": "default"}],
        "note": "專利受理局分布以 country_code group by，與全庫或 workspace patent_ids 快照共用同一報表定義。",
    })


def family_quality_note(quality_rows: list[dict[str, Any]]) -> str:
    """家族資料可信度摘要——**只講有事的**，掛在卡片 note 上。

    2026-07-28 使用者指出：「就算做成卡片，內容跟 json 一樣，那還是不會被看」。
    原句把六個指標並列（實測 52 個家族只有 3 個不完整，其餘四項全為 0），
    異常被一串 0 淹沒；把整包明細換個位置呈現也只是換地方繼續不被看。

    改為：異常項才列出、附分母，讓使用者不必主動點就知道要不要理會；
    完整明細仍留 family_quality.json（要追細節時才點）。
    ⚠ 全部正常時明講「無異常」——沉默無法區分「沒問題」與「沒檢查」。
    """
    total = len(quality_rows)
    if not total:
        return "家族品質：本次無家族資料可核對。"
    checks = (
        ("不完整家族", sum(1 for q in quality_rows if q.get("family_incomplete")), "家族"),
        ("無同族ID（家族數為近似值）",
         sum(1 for q in quality_rows if q.get("is_surrogate_family")), "家族"),
        ("狀態未知", sum(int(q.get("unknown_status_count") or 0) for q in quality_rows), "件"),
        ("審查中", sum(int(q.get("pending_status_count") or 0) for q in quality_rows), "件"),
        ("EP 生效程序進行中", sum(int(q.get("ep_in_transition_count") or 0) for q in quality_rows), "件"),
        ("EPC 欄缺值", sum(int(q.get("ep_missing_epc_count") or 0) for q in quality_rows), "件"),
    )
    flagged = [f"{name} {count} {unit}" for name, count, unit in checks if count]
    if not flagged:
        return f"家族品質：{total} 個家族均完整、狀態明確，無異常。"
    return (
        f"⚠ 家族品質提醒（共 {total} 個家族）：" + "、".join(flagged)
        + "。引用佈局數字前請留意；明細見 family_quality.json。"
    )


def _build_family_layout_section(ctx: ChartContext) -> None:
    """國家佈局（現有保護口徑）：家族×國家報表，第一版用長條圖。

    filters/快照經引擎轉譯成「選中專利所屬家族」的家族集合，佈局計入家族
    全體成員；不帶篩選＝全庫。
    """
    family_report = ctx.report("family_country_layout")
    quality_report = ctx.report("family_quality_detail")
    quality_rows = quality_report["rows"]
    family_notes = [
        "計數單位是「同族（發明）」：group by 同族ID，做到申請國（受理局）層級；EP 以區域標示呈現，暫不展開生效國。",
        family_quality_note(quality_rows),
    ]
    if ctx.analysis_id is not None or ctx.filters:
        family_notes.append("家族集合依篩選／快照圈定；佈局計入家族全體成員，可能含篩選外的國家。")
    render_bar_chart(
        ctx.run_dir / "family_country_distribution.svg",
        family_report["label_zh"],
        family_report["rows"],
        "country_code",
    )
    ctx.chart_rows["family_country_distribution"] = family_report["rows"]
    # 檔名 family_country_distribution ≠ 報表鍵 family_country_layout，須顯式宣告查找鍵。
    # 數據表取家族×國家佈局（本卡主體）；家族品質明細另以 links 的 JSON 提供。
    ctx.sections.append({
        "title": family_report["label_zh"],
        "report_key": "family_country_layout",
        "variants": [{"label": "Bar", "file": "family_country_distribution.svg", "variant_key": "default"}],
        "links": [{"label": "家族品質明細 JSON", "file": "family_quality.json"}],
        "note": " ".join(family_notes),
    })
    write_json(ctx.run_dir / "family_quality.json", {"report": quality_report["report_name"], "rows": quality_rows})

def _build_classification_section(
    ctx: ChartContext, report_key: str, source_column: str, levels: tuple[int, ...]
) -> None:
    """IPC/CPC 分布共用：每階一個 variant，L4/L5 切換鈕對照（2026-07-21 三次修正定版——
    兩階對照是核心價值；「不收合」只指不用查看全部式展開鈕，不禁 toggle）；每階各截前 20。"""
    report = ctx.report(report_key)
    variants: list[dict[str, str]] = []
    for level in levels:
        rows = collapse_classification_rows(report["rows"], source_column, level)
        chart_key = f"{report_key}_L{level}"
        ctx.chart_rows[chart_key] = rows
        filename = f"{chart_key}.svg"
        level_label = CLASSIFICATION_LEVEL_LABELS.get(level, f"Level {level}")
        # 排名全域規則＝前 20 名（render_bar_chart 預設 limit=20）
        render_bar_chart(
            ctx.run_dir / filename,
            f'{report["label_zh"]} - {level_label}',
            rows,
            source_column,
        )
        variants.append({"label": f"{level} 階 · {level_label.split('(')[-1].rstrip(')')}", "file": filename, "variant_key": f"L{level}"})
    ctx.sections.append({
        "title": report["label_zh"],
        "variants": variants,
        "note": "4 階=subclass 總覽，5 階=main group 細分；可用切換鈕對照，每階各取前 20。",
    })


def _build_ipc_section(ctx: ChartContext) -> None:
    _build_classification_section(ctx, "ipc_main_distribution", "Orig. IPC(Main)", ctx.ipc_levels)


def _build_cpc_section(ctx: ChartContext) -> None:
    _build_classification_section(ctx, "cpc_main_distribution", "Orig. CPC(Main)", ctx.cpc_levels)


def _build_applicant_ranking_section(ctx: ChartContext) -> None:
    report = ctx.report("applicant_ranking")
    render_segmented_bar_chart(
        ctx.run_dir / "applicant_ranking.svg",
        report["label_zh"],
        report["rows"],
        "applicant_display_name",
        total_key="patent_count",
        segment_key="recent_assignee_count",
        limit=CHART_ROW_LIMIT,
        segment_label="有最新受讓人",
    )
    ctx.sections.append({
        "title": report["label_zh"],
        "variants": [{"label": "Applicants", "file": "applicant_ranking.svg", "variant_key": "default"}],
        "note": "總長＝申請人全部專利；藍色區段＝轉讓他家（最新受讓人≠申請人）的專利，同名未離手不計。CSV/JSON 保留受讓人公司明細欄。",
    })


def _build_owner_ranking_section(ctx: ChartContext) -> None:
    report = ctx.report("owner_ranking")
    # F-12：與申請人排名同一套規則——列數收斂到可讀，被截時圖上標示。
    render_bar_chart(ctx.run_dir / "owner_ranking.svg", report["label_zh"], report["rows"],
                     "current_assignee_display_name", limit=CHART_ROW_LIMIT)
    ctx.sections.append({"title": report["label_zh"], "variants": [{"label": "Assignees", "file": "owner_ranking.svg", "variant_key": "default"}]})



def shared_matrix_max(ctx: "ChartContext", *report_names: str) -> int | None:
    """跨報表的共同色階基準（取各報表格值的最大值）。

    ⚠ **目前未採用**，保留供日後參考並記下否決理由：泡泡半徑也用 `max_value`
    正規化（`radius = 9 + 19*sqrt(value/max_value)`），共用尺度會讓數值較小的
    那張圖泡泡全部縮小——實測最大半徑由 27 掉到 11.3，回歸測試擋下。
    讀者原本抱怨的就是看不清，用「跨頁可比」換「兩張都變小」並不划算。
    跨頁可比改以**圖例標出各自實際級距**達成（見 render_year_bubble_matrix_chart）。

    🔴 2026-07-31：申請人年度矩陣與專利權人年度矩陣**各自正規化**，於是同樣是
    「1 件」在兩頁顯示成不同顏色。兩張圖版型相同且相鄰，讀者必然橫向比較，
    會把其中一頁的 1 誤讀成比另一頁的 1 更多。兩者單位同為「件數」，共用尺度成立。

    ⚠ 兩個 section 是獨立建構、順序不保證，故**兩邊都算跨報表最大值**，
    不靠「先跑的把值存起來給後跑的」——那種寫法換個註冊順序就失效。
    報表未被選取時略過，全部缺席回 None（呼叫端退回各自正規化）。
    """
    values: list[int] = []
    for name in report_names:
        try:
            report = ctx.report(name)
        except Exception:
            continue
        for row in report.get("rows") or []:
            for key, value in row.items():
                if key.endswith("_display_name") or not isinstance(value, (int, float)):
                    continue
                values.append(int(value))
    return max(values) if values else None


def _build_applicant_year_matrix_section(ctx: ChartContext) -> None:
    """申請人 × 申請年份泡泡矩陣。"""
    report = ctx.report("applicant_year_matrix")
    layout = year_bubble_matrix_layout(report["rows"], "applicant_display_name")
    top_rows = layout["top_rows"]
    render_year_bubble_matrix_chart(
        ctx.run_dir / "applicant_year_matrix.svg",
        report["label_zh"],
        layout,
        top_rows[:10],
    )
    more_variants = []
    if len(top_rows) > 10:
        render_year_bubble_matrix_chart(
            ctx.run_dir / "applicant_year_matrix_more.svg",
            f'{report["label_zh"]}（第 11～20 名）',
            layout,
            top_rows[10:20],
        )
        more_variants.append({"label": "11-20", "file": "applicant_year_matrix_more.svg", "variant_key": "more"})
    # 數據區改交叉表（2026-07-29 使用者定案「數據表是長格式，難讀」）：
    # 原本每列 (公司, 年份, 件數)，同一家公司的不同年份分散在不同列。
    # 轉置在後端做，前端不必知道差異。
    ctx.chart_rows["applicant_year_matrix"] = pivot_year_matrix(report["rows"], "applicant_display_name")
    ctx.sections.append({
        "title": report["label_zh"],
        "variants": [{"label": "Top 10", "file": "applicant_year_matrix.svg", "variant_key": "default"}],
        "more_variants": more_variants,
        "more_label": "＋查看全部（第 11～20 名）",
        "note": f"縱軸為申請人公司，橫軸為申請年份，泡泡大小＝patent_count；依公司跨年度總量排序，預設顯示前 {min(10, len(top_rows))} / {layout['rows_total']} 家。CSV/JSON 保留完整 rows。",
    })


def _build_owner_year_matrix_section(ctx: ChartContext) -> None:
    """專利權人 × 申請年份泡泡矩陣。"""
    report = ctx.report("owner_year_matrix")
    layout = year_bubble_matrix_layout(report["rows"], "current_assignee_display_name")
    top_rows = layout["top_rows"]
    render_year_bubble_matrix_chart(
        ctx.run_dir / "owner_year_matrix.svg",
        report["label_zh"],
        layout,
        top_rows[:10],
    )
    more_variants = []
    if len(top_rows) > 10:
        render_year_bubble_matrix_chart(
            ctx.run_dir / "owner_year_matrix_more.svg",
            f'{report["label_zh"]}（第 11～20 名）',
            layout,
            top_rows[10:20],
        )
        more_variants.append({"label": "11-20", "file": "owner_year_matrix_more.svg", "variant_key": "more"})
    # 數據區改交叉表（2026-07-29 使用者定案「數據表是長格式，難讀」）：
    # 原本每列 (公司, 年份, 件數)，同一家公司的不同年份分散在不同列。
    # 轉置在後端做，前端不必知道差異。
    ctx.chart_rows["owner_year_matrix"] = pivot_year_matrix(report["rows"], "current_assignee_display_name")
    ctx.sections.append({
        "title": report["label_zh"],
        "variants": [{"label": "Top 10", "file": "owner_year_matrix.svg", "variant_key": "default"}],
        "more_variants": more_variants,
        "more_label": "＋查看全部（第 11～20 名）",
        "note": f"縱軸為專利權人公司，橫軸為申請年份，泡泡大小＝patent_count；依公司跨年度總量排序，預設顯示前 {min(10, len(top_rows))} / {layout['rows_total']} 家。CSV/JSON 保留完整 rows。",
    })


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
    ctx.chart_rows["applicant_country_matrix"] = report["rows"]
    # 檔名 applicant_country_matrix ≠ 報表鍵 applicant_country_distribution，須顯式宣告查找鍵。
    ctx.sections.append({
        "title": report["label_zh"],
        "report_key": "applicant_country_distribution",
        "variants": [{"label": "Matrix", "file": "applicant_country_matrix.svg", "variant_key": "default"}],
        "note": note,
    })


def _build_lifecycle_section(ctx: ChartContext) -> None:
    """生命週期軌跡圖：年度 × 申請人家數 vs 件數。"""
    report = ctx.report("lifecycle")
    render_lifecycle_chart(ctx.run_dir / "lifecycle.svg", report["label_zh"], report["rows"])
    ctx.sections.append({
        "title": report["label_zh"],
        "variants": [{"label": "Lifecycle", "file": "lifecycle.svg", "variant_key": "default"}],
        "note": "各點＝一個申請年；萌芽/成長/成熟/衰退的階段判讀由分析者依軌跡判斷。",
    })


# 主題來源段名／檔名後綴（2026-07-21 定案：技術、功效不混；原始欄名不進使用者介面）
SOURCE_SEGMENT_LABELS = {"wips_independent_claims": "技術主題", "effect_summary": "功效分類"}
SOURCE_SEGMENT_SLUGS = {"wips_independent_claims": "tech", "effect_summary": "effect"}


def pivot_year_matrix(rows: list[dict[str, Any]], entity_key: str) -> list[dict[str, Any]]:
    """年度矩陣長格式 → 交叉表（2026-07-29 使用者定案「數據表是長格式，難讀」）。

    輸入每列＝(公司, 年份, 件數)；同一家公司的不同年份分散在不同列，
    使用者要自己對照才看得出趨勢（實測 45 列 / 31 列）。

    輸出每列＝一家公司，年份成為欄位，末欄 total：

        {entity_key: "A", "2022": 3, "2024": 5, "total": 8}

    設計取捨：
    - **該年無資料回空字串不是 0**——0 讀起來像「查過但沒有」，空白才是「無此資料」
    - 依 total 降冪：這是排名報表，件數多的在上
    - 年份欄由舊到新（時間序），欄名用字串以維持 JSON key 型別一致
    - 轉置在後端做，前端不必知道差異（同一資訊一個落點）
    """
    if not rows:
        return []
    years = sorted({str(r.get("application_year")) for r in rows
                    if r.get("application_year") is not None})
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = str(r.get(entity_key) or "")
        if not name:
            continue
        year = str(r.get("application_year"))
        cnt = int(r.get("patent_count") or 0)
        cell = grouped.setdefault(name, {entity_key: name, **{y: "" for y in years},
                                         "total": 0})
        cell[year] = int(cell.get(year) or 0) + cnt
        cell["total"] += cnt
    return sorted(grouped.values(), key=lambda x: (-x["total"], str(x[entity_key])))


def _source_segments(rows: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """依 source_field 分段（技術先、功效後、未知來源殿後），回傳 [(source_field, 段名, rows)]。"""
    order = {"wips_independent_claims": 0, "effect_summary": 1}
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(str(r.get("source_field", "")), []).append(r)
    return [
        (sf, SOURCE_SEGMENT_LABELS.get(sf, "其他分類"), members)
        for sf, members in sorted(groups.items(), key=lambda kv: (order.get(kv[0], 9), kv[0]))
    ]


def render_cluster_topic_table_html(
    path: Path,
    title: str,
    rows: list[dict[str, Any]],
) -> None:
    """主題／功效統計表：依 source_field 分段各自一張表（技術、功效不混；
    Source Field 欄不顯示，段標題已表達來源）。只有一種來源時只出現該段。"""
    parts = [
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">',
        '<style>',
        'body{font-family:"Microsoft JhengHei","Segoe UI",Arial,sans-serif;margin:16px;color:#111827}',
        'table{border-collapse:collapse;width:100%;font-size:13px;margin:0 0 18px}',
        'th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #E5E7EB}',
        'th{background:#F1F5F9;font-weight:600;position:sticky;top:0}',
        'tr:hover{background:#F8FAFC}',
        'h3{font-size:15px;margin:14px 0 8px}',
        '.num{text-align:right;font-variant-numeric:tabular-nums}',
        '</style></head><body>',
        f'<h2 style="font-size:18px;margin:0 0 12px">{xml_text(title)}</h2>',
    ]
    header = (
        '<table><thead><tr>'
        '<th>Topic Code</th><th>Label</th>'
        '<th class="num">專利件數</th><th class="num">申請人家數</th>'
        '<th class="num">龍頭涉入(家)</th>'
        '<th>前三大申請人</th>'
        '</tr></thead><tbody>'
    )
    for _sf, segment_label, seg_rows in _source_segments(rows):
        parts.append(f'<h3>{xml_text(segment_label)}</h3>')
        parts.append(header)
        for r in sorted(seg_rows, key=lambda item: -item["patent_count"]):
            top3_str = "；".join(
                f'{a["name"]} ({a["count"]})' for a in (r.get("top_applicants") or [])
            )
            parts.append(
                f'<tr>'
                f'<td>{xml_text(r["topic_code"])}</td>'
                f'<td>{xml_text(r["label"])}</td>'
                f'<td class="num">{r["patent_count"]}</td>'
                f'<td class="num">{r.get("applicant_count", 0)}</td>'
                f'<td class="num">{r.get("leading_applicant_count", 0)}</td>'
                f'<td>{xml_text(top3_str)}</td>'
                f'</tr>'
            )
        parts.append("</tbody></table>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _qlabel(px: float, py: float, p_med: float, a_med: float) -> tuple[str, str]:
    """回傳（象限名, 後續行動）。

    🔴 2026-08-02：高密度高廣度原本叫「必守核心戰場 → 迴避設計」。
    ⚠ 這張圖只有「件數 × 申請人家數」兩個維度，推不出迴避設計結論——
    真正的 FTO 需要 claim chart、claim overlap、legal status、jurisdiction，
    一項都不在這裡。用密度統計冒充侵權判斷會誤導決策，故改為描述**現象**
    （高競爭技術區）與**下一步查證動作**（claim overlap 分析）。
    """
    if px >= p_med and py >= a_med:
        return "高競爭技術區", "需進行 claim overlap 分析"
    if px < p_med and py >= a_med:
        return "新興戰場（競爭者已進場）", "值得追"
    if px < p_med and py < a_med:
        return "待釐清領域", "需使用者痛點調查"
    return "單一玩家壟斷型", "注意依賴風險"


def _opportunity_quadrant_name(row: dict[str, Any], p_med: float, a_med: float) -> str:
    """依既有四象限門檻回傳前端表格用象限名稱；不改 SVG 產製邏輯。"""
    hi_patent = float(row["patent_count"]) >= p_med
    hi_applicant = float(row["applicant_count"]) >= a_med
    if hi_patent and hi_applicant:
        return "高競爭技術區"
    if (not hi_patent) and hi_applicant:
        return "新興戰場"
    if hi_patent and (not hi_applicant):
        return "單一玩家壟斷"
    return "待釐清"


def _opportunity_display_rows(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    """產生機會四象限前端數據表 rows；主題統計表 rows 與 SVG matrix 均不改。"""
    p_med = float(matrix.get("patent_count_median", 0))
    a_med = float(matrix.get("applicant_count_median", 0))
    rows = []
    for row in matrix.get("rows", []):
        leading = row.get("leading_applicants_involved") or []
        rows.append({
            "label": row.get("label") or row.get("topic_code", ""),
            "patent_count": row.get("patent_count", 0),
            "applicant_count": row.get("applicant_count", 0),
            "quadrant": _opportunity_quadrant_name(row, p_med, a_med),
            "leading_applicants": "；".join(str(name) for name in leading) if leading else "—",
            "leading_applicant_count": row.get("leading_applicant_count", 0),
        })
    return rows


def _opportunity_thresholds(matrix: dict[str, Any]) -> dict[str, float]:
    """回傳機會四象限表格上方顯示的門檻值。"""
    return {
        "patent_count_median": float(matrix.get("patent_count_median", 0)),
        "applicant_count_median": float(matrix.get("applicant_count_median", 0)),
    }


# ---------------------------------------------------------------------------
# 板狀象限圖（2026-07-21 二次修正）：照範例頁 6/7 的板狀佈局取代散點座標式。
# 主題以 chip 小卡在格內流式換行排列（行高固定、同列 x 依序遞增），
# 「結構上不可能重疊」由排列演算法保證，非靠事後碰撞檢查。
# ---------------------------------------------------------------------------

# chip 佈局常數（機會板／痛點板共用）
_CHIP_FONT = 12      # chip 文字字級（px）
_CHIP_H = 24         # chip 高度＝行高（固定）
_CHIP_PAD_X = 9      # chip 內左右留白
_CHIP_GAP_X = 8      # 同列 chip 間距
_CHIP_GAP_Y = 8      # 列與列間距

# 龍頭涉入三級色（沿用散點版 tier_colors）
_TIER_COLORS = {"lead≥2": "#DC2626", "lead=1": "#F59E0B", "lead=0": "#9CA3AF"}


def _tier_key(leading_count: int) -> str:
    """龍頭涉入數 → 三級色 key（≥2家／1家／0家）。"""
    return "lead≥2" if leading_count >= 2 else "lead=1" if leading_count == 1 else "lead=0"


def _est_text_width(text: str, font_size: float) -> float:
    """估算文字像素寬：CJK≈font_size px、ASCII／半形≈0.55×font_size（chip 定寬用）。"""
    return sum(font_size if ord(ch) > 0xFF else font_size * 0.55 for ch in text)


def _chip_text_color(hex_fill: str) -> str:
    """依 chip 底色亮度自動對比字色：亮底配深字、暗底配白字。"""
    r = int(hex_fill[1:3], 16)
    g = int(hex_fill[3:5], 16)
    b = int(hex_fill[5:7], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#111827" if luminance > 0.6 else "#FFFFFF"


def _fit_chip_text(text: str, area_w: float) -> tuple[str, float]:
    """算 chip 寬；文字超過格寬時截字加「…」，回傳（顯示文字, chip 寬）。"""
    max_text_w = area_w - 2 * _CHIP_PAD_X
    if _est_text_width(text, _CHIP_FONT) <= max_text_w:
        return text, _est_text_width(text, _CHIP_FONT) + 2 * _CHIP_PAD_X
    clipped = text
    while len(clipped) > 1 and _est_text_width(clipped + "…", _CHIP_FONT) > max_text_w:
        clipped = clipped[:-1]
    clipped += "…"
    return clipped, min(_est_text_width(clipped, _CHIP_FONT) + 2 * _CHIP_PAD_X, area_w)


def _flow_chips(chips: list[dict[str, Any]], area_w: float) -> tuple[list[dict[str, Any]], float]:
    """把 chips 流式排進寬 area_w 的格內（相對座標），回傳（定位清單, 內容總高）。

    同列 chip x 依序遞增（前一顆右緣＋間距），放不下就換行、行高固定，
    因此同列 chip 的 x 區間必不相交——結構性防重疊。
    """
    placed: list[dict[str, Any]] = []
    x = 0.0
    y = 0.0
    for chip in chips:
        display, w = _fit_chip_text(chip["text"], area_w)
        if x > 0 and x + w > area_w:
            x = 0.0
            y += _CHIP_H + _CHIP_GAP_Y
        placed.append({**chip, "display": display, "x": x, "y": y, "w": w})
        x += w + _CHIP_GAP_X
    total_h = (y + _CHIP_H) if placed else 0.0
    return placed, total_h


def _chip_svg(chip: dict[str, Any], abs_x: float, abs_y: float, attrs: str) -> list[str]:
    """輸出單一 chip（圓角矩形＋自動對比文字＋tooltip）；attrs＝data-* 識別屬性。

    rect 屬性順序固定為 class → data-* → x/y/width/height，測試以 regex 依此取回。
    """
    fill = chip["fill"]
    return [
        f'<rect class="chip" {attrs} x="{abs_x:.1f}" y="{abs_y:.1f}" width="{chip["w"]:.1f}" '
        f'height="{_CHIP_H}" rx="6" fill="{fill}">'
        f'<title>{xml_text(chip.get("tooltip", chip["text"]))}</title></rect>',
        f'<text x="{abs_x + _CHIP_PAD_X:.1f}" y="{abs_y + 16.5:.1f}" font-size="{_CHIP_FONT}" '
        f'fill="{_chip_text_color(fill)}" data-on-fill="{fill}">{xml_text(chip["display"])}</text>',
    ]


def render_opportunity_quadrant_svg(
    path: Path,
    title: str,
    data: dict[str, Any],
) -> None:
    """機會評估板（板狀佈局）：2×2 格依中位數門檻分格。

    每格 header＝密度/廣度標籤＋戰場語言→行動指引（文案沿用 _qlabel 唯一來源，
    色沿用 qcolors）；格內主題畫 chip「label 件/家」，chip 底色＝龍頭涉入三級。
    軸為語意方向標籤（無數值刻度）；空格顯示「本案無此類」；格高依 chip 行數自動長高。
    """
    rows = data.get("rows", [])
    p_med = float(data.get("patent_count_median", 0))
    a_med = float(data.get("applicant_count_median", 0))

    width = 1120
    margin_l, margin_r = 64, 24
    cell_gap = 14
    cell_w = (width - margin_l - margin_r - cell_gap) / 2
    inner_pad = 12
    area_w = cell_w - 2 * inner_pad

    qcolors = {"q1": "#10B981", "q2": "#3B82F6", "q3": "#9CA3AF", "q4": "#F59E0B"}
    density_tags = {"q1": "高密度 · 高廣度", "q2": "低密度 · 高廣度",
                    "q3": "低密度 · 低廣度", "q4": "高密度 · 低廣度"}
    # 以象限代表點反查 _qlabel，戰場語言＋行動指引不在此重複定義
    probes = {"q1": (1.0, 1.0), "q2": (0.0, 1.0), "q3": (0.0, 0.0), "q4": (1.0, 0.0)}

    # 依中位數分格：X＝專利件數（密度）、Y＝申請人家數（廣度）
    cell_rows: dict[str, list[dict[str, Any]]] = {q: [] for q in qcolors}
    for r in sorted(rows, key=lambda item: -int(item["patent_count"])):
        hi_x = float(r["patent_count"]) >= p_med
        hi_y = float(r["applicant_count"]) >= a_med
        q = "q1" if (hi_x and hi_y) else "q2" if hi_y else "q4" if hi_x else "q3"
        cell_rows[q].append(r)

    header_h = 44  # 格 header：密度標籤行＋戰場語言行
    placed: dict[str, tuple[list[dict[str, Any]], float]] = {}
    for q, members in cell_rows.items():
        chips = []
        for r in members:
            label = str(r.get("label") or r.get("topic_code", ""))
            lc = int(r.get("leading_applicant_count", 0))
            involved = "、".join(r.get("leading_applicants_involved") or [])
            tooltip = f'{label} / {int(r["patent_count"])}件 {int(r["applicant_count"])}家'
            if involved:
                tooltip += f"｜龍頭：{involved}"
            chips.append({
                "text": f'{label} {int(r["patent_count"])}/{int(r["applicant_count"])}',
                "fill": _TIER_COLORS[_tier_key(lc)],
                "topic": str(r.get("topic_code", "")),
                "tooltip": tooltip,
            })
        placed[q] = _flow_chips(chips, area_w)

    def _cell_h(q: str) -> float:
        """格內容高＝header＋chips（空格留 placeholder 行高）＋底留白。"""
        chips_h = placed[q][1]
        return header_h + (chips_h if chips_h else 20.0) + inner_pad

    grid_top = 104.0
    grid_chrome = grid_top + cell_gap + 64  # 標題區＋列間距＋底部說明
    # 🔴 H-8（2026-08-03 實機 p17／p18）：象限板實測 1120×374＝比例 2.99，
    # 而 chart_hero 圖框是 1.78——等比縮放後圖只佔框高六成，上下各空兩成。
    # ⚠ 修法不是把圖拉大（那會超出框寬），是**讓畫布本身接近圖框比例**：
    # 列高的下限由目標比例回推，多出來的空間留在**格子內**。
    # 格子有邊框、是內容區，格內下緣留白讀起來是「這一格東西比較少」；
    # 頁面上的空白則單純是版面沒用滿——同樣的像素，意義不同。
    target_h = width / QUADRANT_TARGET_ASPECT
    # ⚠ I-9（2026-08-03）評估後**維持現狀不改**：
    # 實機每格高 224px、內容約 72px（格內空 68%），我一度想給格內留白設上限，
    # 但量測後確認**與 H-8 互斥**——要維持 1.78 比例，格高就必須是內容的 3.1 倍；
    # 任何更小的倍率都會讓畫布變扁、頁面留白回來（實測改成 1.5 倍後畫布 2.65、
    # 頁面空回 33%）。
    # 🔴 使用者實機抱怨的是**頁面留白**（H-8），格內留白是我自己記的觀察、
    # 他評為輕微。不拿他沒抱怨的去換掉他抱怨過的。
    min_row_h = max(96.0, (target_h - grid_chrome) / 2)
    top_row_h = max(_cell_h("q2"), _cell_h("q1"), min_row_h)
    bot_row_h = max(_cell_h("q3"), _cell_h("q4"), min_row_h)
    grid_bottom = grid_top + top_row_h + cell_gap + bot_row_h
    height = int(grid_bottom + 64)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="Segoe UI, sans-serif">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text data-role="chart-title" x="{margin_l}" y="34" font-size="20" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
        # Y 軸口徑防呆註（沿用散點版文案）
        f'<text x="{margin_l}" y="56" font-size="{CHART_LABEL_PX}" fill="#9CA3AF">※ 純專利訊號(申請人家數)＝衡量競爭者是否已進場，不等於產品核心度</text>',
        # 圖例：色＝龍頭涉入三級｜數字＝件/家
        f'<text x="{margin_l}" y="86" font-size="{CHART_LABEL_PX}" font-weight="600" fill="{COLOR_TEXT}">{LEGEND_PREFIX_TEXT}</text>',
    ]
    # 🔴 H-8（2026-08-03 實機 p17／p18）：圖例方塊壓在「數字＝件/家」上面。
    # 原本 `margin_l + 200` 與 `+= 130` 都是寫死的——前綴實際約 234px、
    # 每個圖例項約 144px，兩個數字都不夠。改為依**實際文字寬度**推進，
    # 與標籤區用同一支 `_display_width`，不另立估法。
    legend_x = margin_l + _text_px(LEGEND_PREFIX_TEXT) + LEGEND_ITEM_GAP_PX
    for key, desc in [("lead≥2", "龍頭涉入≥2家"), ("lead=1", "龍頭涉入1家"), ("lead=0", "無龍頭涉入")]:
        parts.append(f'<rect x="{legend_x}" y="{76}" width="12" height="12" fill="{_TIER_COLORS[key]}" rx="2"/>')
        parts.append(f'<text x="{legend_x + 18}" y="87" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">{xml_text(desc)}</text>')
        legend_x += 18 + _text_px(desc) + LEGEND_ITEM_GAP_PX

    cell_pos = {
        "q2": (margin_l, grid_top, top_row_h),
        "q1": (margin_l + cell_w + cell_gap, grid_top, top_row_h),
        "q3": (margin_l, grid_top + top_row_h + cell_gap, bot_row_h),
        "q4": (margin_l + cell_w + cell_gap, grid_top + top_row_h + cell_gap, bot_row_h),
    }
    for q, (cx, cy, ch) in cell_pos.items():
        battle, action = _qlabel(*probes[q], 0.5, 0.5)
        parts.append(
            f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{cell_w:.1f}" height="{ch:.1f}" rx="10" '
            f'fill="{qcolors[q]}" fill-opacity="0.07" stroke="#E5E7EB"/>')
        parts.append(
            f'<text x="{cx + inner_pad:.1f}" y="{cy + 18:.1f}" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT_SOFT}">{xml_text(density_tags[q])}</text>')
        parts.append(
            f'<text x="{cx + inner_pad:.1f}" y="{cy + 37:.1f}" font-size="{CHART_LABEL_PX}" font-weight="600" '
            f'fill="{qcolors[q]}">{xml_text(f"{battle} → {action}")}</text>')
        chips, _chips_h = placed[q]
        if chips:
            for chip in chips:
                parts.extend(_chip_svg(
                    chip, cx + inner_pad + chip["x"], cy + header_h + chip["y"],
                    f'data-cell="{q}" data-topic="{xml_text(chip["topic"])}"'))
        else:
            parts.append(
                f'<text x="{cx + inner_pad:.1f}" y="{cy + header_h + 14:.1f}" font-size="{CHART_LABEL_PX}" '
                f'fill="#9CA3AF" font-style="italic">本案無此類</text>')

    # 語意方向軸標籤（無數值刻度）
    mid_x = margin_l + (width - margin_l - margin_r) / 2
    parts.append(
        f'<text x="{mid_x:.0f}" y="{grid_bottom + 26:.0f}" text-anchor="middle" font-size="{CHART_LABEL_PX}" '
        f'fill="{COLOR_TEXT}">低密度  ←  專利密度(件數)  →  高密度</text>')
    mid_y = grid_top + (grid_bottom - grid_top) / 2
    parts.append(
        f'<text x="26" y="{mid_y:.0f}" text-anchor="middle" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}" '
        f'transform="rotate(-90,26,{mid_y:.0f})">低  ←  申請人家數(廣度)  →  高</text>')
    # 腳註 FTO 聲明（沿用）
    parts.append(
        f'<text x="{margin_l}" y="{grid_bottom + 48:.0f}" font-size="{CHART_LABEL_PX}" fill="#9CA3AF">'
        f'本分析非侵權迴避(FTO)結論｜資料依公開專利資訊整理</text>')

    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def render_pain_point_quadrant_svg(
    path: Path,
    title: str,
    data: dict[str, Any],
) -> None:
    """痛點交叉驗證板（板狀佈局）。

    列帶＝痛點 高／中（中線帶）／低／待調查（灰帶，unknown 全集中此帶、不落低）；
    欄＝密度 低/高（共用機會板同一 X 中位數）。chip＝「label 件數」、底色＝嚴重度色。
    四角象限名照範例頁 7：研發優先缺口★＝低密度×高痛點（散點版左右錯置，板狀版修正）。
    """
    rows = data.get("rows", [])
    x_med = float(data.get("x_median", 0))

    width = 1120
    margin_l, margin_r = 24, 24
    label_w = 104  # 左側帶標籤欄寬
    board_x = margin_l + label_w
    col_gap = 14
    col_w = (width - board_x - margin_r - col_gap) / 2
    inner_pad = 12
    area_w = col_w - 2 * inner_pad

    band_order = ("high", "medium", "low", "unknown")
    band_labels = {"high": "痛點 高", "medium": "中（中線帶）", "low": "低", "unknown": "待調查（灰帶）"}
    band_bg = {"high": "#FEF2F2", "medium": "#FFFBEB", "low": "#F0FDF4", "unknown": "#F3F4F6"}
    chip_fill = {"high": "#EF4444", "medium": "#EAB308", "low": "#10B981", "unknown": "#D1D5DB"}
    corner_names = {
        ("high", "lo"): "研發優先缺口★",
        ("high", "hi"): "高競爭→claim overlap 分析",
        ("low", "lo"): "nice-to-have→防禦即可",
        ("low", "hi"): "競爭者已過度投入→選擇性",
    }

    # 依嚴重度分帶、依 X 中位數分欄；非法／缺 severity 一律進待調查灰帶（不落低）
    cells: dict[tuple[str, str], list[dict[str, Any]]] = {
        (band, col): [] for band in band_order for col in ("lo", "hi")}
    for r in sorted(rows, key=lambda item: -int(item["patent_count"])):
        sev = str(r.get("severity", "unknown"))
        if sev not in band_labels:
            sev = "unknown"
        col = "hi" if float(r["patent_count"]) >= x_med else "lo"
        cells[(sev, col)].append(r)

    placed: dict[tuple[str, str], tuple[list[dict[str, Any]], float]] = {}
    for key, members in cells.items():
        chips = []
        for r in members:
            label = str(r.get("label") or r.get("topic_code", ""))
            tooltip = f'{label} / {int(r["patent_count"])}件 / {key[0]}'
            if r.get("basis"):
                tooltip += f'｜依據：{r["basis"]}'
            chips.append({
                "text": f'{label} {int(r["patent_count"])}',
                "fill": chip_fill[key[0]],
                "topic": str(r.get("topic_code", "")),
                "tooltip": tooltip,
            })
        placed[key] = _flow_chips(chips, area_w)

    corner_h = 24  # 有象限名的格，chips 前多留一行
    def _cell_h(key: tuple[str, str]) -> float:
        """格內容高＝（象限名行）＋chips＋上下留白。"""
        head = corner_h if key in corner_names else 8.0
        chips_h = placed[key][1]
        return head + chips_h + inner_pad + 8.0

    grid_top = 116.0
    band_gap = 10.0
    band_tops: dict[str, float] = {}
    band_hs: dict[str, float] = {}
    y_cursor = grid_top
    for band in band_order:
        h = max(_cell_h((band, "lo")), _cell_h((band, "hi")), 60.0)
        band_tops[band] = y_cursor
        band_hs[band] = h
        y_cursor += h + band_gap
    grid_bottom = y_cursor - band_gap
    height = int(grid_bottom + 70)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="Segoe UI, sans-serif">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text data-role="chart-title" x="{margin_l}" y="34" font-size="20" font-weight="700" fill="{COLOR_TEXT}">{xml_text(title)}</text>',
        # 副標銜接句（沿用散點版文案）
        f'<text x="{margin_l}" y="54" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT_SOFT}">把機會矩陣「待釐清領域」一軸用公開痛點初步補上（數字＝專利件數）</text>',
    ]
    # 圖例：嚴重度四級色（沿用 severity 色；unknown 顯示為待調查灰）
    legend_x = margin_l
    for band in band_order:
        legend_label = "待調查" if band == "unknown" else {"high": "高", "medium": "中", "low": "低"}[band]
        parts.append(f'<rect x="{legend_x}" y="70" width="12" height="12" fill="{chip_fill[band]}" rx="2"/>')
        parts.append(f'<text x="{legend_x + 18}" y="81" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT}">{xml_text(legend_label)}</text>')
        legend_x += 84
    # 欄 header：密度 低/高
    col_x = {"lo": board_x, "hi": board_x + col_w + col_gap}
    parts.append(f'<text x="{col_x["lo"] + col_w / 2:.0f}" y="{grid_top - 8:.0f}" text-anchor="middle" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT_SOFT}">低密度</text>')
    parts.append(f'<text x="{col_x["hi"] + col_w / 2:.0f}" y="{grid_top - 8:.0f}" text-anchor="middle" font-size="{CHART_LABEL_PX}" fill="{COLOR_TEXT_SOFT}">高密度</text>')

    for band in band_order:
        by = band_tops[band]
        bh = band_hs[band]
        parts.append(
            f'<rect x="{board_x:.1f}" y="{by:.1f}" width="{width - board_x - margin_r:.1f}" '
            f'height="{bh:.1f}" rx="8" fill="{band_bg[band]}" stroke="#E5E7EB"/>')
        parts.append(
            f'<text x="{board_x - 10:.1f}" y="{by + bh / 2 + 4:.1f}" text-anchor="end" '
            f'font-size="{CHART_LABEL_PX}" fill="#374151">{xml_text(band_labels[band])}</text>')
        # 欄分隔虛線
        sep_x = board_x + col_w + col_gap / 2
        parts.append(
            f'<line x1="{sep_x:.1f}" y1="{by:.1f}" x2="{sep_x:.1f}" y2="{by + bh:.1f}" '
            f'stroke="#D1D5DB" stroke-width="1" stroke-dasharray="4,4"/>')
        for col in ("lo", "hi"):
            cx = col_x[col]
            head = corner_h if (band, col) in corner_names else 8.0
            if (band, col) in corner_names:
                parts.append(
                    f'<text x="{cx + inner_pad:.1f}" y="{by + 17:.1f}" font-size="{CHART_LABEL_PX}" '
                    f'font-weight="600" fill="{COLOR_TEXT_SOFT}">{xml_text(corner_names[(band, col)])}</text>')
            for chip in placed[(band, col)][0]:
                parts.extend(_chip_svg(
                    chip, cx + inner_pad + chip["x"], by + head + chip["y"],
                    f'data-band="{band}" data-col="{col}" data-topic="{xml_text(chip["topic"])}"'))

    # 語意方向軸標籤（沿用文案）＋腳註（FTO＋痛點待調查聲明沿用）
    mid_x = board_x + (width - board_x - margin_r) / 2
    parts.append(
        f'<text x="{mid_x:.0f}" y="{grid_bottom + 26:.0f}" text-anchor="middle" font-size="{CHART_LABEL_PX}" '
        f'fill="{COLOR_TEXT}">低  ← 專利件數 (patent_count) →  高</text>')
    pain_note = "｜痛點為待調查狀態" if any(
        str(r.get("severity", "unknown")) not in ("high", "medium", "low") or r.get("severity") == "unknown"
        for r in rows) else ""
    parts.append(
        f'<text x="{margin_l}" y="{grid_bottom + 48:.0f}" font-size="{CHART_LABEL_PX}" fill="#9CA3AF">'
        f'本分析非侵權迴避(FTO)結論{pain_note}</text>')

    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _build_cluster_analytics_section(ctx: ChartContext) -> None:
    """分群分析：主題／功效統計表、機會矩陣、痛點矩陣。

    資料由 ctx.cluster_data 注入（repository adapter 層填入），
    cluster_data 為 None 時靜默跳過，不影響既有報表流程。
    """
    data = ctx.cluster_data
    if data is None:
        return

    topic_rows = build_topic_effect_table(
        data["topics"], data["assignments"], data["normalized_applicants"],
        patents=data.get("patents"),
    )

    # 2026-07-21 定案：技術、功效不混——依 source_field 分段，矩陣板每來源各一組
    # （中位數門檻按段各自計算，不跨來源混算）；單一來源維持原檔名與原 tab 名。
    segments = _source_segments(topic_rows)
    multi_source = len(segments) > 1
    # ⚠ 單一來源時才放預設項；多來源時由下方迴圈依通道各加一項
    # （否則會有「主題統計表」與「主題統計表——技術主題」兩個重複選項）。
    # ⚠ 2026-07-29：主題統計表**不再產 HTML 變體**（使用者「沒圖表用表格就好，
    # 現在跑兩個表格很難看」）。原本這裡與下方迴圈各 append 一次（單一來源／多來源
    # 兩條路徑），是同一概念兩處落點——只移除其中一處會留下「宣告了變體但檔案不存在」
    # 的死選項。兩處一併移除，主題統計改由 section 的 rows 走數據表單一呈現。
    variants: list[dict[str, str]] = []
    segment_matrices: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    leading_by_topic: dict[str, dict[str, Any]] = {}
    for sf, segment_label, seg_rows in segments:
        opp_matrix = build_opportunity_matrix(seg_rows, data.get("top_applicants_ws", []))
        pain_matrix = build_pain_point_matrix(
            seg_rows, data.get("pain_data", []), opp_matrix["patent_count_median"]
        )
        segment_matrices.append((sf, segment_label, opp_matrix, pain_matrix))
        leading_by_topic.update({r["topic_code"]: r for r in opp_matrix["rows"]})

    # 顯示規格（2026-07-21）：把機會矩陣算出的龍頭涉入（leading_applicant_count／
    # leading_applicants_involved）依 topic_code 併回主題統計列，統計表與數據區共用。
    for row in topic_rows:
        opp_row = leading_by_topic.get(row["topic_code"], {})
        row["leading_applicant_count"] = opp_row.get("leading_applicant_count", 0)
        row["leading_applicants_involved"] = opp_row.get("leading_applicants_involved", [])

    # 兩份分群報表登記成 report 形狀（label_zh 取自 REPORT_DEFINITIONS 唯一來源），
    # 組檔時併進 report_data["reports"]——PPT 端 _page_should_render 只查該 bucket。
    # ⚠ 機會矩陣列補 source_field（成對報表在 PPT 可分頁／同頁比較，靠它切分）；
    #   thresholds 逐通道保存中位數門檻（象限判讀可重現，不每次重算）。
    opportunity_rows: list[dict[str, Any]] = []
    opportunity_thresholds: dict[str, dict[str, float]] = {}
    for sf, _segment_label, opp_matrix, _pain in segment_matrices:
        opportunity_rows.extend({**row, "source_field": sf} for row in opp_matrix["rows"])
        opportunity_thresholds[sf] = {
            "patent_count_median": opp_matrix["patent_count_median"],
            "applicant_count_median": opp_matrix["applicant_count_median"],
        }
    ctx.cluster_reports["cluster_topic_table"] = {
        "label": REPORT_DEFINITIONS["cluster_topic_table"].label,
        "label_zh": REPORT_DEFINITIONS["cluster_topic_table"].label_zh,
        "report_type": "cluster",
        "rows": topic_rows,
        "row_count": len(topic_rows),
    }
    ctx.cluster_reports["opportunity_quadrant"] = {
        "label": REPORT_DEFINITIONS["opportunity_quadrant"].label,
        "label_zh": REPORT_DEFINITIONS["opportunity_quadrant"].label_zh,
        "report_type": "cluster",
        "rows": opportunity_rows,
        "row_count": len(opportunity_rows),
        "thresholds": opportunity_thresholds,
    }

    # 主題統計表**只渲染一次**（2026-07-29 使用者實機回報，兩張截圖）：
    #
    # 原本同一份資料畫兩次並排——上方是 cluster_topic_table_<slug>.html 變體（圖表區）、
    # 下方是 chart_rows 的數據表。使用者：「主題分類統計表如果沒圖表用表格就好，
    # 現在跑兩個表格很難看」。
    #
    # 且兩者切換不同步：圖表區逐通道分檔切得動，數據區卻是
    # `chart_rows["cluster_topic_table"] = topic_rows` 一鍵存**技術＋功效全部**
    # → 使用者：「技術、功效按鈕切不了」。兩個症狀同一個根因。
    #
    # 收斂做法：這支的「圖表」本來就是表格，**不另渲染 HTML 變體**，只留數據表一份。
    # ⚠ 機會／痛點矩陣是真的 SVG 圖，維持變體不動（見下方迴圈）。
    #
    # ⚠ 列**維持單一鍵**：每列本來就帶 `source_field`（實測技術 5 列／功效 8 列），
    # 前端依該欄過濾（`rows.filter(row => row.source_field === sourceField)`）。
    # 曾嘗試依通道分鍵（cluster_topic_table_tech／_effect），但前端找的是
    # `cluster_topic_table`，分鍵反而讓它取不到資料——切換問題的真因不在這裡，
    # 而是 section 沒把 rows 帶給前端（見下方 sections.append）。
    ctx.chart_rows["cluster_topic_table"] = topic_rows

    # 🔴 主題統計表的**解讀掛點**（2026-07-30 使用者實機回報「其他都有，就這個沒有」）。
    #
    # main.py 把 narrative 掛在 **variant** 上（`entry["variants"].get(variant_key)`），
    # 而本輪移除 HTML 變體後這張表沒有任何 variant → AI 產的解讀無處可掛，
    # 前端 `v.narrative.text` 永遠讀不到（實測 narratives.json 的
    # cluster_topic_table 底下只有 opportunity_tech／effect）。
    #
    # ⚠ 這個 variant **沒有圖檔**（file 為空字串）：它只是解讀的落點，
    # 不得指向 .svg／.html——指了會在畫面顯示「圖檔待產出」佔位。
    # ⚠ 放在最前面：檢視選單以第一個變體為預設，主題統計表本來就是這張卡的主體。
    #
    # 🔴 2026-07-31：一個 variant 改為**依通道各一個**。
    # 主題統計表在 PPT 依 source_field 拆成「技術主題分布」「功效主題分布」兩頁，
    # 但解讀只有一份 → 兩頁印出一模一樣的標題與要點（使用者實機回報）。
    # 治本在上游：讓解讀本來就分通道產，PPT 兩頁各取各的。
    # ⚠ 這兩個 variant 一樣**沒有圖檔**（file 為空字串），只是解讀落點。
    # ⚠ 只有**實際存在兩個通道**時才拆：單通道還硬塞兩個變體，會多出一個永遠空的
    # 解讀落點（檢視選單也會多一個點不出東西的選項）。這裡的判斷刻意與
    # build_ppt._split_by_channel 一致——上游怎麼分，下游就怎麼取。
    channels = [
        ("wips_independent_claims", "topic_table_tech", "主題統計表（技術）"),
        ("effect_summary", "topic_table_effect", "主題統計表（功效）"),
    ]
    present = [c for c in channels
               if any(str(r.get("source_field")) == c[0] for r in topic_rows)]
    if len(present) > 1:
        for index, (_, variant_key, label) in enumerate(present):
            variants.insert(index, {"label": label, "file": "", "variant_key": variant_key})
    else:
        variants.insert(0, {"label": "主題統計表", "file": "", "variant_key": "topic_table"})

    for sf, segment_label, opp_matrix, pain_matrix in segment_matrices:
        # 檔名後綴：多來源時帶 slug（tech/effect），單一來源維持原檔名（相容既有契約）
        slug = SOURCE_SEGMENT_SLUGS.get(sf, "other")
        suffix = f"_{slug}" if multi_source else ""
        tab_suffix = f"——{segment_label}" if multi_source else ""
        opp_file = f"opportunity_quadrant{suffix}.svg"
        render_opportunity_quadrant_svg(
            ctx.run_dir / opp_file, f"機會四象限分析——{segment_label}", opp_matrix)
        opp_rows = _opportunity_display_rows(opp_matrix)
        opp_thresholds = _opportunity_thresholds(opp_matrix)
        variants.append({
            "label": f"機會矩陣{tab_suffix}",
            "file": opp_file,
            "variant_key": f"opportunity{suffix}",
            "rows": opp_rows,
            "thresholds": opp_thresholds,
        })
        ctx.chart_rows[f"opportunity_quadrant{suffix}"] = {**opp_matrix, "rows": opp_rows}
        # 🔴 痛點矩陣**不產**（2026-07-29 使用者定案「整個藏起來，等市場線做好再放出來」）。
        #
        # ⚠ 5b4dbef 只把 pain_point_quadrant 從 DEFAULT_REPORT_NAMES 排除，那擋的是
        # 「報表勾選清單」那一層——但本函式是**整包產出**，內部無條件 render + append，
        # 完全不看使用者選了哪些報表。使用者重產報表後（report_trial_20260729_164537）
        # 痛點矩陣照樣出現在檢視選單，實測打臉了我「已擋住」的判斷。
        # 教訓：擋一個報表要追**所有**產出路徑，只查 DEFAULT_REPORT_NAMES 不夠。
        #
        # 市場線（上傳→AI 摘要→使用者確認）尚未實作，缺資料時痛點軸全是「待調查」，
        # 產出的圖看不出不完整、匯進 PPT 會被讀成「痛點都很低」。
        # ⚠ 機會矩陣是純專利資料（x 專利密度、y 競爭者結構強度），**照常產出**，不連坐。
        # pain_matrix 仍計算（上方迴圈）但不落檔——市場線做好後解除本段即可恢復。

    note = (
        "主題統計表包含所有正式主題（含未分類），技術主題與功效分類分段不混表；"
        "機會板／痛點板採板狀佈局（chip 流式排列，結構上不重疊）、每個來源各一組——"
        "機會板 2×2 格依該段專利件數與申請人家數中位數分高低，chip 色＝龍頭涉入三級；"
        "痛點板列帶＝嚴重度（高／中線帶／低／待調查灰帶，unknown 不落低）、"
        "欄＝密度低/高（共用同段機會板件數中位數）。"
    )
    # 顯示規格（2026-07-21 二次修正）：板狀佈局完成，象限圖回歸 index——
    # cluster 卡片＝主題統計表＋各來源機會/痛點矩陣 tabs。
    ctx.sections.append({
        "title": "分群分析",
        "report_key": "cluster_topic_table",
        # 🔴 rows 必須帶進 section（2026-07-29 使用者實機回報「技術、功效按鈕切不了」）：
        # 原本只寫 report_key、期待前端自己從 chart_rows 取，但 API 回給前端的 section
        # **沒有 rows 欄**（實測 section keys 只有 title/report_key/variants/note）。
        # 前端 `rows.filter(row => row.source_field === sourceField)` 過濾的是空陣列，
        # 切換自然沒有任何效果——這是靜默失敗：表格由另一條路徑顯示得出來，
        # 只有切換無反應，看起來像按鈕壞掉而不是資料沒給。
        # 每列本來就帶 source_field（實測技術 5 列／功效 8 列），帶上就能切。
        "rows": topic_rows,
        "variants": variants,
        "note": note,
    })


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
    SectionSpec("applicant_year_matrix", ("applicant_year_matrix",), _build_applicant_year_matrix_section),
    SectionSpec("owner_year_matrix", ("owner_year_matrix",), _build_owner_year_matrix_section),
    SectionSpec("applicant_country", ("applicant_country_distribution",), _build_applicant_country_section),
    SectionSpec("lifecycle", ("lifecycle",), _build_lifecycle_section),
    # 分群卡片＝一張 section 出三個 artifact（主題統計表＋機會板＋痛點板）。三個報表名
    # 都掛在此 spec：requestreport_names 帶其中任一就渲染整張分群卡（三者同源、一體呈現）；
    # 保留 "cluster_analytics" 虛擬別名，相容既有「無對應報表的特殊 section」契約與呼叫端。
    SectionSpec(
        "cluster_analytics",
        ("cluster_analytics", "cluster_topic_table", "opportunity_quadrant", "pain_point_quadrant"),
        _build_cluster_analytics_section,
    ),
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
    ranking_limit: int = 20,
    ipc_levels: tuple[int, ...] = (4, 5),
    cpc_levels: tuple[int, ...] = (4, 5),
    analysis_id: int | None = None,
    report_names: Sequence[str] | None = None,
    filters: dict[str, Any] | None = None,
    cluster_data: dict[str, Any] | None = None,
    patent_ids: list[int] | None = None,
    workspace_name: str | None = None,
    workspace_id: int | None = None,
) -> dict[str, Any]:
    """渲染報表圖組（MCP reporting tools 與 CLI 共用的出圖入口）。

    report_names=None 出整套（保留舊行為）；給清單則只渲染依賴到那些報表的
    sections（選擇性出圖）。analysis_id 給了用該 analysis 的專利快照出圖，並把
    每個產出檔登錄 app_layer.export_runs；filters 讓 patent 層報表與數據端同
    口徑（家族層報表一律全庫口徑，note 現形）。

    patent_ids 由呼叫端直接指定專利範圍（worker 的 report_generate payload 走這條——
    它帶的是 patent_ids 而非 analysis_id）；與 analysis_id 同時給時以 analysis 快照
    為準（快照是正式口徑，不讓呼叫端的清單覆寫已定案的 analysis 範圍）。

    cluster_data 由呼叫端注入（見 compute_and_save_cluster_analysis 的回傳值），
    驅動分群分析區塊（主題統計表、機會矩陣、痛點矩陣）；為 None 時該區塊靜默跳過。
    """
    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR
    specs = resolve_sections(report_names)
    if analysis_id is not None:
        patent_ids = fetch_analysis_patent_ids(analysis_id)
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
        cluster_data=cluster_data,
    )
    for spec in specs:
        spec.build(ctx)

    fetched = ctx.fetched_reports()
    generated_at = datetime.now().isoformat(timespec="seconds")
    version = run_dir.name
    selected_report_names = sorted(fetched)
    parameters = {
        "ranking_limit": ranking_limit,
        "ipc_levels": list(ctx.ipc_levels),
        "cpc_levels": list(ctx.cpc_levels),
        "reports_selected": sorted(set(report_names)) if report_names is not None else "all",
        "filters": filters or None,
        "generated_at": generated_at,
        "version": version,
        "analysis_id": analysis_id,
        "has_cluster_analytics": cluster_data is not None,
        # workspace 顯示名稱（P3-2）：封面主標的資料源（P1-8 cover.title 退場後由此組成）。
        # ⚠ 不給就不落鍵——封面端以「鍵不存在」走通用標題 fallback，落 null 反而混淆。
        **({"workspace_name": workspace_name} if workspace_name else {}),
        # workspace_id（2026-07-31 版本區隔定案）：name 會撞名，id 才是穩定歸屬鍵。
        **({"workspace_id": int(workspace_id)} if workspace_id is not None else {}),
        **patent_snapshot_metadata(patent_ids),
    }

    # 入庫截取（2026-07-21 定案修正）：排名類前 20、年度序列最新 25 年；
    # 圖表已渲染完成，截取只影響落檔，不影響本次輸出的 SVG。
    persist_reports, persist_chart_rows, chart_rows_total = truncate_rows_for_persistence(
        fetched, ctx.chart_rows
    )
    write_json(
        run_dir / "report_data.json",
        {
            "parameters": parameters,
            "reports": {
                **{
                    name: report for name, report in persist_reports.items() if REPORT_DEFINITIONS[name].supports_patent_ids
                },
                # 分群兩份顯式併入（⚠ 不走 supports_patent_ids 分流：它們該欄是
                # False，照條件會被丟進 family_reports——語意是家族報表，不對）。
                # 只在有 cluster_data 時非空；沒跑分群不出現空殼（PPT 會出空頁）。
                **ctx.cluster_reports,
            },
            "family_reports": {
                name: report for name, report in persist_reports.items() if not REPORT_DEFINITIONS[name].supports_patent_ids
            },
            "chart_rows": persist_chart_rows,
            "chart_rows_total": chart_rows_total,
            # sections 持久化：--refresh-index 由此重建 index（解讀回填後重渲染）
            "sections": persistable_sections(ctx.sections),
            # 表格顯示規格（2026-07-31）：欄名對照、排除欄與儲存格呈現字串由引擎寫出，
            # PPT 端讀這份（理由見 table_display_spec docstring）。
            # ⚠ 分群兩份走 ctx.cluster_reports、不在 persist_reports 裡，必須一併餵進去
            # ——`top_applicants` 這類物件值欄正好都在它們身上。
            "table_display": table_display_spec({**persist_reports, **ctx.cluster_reports}),
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
        files.extend(variant["file"] for variant in section.get("more_variants", []))
        files.extend(link["file"] for link in section.get("links", []))
    # 版本歸屬標記檔（2026-07-31）：版本清單依 workspace 過濾時只讀這個 ~120B
    # 小檔，不開 124KB 的 report_data.json——維持「列表不撈大檔」的效率契約。
    # 沒 workspace 就不寫歸屬鍵：該版本不歸屬任何 workspace，帶過濾時不顯示。
    write_json(
        run_dir / "version_meta.json",
        {
            "version": version,
            "generated_at": generated_at,
            **({"workspace_id": int(workspace_id)} if workspace_id is not None else {}),
            **({"workspace_name": workspace_name} if workspace_name else {}),
        },
    )
    files += ["report_data.json", "index.html", "version_meta.json"]
    # De-duplicate while keeping order (a file may appear as both variant and link).
    files = list(dict.fromkeys(files))
    manifest = build_artifact_manifest(
        run_dir,
        files,
        generated_at=generated_at,
        version=version,
        report_names=selected_report_names,
        filters=filters,
        analysis_id=analysis_id,
        patent_ids=patent_ids,
    )
    write_json(run_dir / "artifact_manifest.json", manifest)
    files.append("artifact_manifest.json")
    files = list(dict.fromkeys(files))
    file_metadata = {
        item["file"]: item
        for item in manifest["artifacts"]
    }

    result: dict[str, Any] = {
        "status": "ok",
        "output_dir": str(run_dir),
        "ranking_limit": ranking_limit,
        "ipc_levels": list(ctx.ipc_levels),
        "cpc_levels": list(ctx.cpc_levels),
        "sections_rendered": [spec.key for spec in specs],
        "files": files,
        "version": version,
        "generated_at": generated_at,
        "artifact_manifest": "artifact_manifest.json",
        **ctx.meta,
    }

    if analysis_id is not None:
        export_count = record_exports(analysis_id, run_dir, files, parameters, file_metadata)
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
    parser.add_argument("--ranking-limit", type=int, default=20, help="Top N limit for applicant and current assignee ranking charts.")
    parser.add_argument("--ipc-levels", type=int, nargs="+", choices=(4, 5), default=[4, 5], help="IPC classification levels to render (4=subclass, 5=main group). Defaults to both.")
    parser.add_argument("--cpc-levels", type=int, nargs="+", choices=(4, 5), default=[4, 5], help="CPC classification levels to render (4=subclass, 5=main group). Defaults to both.")
    parser.add_argument("--analysis-id", type=int, help="Bind charts to an app_layer analysis: use its patent snapshot and record files into export_runs.")
    parser.add_argument("--reports", help="Comma-separated report keys to render selectively (default: full battery).")
    parser.add_argument("--filters", help="JSON object of report filters (whitelist columns; family reports stay full-DB scope).")
    parser.add_argument(
        "--refresh-index", type=Path, metavar="RUN_DIR",
        help="不出圖：從 RUN_DIR/report_data.json 的 sections 重建 index.html（narratives.json 有就嵌入解讀）。",
    )
    args = parser.parse_args()
    # 解讀回填後重渲染模式：不碰 DB、不出圖，只重建該目錄的 index.html
    if args.refresh_index is not None:
        try:
            result = refresh_index(args.refresh_index)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
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
