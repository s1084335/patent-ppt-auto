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
TOPIC_STATUS_EMERGING = "近期新進"
TOPIC_STATUS_GROWING = "申請成長"
TOPIC_STATUS_MATURE = "申請趨穩"
TOPIC_STATUS_CONCENTRATED = "少數申請人集中"
TOPIC_STATUS_DECLINING = "申請下降"
TOPIC_STATUS_UNCLASSIFIED = "未分類"
TOPIC_STATUS_INSUFFICIENT = "樣本不足"

# 「意義」不是裝飾——狀態名只說了是什麼，讀者要的是「所以呢」（C-6）。
TOPIC_STATUS_MEANINGS: dict[str, str] = {
    TOPIC_STATUS_EMERGING: "近三年才開始出現申請",
    TOPIC_STATUS_GROWING: "申請件數與申請人數同步上升",
    TOPIC_STATUS_MATURE: "申請件數持平，方向趨於固定",
    TOPIC_STATUS_CONCENTRATED: "申請持續但集中於少數申請人",
    TOPIC_STATUS_DECLINING: "近年申請件數明顯下降",
    TOPIC_STATUS_INSUFFICIENT: "件數過少，趨勢判斷不可靠",
}

# 時間窗（申請年）。
#
# 🔴 2026-08-19：改為**由本批資料推導**（`derive_status_windows`）。
# 原本這兩個是絕對年份，切自滑雪機那批的年度分布：
#   2011–2019 共 17 件（9 年）／2020–2024 共 38 件（5 年）／2025–2026 僅 5 件。
# ⚠ 絕對年份套到別批資料上的失效方式是**靜默**的：例如一批 2000–2010 的資料，
#   兩個窗都落在資料之外 → recent＝early＝0 → `classify_topic_status` 的四個比較
#   全是 `0 < 0`＝假 → **每個主題都回「未分類」**，不報錯也不警告。
#
# 下列兩個常數**保留**，降為兩種用途：①推導失敗（跨度不足）時的退路
# ②`threshold_basis` 雙向對帳的對象（刪了會變成「宣告了但常數不存在」）。
STATUS_EARLY_YEARS = (2011, 2019)
STATUS_RECENT_YEARS = (2020, 2024)

# 推導參數。⚠ 具名而非寫在算式裡——寫在算式裡就沒人知道它代表什麼，也改不動。
#: 近期窗長度（年）。固定不隨資料量浮動，否則兩批報表的「近期」不是同一回事。
STATUS_RECENT_WINDOW_YEARS = 5
#: 末端排除幾年的**退路值**。**資料截止效應**：新案還在審查中未公開，件數天然
#: 偏低，併進近期窗會把每個主題都拉成「衰退」。故整段排除，不計入任何一窗。
#: 🔴 2026-08-19 起正式路徑改由 `derive_tail_exclusion` 依**法律狀態**推導；
#: 本常數只在狀態資料缺席時使用。
STATUS_TAIL_EXCLUDED_YEARS = 2

#: 判定「這一年還沒跑完」的未決比門檻。
#: 🔴 兩批實測夾出來的：50% 會多排除滑雪機的 2024、70% 會少排除割草機的 2025，
#: **只有 60% 在兩批上都重現現行行為**。
#: ⚠ 誠實界線：判準是「與既有行為相容」，而既有行為（固定排除末 2 年）本身
#: 沒被獨立驗證過——60% 是兩批夾出的相容區間，**不是被證明的最佳值**。
STATUS_TAIL_PENDING_RATIO = 0.60


