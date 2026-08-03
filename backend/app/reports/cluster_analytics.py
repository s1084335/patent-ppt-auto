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


# ── 技術狀態分類（2026-08-02 使用者定案，五類）─────────────────────────────
#
# 這一組取代原本只有件數與家數的靜態統計：使用者要的是「技術競爭型態、演進趨勢
# 及布局意義」的判讀，不是「哪個主題件數比較高」。
TOPIC_STATUS_EMERGING = "新興技術"
TOPIC_STATUS_GROWING = "成長技術"
TOPIC_STATUS_MATURE = "成熟技術"
TOPIC_STATUS_CONCENTRATED = "競爭集中技術"
TOPIC_STATUS_DECLINING = "衰退／轉型技術"
TOPIC_STATUS_UNCLASSIFIED = "未分類"
TOPIC_STATUS_INSUFFICIENT = "樣本不足"

# 「意義」不是裝飾——狀態名只說了是什麼，讀者要的是「所以呢」（C-6）。
TOPIC_STATUS_MEANINGS: dict[str, str] = {
    TOPIC_STATUS_EMERGING: "剛開始受到關注",
    TOPIC_STATUS_GROWING: "技術快速擴散",
    TOPIC_STATUS_MATURE: "技術方向逐漸穩定",
    TOPIC_STATUS_CONCENTRATED: "技術成熟後由少數玩家掌握",
    TOPIC_STATUS_DECLINING: "技術熱度降低或被新技術取代",
    TOPIC_STATUS_INSUFFICIENT: "件數過少，趨勢判斷不可靠",
}

# 時間窗（申請年）。⚠ 這三個數字是從實際資料切出來的，不是慣例：
#   2011–2019 共 17 件（9 年）／2020–2024 共 38 件（5 年）／2025–2026 僅 5 件。
# 末兩年偏低是**資料截止效應**（新案還在審查中未公開），不是活動衰退，
# 併進近期窗會把每個主題都拉成「衰退」。故整段排除，不計入任何一窗。
STATUS_EARLY_YEARS = (2011, 2019)
STATUS_RECENT_YEARS = (2020, 2024)

# 「成長率高」與「成長停滯」的分界：近期件數佔該主題總件數的比例 R。
# 全庫基準 R＝38/55＝0.69——高於它才叫成長得比整體快。
STATUS_GROWTH_HIGH = 0.70
STATUS_STAGNANT_BAND = (0.59, 0.79)

# 件數過少時不判狀態。⚠ 切窗後「近期 2 件 vs 早期 1 件」在數學上是成長 100%，
# 但那是噪音不是訊號；本案 13 個主題有 3 個落在這裡。
STATUS_MIN_SAMPLE = 5

# 代表專利取幾件：前三大申請人各一件。⚠ 上限與 `_compute_top_applicants` 的前三大
# 對齊——取多於它就會開始取到沒列在表上的申請人，讀者對不起來。
REPRESENTATIVE_MAX = 3


def classify_topic_status(metrics: dict[str, Any], median_count: float) -> str:
    """依五類條件判定技術狀態；判定優先序見下。

    優先序 **衰退／轉型 → 競爭集中 → 成長 → 成熟 → 新興 → 未分類**。
    ⚠ 衰退排最前面：件數在退的時候，就算集中度也升高了，主訊號仍是「熱度在退」；
    報成「競爭集中」會暗示技術還活著，是相反的決策訊號。

    Parameters
    ----------
    metrics : dict
        patent_count／recent_count／early_count／recent_applicants／early_applicants／
        share_recent／share_early（該主題占同窗全體的比例）／
        concentration_recent／concentration_early（前三大合計占比）。
    median_count : float
        **同通道**各主題件數的中位數，作為「量高／量低」的界線。
        不寫死絕對值——技術通道與功效通道的量級本來就不同。
    """
    total = int(metrics["patent_count"])
    if total < STATUS_MIN_SAMPLE:
        return TOPIC_STATUS_INSUFFICIENT

    recent, early = int(metrics["recent_count"]), int(metrics["early_count"])
    apps_recent, apps_early = int(metrics["recent_applicants"]), int(metrics["early_applicants"])
    share_recent, share_early = float(metrics["share_recent"]), float(metrics["share_early"])
    conc_recent, conc_early = float(metrics["concentration_recent"]), float(metrics["concentration_early"])
    in_window = recent + early
    ratio = (recent / in_window) if in_window else 0.0
    high_volume = total >= median_count

    if recent < early and share_recent < share_early and apps_recent < apps_early:
        return TOPIC_STATUS_DECLINING
    if high_volume and apps_recent < apps_early and conc_recent > conc_early:
        return TOPIC_STATUS_CONCENTRATED
    if recent > early and apps_recent > apps_early:
        return TOPIC_STATUS_GROWING
    if high_volume and STATUS_STAGNANT_BAND[0] <= ratio <= STATUS_STAGNANT_BAND[1]:
        return TOPIC_STATUS_MATURE
    if not high_volume and ratio >= STATUS_GROWTH_HIGH and share_recent > share_early:
        return TOPIC_STATUS_EMERGING
    return TOPIC_STATUS_UNCLASSIFIED


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


