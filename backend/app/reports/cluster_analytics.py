"""分群標籤區報表分析：主題/功效統計表、機會評估與痛點四象限（2026-07-21 報表定案 #1/#5/#6）。

純邏輯模組——不碰 DB、API 或 I/O；輸入輸出皆為 dict/list，
可供任何 renderer 或 repository adapter（如 TopicStateRepository 的輸出）直接串接。
"""
from __future__ import annotations

import statistics
from typing import Any

# 通道常數取自唯一來源（sources.py 只 import dataclass，是純常數模組，
# 不破壞本模組「不碰 DB／API／I/O」的邊界）。不自行寫死字串——
# 值域對不上正是本專案反覆出現的靜默失敗來源。
from backend.app.clustering.sources import (
    SOURCE_FIELD_EFFECT,
    SOURCE_FIELD_TECHNICAL,
)


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
    # ⚠ 歸戶鍵＝(topic_code, source_field) 複合鍵（2026-07-28 驗收發現）。
    #
    # `topic_code` 由 clustering/runner.py 產出 `f"T{position:03d}"`，**兩個通道
    # 各自從 T001 開始編號**，共用同一命名空間。原本以 topic_code 單鍵歸戶：
    #   - topic_patents：兩通道同 code 的專利集合被**合併**（互相污染件數）
    #   - topic_map：後出現的通道**整批覆蓋**前者
    # 實測 4 個主題（技術 T001/T002 ＋ 功效 T001/T002）只出 2 列，技術全滅。
    # worker/handlers.py 把兩通道 topic_rows 直接串接，正是觸發點。
    #
    # 報表仍正常產出、不報錯——又一次靜默失敗。分群報表 07-28 上午接通時
    # 只驗了「產得出來」，沒驗「兩個通道的資料都在」。
    #
    # ⚠ 不改 topic_code 的產生規則：那會牽動 artifact、增量分群、合併/拆分
    # 一整條線。此處只修「報表層把兩通道當成同一組」。
    def _key(item: dict[str, Any], code: str) -> tuple[str, str]:
        return (code, str(item.get("source_field") or ""))

    topic_patents: dict[tuple[str, str], set[int]] = {}
    for t in topics:
        topic_patents.setdefault(_key(t, t["topic_code"]), set())

    # assignment 未帶 source_field 時（舊資料，單通道時期）退回「同 code 只有
    # 一個主題」的唯一解；同 code 跨通道則無從判斷，兩邊都不加以免灌錯數。
    codes_by_topic: dict[str, list[tuple[str, str]]] = {}
    for key in topic_patents:
        codes_by_topic.setdefault(key[0], []).append(key)

    for a in assignments:
        tc = str(a.get("topic_code", a.get("topic_key", "")) or "")
        pid = int(a["patent_id"])
        src = str(a.get("source_field") or "")
        if src:
            key = (tc, src)
        else:
            candidates = codes_by_topic.get(tc) or []
            if len(candidates) != 1:
                continue  # 同 code 多通道且無來源標記——無法歸戶，不猜
            key = candidates[0]
        topic_patents.setdefault(key, set()).add(pid)

    app_by_patent: dict[int, set[str]] = {}
    for na in normalized_applicants:
        pid = int(na["patent_id"])
        aname = str(na["applicant_name"]).strip()
        if not aname:
            continue
        app_by_patent.setdefault(pid, set()).add(aname)

    topic_map = {_key(t, t["topic_code"]): t for t in topics}

    # 排序：技術先、功效後，各自依 topic_code（與 chart_runner._source_segments
    # 的分段順序同口徑；未知來源殿後）。單以 tuple 排序會變成字母序
    # （effect_summary < wips_...），與定案的「技術先」相反。
    order = {SOURCE_FIELD_TECHNICAL: 0, SOURCE_FIELD_EFFECT: 1}

    result: list[dict[str, Any]] = []
    for key in sorted(topic_patents, key=lambda k: (order.get(k[1], 2), k[1], k[0])):
        code, source_field = key
        patents = topic_patents[key]
        top3, app_count = _compute_top_applicants(patents, app_by_patent)
        info = topic_map.get(key, {})
        result.append({
            "topic_code": code,
            "label": info.get("label", code),
            "source_field": info.get("source_field", source_field),
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