def derive_tail_exclusion(patents) -> int:
    """依法律狀態推導要排除幾個末端年份（資料截止效應）。

    🔴 2026-08-19 使用者裁決：「應該以專利狀態去推，不直接用年分」。

    原本固定排除末 2 年，在滑雪機與割草機**剛好都對**——但那是巧合，
    它不知道自己在排除什麼，只是數了兩年。改用**未決比**（該年可見案件中仍在
    審查／公開的比例）直接量到真相：未決比高代表「這一年看得到的案子幾乎都還
    沒有結局」，看到的只是最早公開的那一小撮。

    ⚠ 這同時解掉「相對本批最新年 vs 相對今年」的兩難：**不需要知道今天是哪一年**。
    一份 2022 年匯出的舊資料，它的 2021 年未決比自然偏高，會被正確排除；
    不必靠「相對今年」去猜，報表也不會隨時間變動（可重現性保住）。

    ⚠ 只排除**連續的末端**：中間年份未決比高（割草機 2018 年 90% 是個別案件還在
    審）不代表那一年沒跑完。截止效應必然從最新年開始連續往回。

    ⚠ 三個退化情形各有理由：
    - 沒有年份／沒有任何可判狀態 → 回常數，**不是回 0**。回 0 等於當成「全部
      已結案」，末端的假低值會把每個主題拉成衰退。
    - 每一年都超過門檻 → 至少留一年，否則兩窗全空、每個主題回「未分類」，
      正是本輪在修的那種靜默失效。
    """
    from backend.app.mappings.legal_status import (
        STATUS_PENDING, normalize_legal_status)

    if not patents:
        return STATUS_TAIL_EXCLUDED_YEARS
    total: dict[int, int] = {}
    pending: dict[int, int] = {}
    judged = 0
    for p in patents:
        year = p.get("application_year")
        if not isinstance(year, int):
            continue
        raw = p.get("legal_status")
        if raw is None or not str(raw).strip():
            continue                      # 狀態缺席者不計入分母，避免稀釋未決比
        judged += 1
        total[year] = total.get(year, 0) + 1
        if normalize_legal_status(raw) == STATUS_PENDING:
            pending[year] = pending.get(year, 0) + 1
    if not judged or not total:
        return STATUS_TAIL_EXCLUDED_YEARS

    years = sorted(total)
    excluded = 0
    for year in reversed(years):
        if pending.get(year, 0) / total[year] < STATUS_TAIL_PENDING_RATIO:
            break                          # 一遇到已跑完的年份就停（只砍連續末端）
        excluded += 1
    return min(excluded, len(years) - 1)   # 至少留一年


def derive_status_windows(years, *, tail_excluded: int | None = None
                          ) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """從本批資料的申請年推導（早期窗, 近期窗）；跨度不足以切窗時回 `None`。

    規則就是原本註解已經寫下的那三件事，只是改成對**任何一批**都成立：
    末 `STATUS_TAIL_EXCLUDED_YEARS` 年排除 → 其餘的最後
    `STATUS_RECENT_WINDOW_YEARS` 年為近期窗 → 再往前全部為早期窗。

    ⚠ 套在滑雪機那批（2011–2026）上的結果**與原本寫死的常數逐字相同**
    ——`test_status_windows_relative` 鎖住這點。移除資料綁定不該順手改動
    現有報表的判定結果，那是兩個決定。

    ⚠ 回 `None` 而不是回一組落在資料外的窗：沉默地回假窗會讓所有主題變成
    「未分類」而沒有人知道為什麼。
    """
    usable = sorted({int(y) for y in (years or [])
                     if isinstance(y, (int, float)) and not isinstance(y, bool)}
                    ) if years else []
    if not usable:
        return None
    # ⚠ 排除年數由呼叫端傳入（正式路徑走 `derive_tail_exclusion` 依狀態推導）；
    #   未傳時退回常數，既有呼叫端零修改。
    tail = (STATUS_TAIL_EXCLUDED_YEARS if tail_excluded is None
            else max(0, int(tail_excluded)))
    cutoff = usable[-1] - tail                            # 末端排除後的最後一年
    recent_start = cutoff - STATUS_RECENT_WINDOW_YEARS + 1
    early_end = recent_start - 1
    # 早期窗至少要有一年才算切得出來——否則整批都被近期窗吃掉，比不到東西。
    if early_end < usable[0]:
        return None
    return (usable[0], early_end), (recent_start, cutoff)

# 「成長率高」與「成長停滯」的分界：近期件數佔（近期＋早期）的比例 R。
#
# 🔴 2026-08-19 改為**由本批推導**（`derive_growth_baseline`）。
# 語意本來就是比較型的——原註解寫著「高於它才叫成長得**比整體快**」，
# 而「整體」隨批次改變。寫成常數等於拿甲批的整體去衡量乙批的主題。
#
# ⚠ 實測還發現這個常數連**它自己宣稱的依據**都對不上：原註解說「全庫基準
#   R＝38/55＝0.69」，但 55 是 **workspace 成員數**，判定實際跑在 **44 件的
#   分群母體**上——真正的 R＝30/40＝**0.75**。0.70 既不是別批的尺也不是本批的尺。
# ⚠ 改成推導後實測滑雪機 13 個主題狀態**變動 0 個**（主因是「件數與家數同步
#   上升就判成長」的優先序排在這兩個門檻之前，多數主題走不到這裡）。
#
# 下列兩個常數**保留**為推導失敗（母體空）時的退路與基準宣告的對象。
STATUS_GROWTH_HIGH = 0.70
STATUS_STAGNANT_BAND = (0.59, 0.79)