def _window_metrics(
    topic_patents: set[int],
    patents: dict[int, dict[str, Any]],
    window: tuple[int, int],
    app_by_patent: dict[int, set[str]],
) -> tuple[int, int, float]:
    """單一時間窗內的（件數, 申請人家數, 前三大合計占比）。

    ⚠ 只算落在窗內的專利；沒有申請年的專利不計入任何一窗（寧可少算也不猜年份）。
    """
    inside = {pid for pid in topic_patents
              if window[0] <= int(patents.get(pid, {}).get("application_year") or 0) <= window[1]}
    if not inside:
        return 0, 0, 0.0
    top3, app_count = _compute_top_applicants(inside, app_by_patent)
    counts = [int(a.get("count", 0)) for a in top3]
    return len(inside), app_count, round(sum(counts[:3]) / len(inside) * 100, 1)


def _pick_representative(
    topic_patents: set[int],
    patents: dict[int, dict[str, Any]],
    app_by_patent: dict[int, set[str]],
    top_applicants: list[dict[str, Any]],
) -> dict[str, str]:
    """挑該主題的代表專利：**最大申請人**的專利中，申請年最新那件。

    🔴 使用者：「分類有了，但缺證據」。說「馬達自鎖技術集中」的下一頁要能指出
    是哪一件、誰的、講什麼——否則標籤沒有落地依據。

    ⚠ 選法必須**確定性可重現**：最大申請人 → 申請年最新 → patent_id 最小。
    同一份資料重跑必須挑到同一件，否則兩次報表對不起來、使用者無從查證。
    """
    if not top_applicants:
        return {"representative": ""}
    # ⚠ **前三大申請人各取一件**（2026-08-03 使用者：「專利號可以取多個」）。
    # 取同一家的三件只代表那一家；各取一件才代表這個主題的主要玩家分別在做什麼，
    # 解讀端也才有素材講出「A 做 X、B 做 Y」的布局差異。
    numbers: list[str] = []
    for applicant in top_applicants[:REPRESENTATIVE_MAX]:
        owned = [pid for pid in topic_patents
                 if applicant["name"] in app_by_patent.get(pid, set())]
        if not owned:
            continue
        # 年份缺漏者排最後（-1），不因為沒年份就被當成最新
        best = max(owned, key=lambda pid: (int(patents.get(pid, {}).get("application_year") or -1), -pid))
        number = str(patents.get(best, {}).get("number") or "")
        if number and number not in numbers:
            numbers.append(number)
    # ⚠ 表格欄**只放專利號**（2026-08-03 使用者定案）。
    #
    # 一度改成「專利號＋文獻備註」，實算後行不通：文獻備註是 **60 字目標線、
    # 100 字上限**的完整句子（`ai_patent_note_runner`），欄寬 2.5in 時每列要 4–7 行，
    # 技術表 5 列就需 6.4–11.2 in，而表格區只有 2.88 in——差 2–4 倍。
    # ⚠ 它也不是欄位值，是句子；且 `ai:patent_note` 是**手動觸發、只補空值**，
    # 沒跑過就是空的，表格會出現整欄空白。
    #
    # 「這幾件專利做了什麼」改由**判讀要點**講——那裡本來就要提代表性專利，
    # 而且不受 60 字與欄寬限制。專利號留在表格供查證，兩邊各司其職。
    # 也不回傳申請人——「前三大申請人」欄已經列出誰在這個主題。
    # `note`（文獻備註）仍在 patents 資料裡供解讀端引用，只是不進表格欄。
    return {"representative": "、".join(numbers)}


