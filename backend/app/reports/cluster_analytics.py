"""分群標籤區報表分析：主題/功效統計表、機會評估與痛點四象限（2026-07-21 報表定案 #1/#5/#6）。

純邏輯模組——不碰 DB、API 或 I/O；輸入輸出皆為 dict/list，
可供任何 renderer 或 repository adapter（如 TopicStateRepository 的輸出）直接串接。
"""
from __future__ import annotations

import statistics
from typing import Any


def _compute_top_applicants(
    topic_patents: set[int],
    app_by_patent: dict[int, set[str]],
) -> tuple[list[dict[str, Any]], int]:
    """統計主題內各申請人件數，回傳（前三大清單, 獨立申請人總數）。

    口徑：同一專利對同一公司只計 1 次（set 去重）；同一專利兩家申請人各計 1 次，
    故公司件數合計可大於主題專利總件數。
    """
    counts: dict[str, int] = {}
    for pid in topic_patents:
        for name in app_by_patent.get(pid, set()):
            counts[name] = counts.get(name, 0) + 1
    sorted_apps = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [{"name": n, "count": c} for n, c in sorted_apps[:3]], len(counts)


def build_topic_effect_table(
    topics: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    normalized_applicants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """由分群定案資料建立主題/功效統計表列（全部主題都輸出，不做 Top N 截斷）。

    Parameters
    ----------
    topics : list[dict]
        每項需含 ``topic_code``、``label``、``source_field``；
        零專利的主題也會輸出（件數 0）。
    assignments : list[dict]
        每項需含 ``topic_code``（或 ``topic_key``）與 ``patent_id``。
    normalized_applicants : list[dict]
        每項需含 ``patent_id`` 與 ``applicant_name``（正規化後 applicant_display_name）；
        同一專利有多家申請人時會出現多列。

    Returns
    -------
    list[dict]
        每列：topic_code、label、source_field、patent_count（distinct patent）、
        applicant_count（獨立申請人數）、top_applicants（前三大 {name, count}）。
    """
    topic_patents: dict[str, set[int]] = {}
    for t in topics:
        topic_patents.setdefault(t["topic_code"], set())
    for a in assignments:
        tc = a.get("topic_code", a.get("topic_key", ""))
        pid = int(a["patent_id"])
        if tc in topic_patents:
            topic_patents[tc].add(pid)
        else:
            topic_patents[tc] = {pid}

    app_by_patent: dict[int, set[str]] = {}
    for na in normalized_applicants:
        pid = int(na["patent_id"])
        aname = str(na["applicant_name"]).strip()
        if not aname:
            continue
        app_by_patent.setdefault(pid, set()).add(aname)

    topic_map = {t["topic_code"]: t for t in topics}

    result: list[dict[str, Any]] = []
    for tc in sorted(topic_patents.keys()):
        patents = topic_patents[tc]
        top3, app_count = _compute_top_applicants(patents, app_by_patent)
        info = topic_map.get(tc, {})
        result.append({
            "topic_code": tc,
            "label": info.get("label", tc),
            "source_field": info.get("source_field", ""),
            "patent_count": len(patents),
            "applicant_count": app_count,
            "top_applicants": top3,
        })
    return result


def build_opportunity_matrix(
    topic_rows: list[dict[str, Any]],
    top_applicants_ws: list[str],
) -> dict[str, Any]:
    """建立機會評估四象限（專利密度 × 競爭者結構強度）資料。

    X 軸＝主題專利總件數、Y 軸＝主題獨立申請人公司數；高低門檻取當次
    主題分布的中位數，原始值與門檻一併回傳保存（報表定案 #5）。

    Parameters
    ----------
    topic_rows : list[dict]
        ``build_topic_effect_table`` 的輸出。
    top_applicants_ws : list[str]
        呼叫端傳入的 workspace／全庫前三大（龍頭）申請人公司名。

    Returns
    -------
    dict，鍵：
        rows : list[dict]
            topic_code、patent_count（X）、applicant_count（Y）、
            top_applicants、leading_applicants_involved（龍頭涉入名單）、
            leading_applicant_count（涉入數）。
        patent_count_median : float
        applicant_count_median : float
    """
    if not topic_rows:
        return {"rows": [], "patent_count_median": 0.0, "applicant_count_median": 0.0}

    patent_counts = [r["patent_count"] for r in topic_rows]
    applicant_counts = [r["applicant_count"] for r in topic_rows]

    p_median = statistics.median(patent_counts)
    a_median = statistics.median(applicant_counts)

    ws_top_set = set(top_applicants_ws)

    rows: list[dict[str, Any]] = []
    for r in topic_rows:
        involved: list[str] = []
        for app in r.get("top_applicants", []):
            if app["name"] in ws_top_set:
                involved.append(app["name"])
        rows.append({
            "topic_code": r["topic_code"],
            # label 傳遞給圖表顯示中文主題名；缺 label 時由 renderer fallback 到 code
            "label": r.get("label"),
            "patent_count": r["patent_count"],
            "applicant_count": r["applicant_count"],
            "top_applicants": r.get("top_applicants", []),
            "leading_applicants_involved": involved,
            "leading_applicant_count": len(involved),
        })

    return {
        "rows": rows,
        "patent_count_median": float(p_median),
        "applicant_count_median": float(a_median),
    }


def build_pain_point_matrix(
    topic_rows: list[dict[str, Any]],
    pain_data: list[dict[str, Any]],
    x_median: float,
) -> dict[str, Any]:
    """建立痛點交叉驗證四象限（專利訊號 × 客戶痛點）資料。

    痛點等級由使用者確認後才傳入；X 軸與機會評估共用同一次主題件數
    中位數，不得重算不同門檻；unknown 由前端顯示為灰色待調查，不當 low
    （報表定案 #6）。

    Parameters
    ----------
    topic_rows : list[dict]
        ``build_topic_effect_table`` 的輸出。
    pain_data : list[dict]
        每項需含 ``topic_code``、``severity``（high/medium/low/unknown）、
        ``basis``（簡短依據）、``source``（來源）。
    x_median : float
        機會評估四象限的專利件數中位數（共用 X 門檻）。

    Returns
    -------
    dict，鍵：
        rows : list[dict]
            topic_code、patent_count、severity、basis、source。
        x_median : float
    """
    pain_map: dict[str, dict[str, Any]] = {}
    for p in pain_data:
        pain_map[p["topic_code"]] = p

    rows: list[dict[str, Any]] = []
    for r in topic_rows:
        tc = r["topic_code"]
        info = pain_map.get(tc, {})
        rows.append({
            "topic_code": tc,
            # label 傳遞給圖表顯示中文主題名；缺 label 時由 renderer fallback 到 code
            "label": r.get("label"),
            "patent_count": r["patent_count"],
            "severity": info.get("severity", "unknown"),
            "basis": info.get("basis"),
            "source": info.get("source"),
        })

    return {
        "rows": rows,
        "x_median": float(x_median),
    }