#: 停滯帶的半寬。⚠ 這是**選擇**不是推導——帶要多寬沒有資料能告訴你，
#: 具名是為了讓它可以被宣告、被追問，而不是藏在 (0.59, 0.79) 這組數字裡。
STATUS_STAGNANT_HALF_WIDTH = 0.10


def derive_growth_baseline(recent_total: int, early_total: int) -> float | None:
    """本批的「整體成長率」R＝近期 ÷（近期＋早期）；母體空時回 `None`。

    ⚠ 回 `None` 而不是 0：回 0 會讓每個主題的 `ratio >= baseline` 都成立，
    整批誤判成「新興」——那是比沒有基準更糟的失效方式。
    """
    total = int(recent_total) + int(early_total)
    if total <= 0:
        return None
    return int(recent_total) / total


def derive_stagnant_band(baseline: float) -> tuple[float, float]:
    """停滯帶＝以本批基準為中心、`STATUS_STAGNANT_HALF_WIDTH` 為半寬。

    ⚠ 夾限在 [0, 1]：基準接近兩端時帶會溢出比例的定義域，
    不夾限會出現負數下界（等於帶變單邊）或 >1 的上界（等於帶失效）。
    """
    lo = max(0.0, baseline - STATUS_STAGNANT_HALF_WIDTH)
    hi = min(1.0, baseline + STATUS_STAGNANT_HALF_WIDTH)
    return (round(lo, 4), round(hi, 4))

# 件數過少時不判狀態。⚠ 切窗後「近期 2 件 vs 早期 1 件」在數學上是成長 100%，
# 但那是噪音不是訊號。
#
# 🔴 2026-08-19 依據改寫：原註解是「本案 13 個主題有 3 個落在這裡」——那是用
# 本批的分布去 justify 一個本該與批次無關的門檻。量級的來源是 **Cochran 期望
# 次數規則**（列聯表／比例比較每格期望次數 ≥ 5，低於此常態近似不成立）。
# ⚠ 誠實界線：這裡**沒有真的跑檢定**，只是拿總件數當閘門，而且擋的是主題
#   **總件數**、比「每格 ≥5」寬鬆。引用慣例是為了說明 5 從何而來，不是宣稱做了檢定。
# ⚠ 本項**不推導**：它問的是「樣本夠不夠讓計算有意義」，不隨批次縮放。
#   改成推導是循環論證——「這批少所以把標準放寬」，而少正是不可靠的時候。
STATUS_MIN_SAMPLE = 5

# 代表專利取幾件：前三大申請人各一件。⚠ 上限與 `_compute_top_applicants` 的前三大
# 對齊——取多於它就會開始取到沒列在表上的申請人，讀者對不起來。
REPRESENTATIVE_MAX = 3

# 功效主題列出幾種技術手段（2026-08-03 使用者：「我要知道功效可以用那些技術手段達成」）。
# 實機 8 個功效主題各命中 1–4 種，取三覆蓋絕大多數；與「前三大申請人」同口徑。
TECH_MEANS_MAX = 3