def build_topic_effect_table(
    topics: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    normalized_applicants: list[dict[str, Any]],
    patents: dict[int, dict[str, Any]] | None = None,
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
    patents : dict[int, dict] | None
        patent_id → 該專利屬性（``application_year`` / ``number`` / ``title``）。
        給了才算得出技術狀態五類（見 `classify_topic_status`）與代表專利；
        不給時輸出形狀與舊版完全相同，既有呼叫端零修改。
        ⚠ 這是本表原本缺的兩個維度：三個輸入既不帶時間（「成長率高」「成長停滯」
        無從判斷），也不帶專利識別（分類沒有證據可指）。
        ⚠ 兩者共用同一個入口——都是「依 patent_id 查該專利的什麼」，
        拆成 patent_years＋patent_meta 就是同一件事兩個落點。

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
        patents_of_topic = topic_patents[key]
        top3, app_count = _compute_top_applicants(patents_of_topic, app_by_patent)
        info = topic_map.get(key, {})
        # 集中度兩欄（2026-07-29 使用者定案「兩欄都要」）：
        #   top3_share＝前三大合計占比 → 看整體集中程度
        #   max_share ＝最大一家占比   → 看有沒有單一壟斷者
        # 只看其一會誤判：實測使用者資料，「風阻磁阻調節 83%/33%（三家均分）」與
        # 「馬達捲繩自鎖 88%/62%（一家獨大）」前三大占比接近，競爭態勢卻相反。
        # 分母＝該主題專利件數；件數 0 時回 0，不除以零。
        total = len(patents_of_topic)
        counts = [int(a.get("count", 0)) for a in top3]
        top3_share = round(sum(counts[:3]) / total * 100) if total else 0
        max_share = round(max(counts, default=0) / total * 100) if total else 0
        row = {
            "topic_code": code,
            "label": info.get("label", code),
            "source_field": info.get("source_field", source_field),
            "patent_count": total,
            "applicant_count": app_count,
            "top3_share": top3_share,
            "max_share": max_share,
            "top_applicants": top3,
        }
        if patents is not None:
            early = _window_metrics(patents_of_topic, patents, STATUS_EARLY_YEARS, app_by_patent)
            recent = _window_metrics(patents_of_topic, patents, STATUS_RECENT_YEARS, app_by_patent)
            row.update({
                "early_count": early[0], "early_applicants": early[1], "concentration_early": early[2],
                "recent_count": recent[0], "recent_applicants": recent[1], "concentration_recent": recent[2],
            })
            row.update(_pick_representative(patents_of_topic, patents, app_by_patent, top3))
        result.append(row)

    if patents is not None:
        _attach_topic_status(result)
    return result


def _attach_topic_status(rows: list[dict[str, Any]]) -> None:
    """就地補上 Topic 占比與技術狀態（需要全體才算得出，故獨立第二階段）。

    ⚠ 占比與中位數都**按通道各自算**：技術通道與功效通道的量級本來就不同
    （本案中位數 8 vs 6.5），混算會讓功效主題整批被判成「量低」。
    """
    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_source.setdefault(str(row.get("source_field") or ""), []).append(row)

    for channel_rows in by_source.values():
        recent_total = sum(r["recent_count"] for r in channel_rows) or 1
        early_total = sum(r["early_count"] for r in channel_rows) or 1
        counts = [r["patent_count"] for r in channel_rows] or [0]
        median_count = statistics.median(counts)
        for row in channel_rows:
            row["share_recent"] = round(row["recent_count"] / recent_total, 4)
            row["share_early"] = round(row["early_count"] / early_total, 4)
            row["status"] = classify_topic_status(row, median_count)
            row["status_meaning"] = TOPIC_STATUS_MEANINGS.get(row["status"], "")


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
            # 集中度兩欄一併帶進矩陣（2026-07-29 使用者定案「機會四象限和痛點
            # 四象限也改兩欄都要」）。值由 build_topic_effect_table 算好，
            # 此處只轉傳——不重算，避免兩處算法漂移。
            "top3_share": r.get("top3_share", 0),
            "max_share": r.get("max_share", 0),
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
            # 集中度兩欄（同機會矩陣，2026-07-29 定案）：只轉傳、不重算。
            "top3_share": r.get("top3_share", 0),
            "max_share": r.get("max_share", 0),
            "severity": info.get("severity", "unknown"),
            "basis": info.get("basis"),
            "source": info.get("source"),
        })

    return {
        "rows": rows,
        "x_median": float(x_median),
    }