def classify_topic_status(metrics: dict[str, Any], median_count: float,
                          *, growth_high: float | None = None,
                          stagnant_band: tuple[float, float] | None = None) -> str:
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
    # ⚠ 未傳入就退回常數：既有呼叫端零修改，且推導失敗（母體空）時仍有退路。
    high_bar = STATUS_GROWTH_HIGH if growth_high is None else growth_high
    band = STATUS_STAGNANT_BAND if stagnant_band is None else stagnant_band

    if recent < early and share_recent < share_early and apps_recent < apps_early:
        return TOPIC_STATUS_DECLINING
    if high_volume and apps_recent < apps_early and conc_recent > conc_early:
        return TOPIC_STATUS_CONCENTRATED
    if recent > early and apps_recent > apps_early:
        return TOPIC_STATUS_GROWING
    if high_volume and band[0] <= ratio <= band[1]:
        return TOPIC_STATUS_MATURE
    if not high_volume and ratio >= high_bar and share_recent > share_early:
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
        # 🔴 J-3（2026-08-04）：連字號換成 U+2011（不斷行連字號）。
        # PowerPoint 把 ASCII `-` 當合法斷點，`2019-0247710` 會被折成兩行；
        # 而自動換行不寫進 XML，程式掃不到，只有轉圖／實機看得見。
        # 顯示長相相同、網頁端同樣受益；關口只有這裡，組版端零改動。
        number = number.replace("-", "‑")
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

    # 🔴 2026-08-19：時間窗改由**本批資料**推導，不再用寫死的絕對年份。
    # ⚠ 推導一次、兩個通道共用：逐主題各推一次的話，主題間的「近期」會不一樣，
    #   跨主題比較就失去意義（而症狀只會表現成幾個主題的狀態怪怪的）。
    # ⚠ 跨度不足以切窗時退回常數：那組值仍出自滑雪機，但**退路比假窗誠實**
    #   ——回一組落在資料外的窗會讓每個主題都變「未分類」且無人知情。
    # 🔴 2026-08-19 使用者裁決：截止效應改**以專利狀態推**，不直接數年份。
    _members = list((patents or {}).values())
    _tail = derive_tail_exclusion(_members)
    _windows = derive_status_windows(
        [p.get("application_year") for p in _members], tail_excluded=_tail)
    early_window, recent_window = _windows if _windows else (
        STATUS_EARLY_YEARS, STATUS_RECENT_YEARS)

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
            early = _window_metrics(patents_of_topic, patents, early_window, app_by_patent)
            recent = _window_metrics(patents_of_topic, patents, recent_window, app_by_patent)
            row.update({
                "early_count": early[0], "early_applicants": early[1], "concentration_early": early[2],
                "recent_count": recent[0], "recent_applicants": recent[1], "concentration_recent": recent[2],
            })
            row.update(_pick_representative(patents_of_topic, patents, app_by_patent, top3))
            row.update(_status_breakdown(patents_of_topic, patents))
        result.append(row)

    if patents is not None:
        _attach_topic_status(result)
    _attach_technical_means(result, topic_patents, topic_map)
    return result


def _status_breakdown(
    patent_ids: set[int],
    patents: dict[int, dict[str, Any]],
) -> dict[str, int]:
    """該主題的法律狀態分解（2026-08-18，§7e）：審查中／已授權／失效／未知。

    用途：結論頁拿掉期程後，改依**外部訊號**排序——該主題有多少件他人的審查中
    案件。那是對手給的時間壓力，可查證；`短期 0–3 個月` 則是系統編的。

    ⚠ 桶收斂一律走 `mappings/legal_status.normalize_legal_status`（唯一定義處），
    本函式**不比對任何狀態字面**。

    ⚠ 沒有狀態的算進「未知」而**不是不算**——不算的話分解合計會對不上件數，
    而讀者不會發現少了什麼（缺席型偏差）。

    ⚠ 分母是**分群母體**（滑雪機 44）不是 workspace 成員（55）：外觀設計案沒有
    獨立項文字，分不了群。合計寫成 55 等於把「11 件被靜默排除」偽裝成
    「全都算到了」。這裡只保證合計＝該主題件數；整體 44 vs 55 的揭露見 chart_runner。
    """
    from backend.app.mappings.legal_status import (
        STATUS_ALIVE,
        STATUS_DEAD,
        STATUS_PENDING,
        normalize_legal_status,
    )

    tally = {"pending_count": 0, "granted_count": 0,
             "inactive_count": 0, "unknown_status_count": 0}
    bucket_key = {
        STATUS_PENDING: "pending_count",
        STATUS_ALIVE: "granted_count",
        STATUS_DEAD: "inactive_count",
    }
    for pid in patent_ids:
        raw = (patents.get(pid) or {}).get("legal_status")
        key = bucket_key.get(normalize_legal_status(raw), "unknown_status_count")
        tally[key] += 1
    return tally


def _attach_technical_means(
    rows: list[dict[str, Any]],
    topic_patents: dict[tuple[str, str], set[int]],
    topic_map: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """就地替**功效列**補上「主要技術手段」——這個功效是用哪些技術做出來的。

    做法：同一批專利同時有技術通道與功效通道的分派，取交集即可。
    ⚠ 純交集統計，不需要 AI 也不需要新資料來源——兩個通道的 assignment 都已存在。

    ⚠ 只加在功效列。技術列不做反向對照（2026-08-03 使用者：技術通道維持現狀）；
    真要做也應該是另一個欄位，不是同一個鍵兩種語意。
    ⚠ 值是**字串**不是結構化 list：這欄純顯示，做成 list 會逼 PPT 與網頁兩端
    各寫一份格式化邏輯（`top_applicants` 已經是這樣，不要再多一個）。
    """
    tech_sets = [
        (str(topic_map.get(key, {}).get("label") or key[0]), patents)
        for key, patents in topic_patents.items()
        if key[1] == SOURCE_FIELD_TECHNICAL and patents
    ]
    if not tech_sets:
        return
    for row in rows:
        if row.get("source_field") != SOURCE_FIELD_EFFECT:
            continue
        own = topic_patents.get((row["topic_code"], SOURCE_FIELD_EFFECT), set())
        if not own:
            continue
        hits = [(name, len(own & pats)) for name, pats in tech_sets if own & pats]
        # 件數多的在前；同件數依名稱排序，避免同資料兩次執行順序不同。
        hits.sort(key=lambda item: (-item[1], item[0]))
        row["tech_means"] = "、".join(
            f"{name} {count}" for name, count in hits[:TECH_MEANS_MAX])


def _attach_topic_status(rows: list[dict[str, Any]]) -> None:
    """就地補上 Topic 占比與技術狀態（需要全體才算得出，故獨立第二階段）。

    ⚠ 占比與中位數都**按通道各自算**：技術通道與功效通道的量級本來就不同
    （本案中位數 8 vs 6.5），混算會讓功效主題整批被判成「量低」。
    """
    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_source.setdefault(str(row.get("source_field") or ""), []).append(row)

    for source_field, channel_rows in by_source.items():
        # ⚠ 原始總數與除法用的分母要分開：下面兩個 `or 1` 是除零保護，
        #   拿它去算成長基準會把「近期 0 件」變成「近期 1 件」，基準整個偏掉。
        recent_sum = sum(r["recent_count"] for r in channel_rows)
        early_sum = sum(r["early_count"] for r in channel_rows)
        recent_total = recent_sum or 1
        early_total = early_sum or 1
        counts = [r["patent_count"] for r in channel_rows] or [0]
        median_count = statistics.median(counts)
        # 🔴 2026-08-19：「成長得比整體快」的基準由**本通道的整體**推導，
        # 不再用寫死的 0.70。⚠ 逐通道各推一份——技術與功效的量級本來就不同，
        # 共用一個基準等於拿技術的尺量功效（同 median_count 的理由）。
        baseline = derive_growth_baseline(recent_sum, early_sum)
        growth_high = STATUS_GROWTH_HIGH if baseline is None else baseline
        stagnant_band = (STATUS_STAGNANT_BAND if baseline is None
                         else derive_stagnant_band(baseline))
        # 🔴 狀態分類**只給技術通道**（2026-08-03 使用者定案）。
        # 使用者實機看到「提升訓練成效 → 成長技術」後指出這是技術通道的設計。
        # 他 08-02 定的五類講的是技術；功效通道的用途是**跟技術主題對照**，
        # 不自己判演進狀態。⚠ 分類邏輯本身沒問題（中位數本來就分通道算），
        # 錯的是把它套到功效上——所以是不套用，不是改演算法。
        # ⚠ 占比兩欄（share_recent／share_early）維持兩通道都算：
        # 它們是機會矩陣與解讀的輸入，不只服務 status。
        classify = source_field == SOURCE_FIELD_TECHNICAL
        for row in channel_rows:
            row["share_recent"] = round(row["recent_count"] / recent_total, 4)
            row["share_early"] = round(row["early_count"] / early_total, 4)
            if classify:
                row["status"] = classify_topic_status(
                    row, median_count,
                    growth_high=growth_high, stagnant_band=stagnant_band)
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


# 🔴 2026-08-04：痛點板（pain_point_quadrant）已整個刪除（使用者定案）。
# 07-29 起本就停產（「整個藏起來，等市場線做好再放出來」），市場線也已定案移除，
# 留著的程式每次改字級、用詞、版面都多一份要同步、又永遠驗不到。
