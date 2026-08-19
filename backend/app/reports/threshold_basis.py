"""分析判準的**基準來源宣告**（tasks §9.9，2026-08-19）。

## 問題

使用者：「分類器在這環節之前就要依據建立在 workspace 的資料才有意義」
「workspace 是會變的，你也不能只用全庫的當依據」。

實查（`scripts/audit_thresholds.py`）：分析路徑下 50 個模組層數值常數，
**4 個註解自承出自單一資料集**——`STATUS_GROWTH_HIGH = 0.70` 的註解寫著
「全庫基準 R＝**38/55**」，而 55 就是滑雪機那個 workspace 的成員數。

🔴 **這與第 1 段的母體閘門是同一種病**：那個 0.70 套到割草機上，
就是**別的 workspace 的資料混進來**——只是混的是**門檻**不是件數，
而且偽裝成常數，看起來像設計。

## 判準：宣告基準來源，不是「不得出現字面常數」

⚠ 「原始碼沒有裸數字」可以用**把常數搬進設定檔**滿足：閘門綠、行為完全沒變，
而且可追溯性**反而下降**（從「有註解說明它出自滑雪機」變成「設定檔裡一個沒有
來歷的值」）。那是 v5／v7／v9 形式鎖的死法重演——**為了過鎖而搬家**。

正面表述比較硬：**每個判準必須宣告它的基準來源，沒有宣告即紅。**
搬家不會產生宣告，改寫算式也不會。

| 基準 | 意思 | 要求 |
|---|---|---|
| 本次母體 | 由這份報告的母體推導（中位數、分位數…） | 要寫**怎麼推導** |
| 制度事實 | 法規或制度決定（公開延遲、專利年限） | 要附**可外部查證**的依據 |
| 全庫 | 由全庫推導 | **僅限全庫報表**；workspace 報表用它就是混庫 |

## ⚠ 這道閘門守不住什麼

宣告制擋得了「沒說」，擋不了「說謊」——宣告「本次母體」但實際算錯母體，
它看不出來。那一層靠 §1 的母體閘門與兩個 workspace 的數字對帳。
**兩道必須都在**，少一道另一道就會被當成「已經檢查過了」。
"""
from __future__ import annotations

from dataclasses import dataclass

BASES = ("本次母體", "制度事實", "全庫")


@dataclass(frozen=True)
class BasisDeclaration:
    """一個判準的基準宣告。

    `pending=True` 代表**現況兩者皆非**（既不是本次母體也不是制度事實）——
    ⚠ 誠實標出來，不得為了讓表看起來乾淨而硬塞一個基準，那就變成用宣告掩蓋問題。
    """

    basis: str
    why: str
    derivation: str = ""     # basis＝本次母體時必填：怎麼推導
    reference: str = ""      # basis＝制度事實時必填：可外部查證的依據
    pending: bool = False


#: 🔴 判準基準宣告表（唯一定義處）。
#:
#: ⚠ 目前 5 項標 `pending`——它們是掃描抓到的「出自單一資料集」。
#: 宣告表的作用不是宣稱一切正常，是讓**現況可見**。
THRESHOLD_BASIS: dict[str, BasisDeclaration] = {
    "STATUS_EARLY_YEARS": BasisDeclaration(
        "本次母體",
        "🔴 2026-08-19 已改為由本批資料推導（`derive_status_windows`）——"
        "原本是絕對年份 (2011, 2019)，切自滑雪機那份資料的年度分布。"
        "⚠ 絕對年份的失效方式是**靜默**的：一批 2000–2010 的資料，兩窗都落在"
        "資料之外 → recent＝early＝0 → 每個主題都回「未分類」，不報錯也不警告。"
        "本常數**保留**為推導失敗（跨度不足）時的退路。"
        "實測：滑雪機那批推導結果與原常數逐字相同，13 個主題狀態一個都沒變。",
        derivation="母體最新申請年 − 末端排除年數 = 近期窗結束年，再往前推窗長",
        pending=False),
    "STATUS_RECENT_YEARS": BasisDeclaration(
        "本次母體",
        "同 STATUS_EARLY_YEARS，是同一組切窗，已一併改為推導。"
        "⚠ 沿革值得記：掃描器當初沒把它標紅，只因為註解寫在上一個常數頭上"
        "——**訊號式掃描抓的是註解裡的詞、不是行為**，結果仍要人看過。",
        derivation="同上，起點跟著母體走",
        pending=False),
    "STATUS_RECENT_WINDOW_YEARS": BasisDeclaration(
        "本次母體",
        "⚠ 5 年這個長度**沒有制度依據**，它是反推出來的：取 5 才能讓推導結果"
        "等於原本寫死的 (2020, 2024)，也就是「不改動現有報表判定」的安全帶。"
        "換句話說它仍是滑雪機那批的尺，只是不再寫成絕對年份。",
        derivation="待實測：兩個以上 workspace 各跑一次，看窗長對狀態分布的敏感度",
        pending=True),
    "STATUS_TAIL_EXCLUDED_YEARS": BasisDeclaration(
        "制度事實",
        "新案自申請到公開有法定延遲（多數局 18 個月），最末兩年的件數天然偏低。"
        "不排除的話每個主題都會被拉成「衰退」。"
        "⚠ **但現行實作是相對「本批最新年」排除，不是相對「今年」**——"
        "這對一份 2022 年就匯出的舊資料是錯的：2021–2022 對那批而言已經完整，"
        "卻仍被當成截止效應剔除（`test_cluster_analytics` 的 fixture 正好踩到）。"
        "改成相對今年則報表輸出會隨時間變動，傷可重現性。**這是設計分岔，未定案。**",
        derivation="待裁決：相對母體最新年／相對今年／依實際完整度判斷，三選一",
        reference="PCT Article 21(2)(a)／各局早期公開制度：自最早優先日起 18 個月公開",
        pending=True),
    "STATUS_STAGNANT_HALF_WIDTH": BasisDeclaration(
        "本次母體",
        "⚠ 停滯帶要多寬是**選擇**，沒有資料能告訴你。0.10 是從原本寫死的 "
        "(0.59, 0.79) 反解出來的半寬，等於沿用滑雪機那組的寬度。"
        "具名的目的是讓它可以被追問，不是宣稱它有依據。",
        derivation="待實測：兩個以上 workspace 各跑一次，看帶寬對「成熟」判定的敏感度",
        pending=True),
    "STATUS_GROWTH_HIGH": BasisDeclaration(
        "本次母體",
        "🔴 2026-08-19 已改為由本批推導（`derive_growth_baseline`）。"
        "語意本來就是比較型的——原註解寫著「高於它才叫成長得**比整體快**」，"
        "而「整體」隨批次改變。"
        "⚠ 實測還發現這個常數連**自己宣稱的依據**都對不上：註解說「全庫基準 "
        "R＝38/55」，但 55 是 workspace 成員數，判定實際跑在 44 件的分群母體上"
        "——真正的 R＝30/40＝0.75。0.70 既不是別批的尺也不是本批的尺。"
        "改成推導後實測 13 個主題狀態變動 0 個。本常數保留為母體空時的退路。",
        derivation="R＝該通道近期窗件數 ÷（近期＋早期），逐通道各推一份",
        pending=False),
    "STATUS_STAGNANT_BAND": BasisDeclaration(
        "本次母體",
        "🔴 2026-08-19 **帶心**已改為由本批推導（`derive_stagnant_band`，"
        "以 `derive_growth_baseline` 為中心）。"
        "⚠ **但這一項仍標 pending，因為值不是它真正的問題**：原宣告已經寫過"
        "「這是**機制寫死**不只是值寫死」——它**假設**「近期占比」這個量本身有"
        "意義，而那只在早期窗與近期窗長度相近時才成立。"
        "`ratio = recent/(recent+early)` 是拿**5 年的窗**比**9 年的窗**（滑雪機），"
        "比例本身被窗長汙染。"
        "換一批年份分布不同的資料，同一個 ratio 代表的意思就不一樣。"
        "帶心跟著本批走之後，主題與基準用的是同一組窗，**部分**抵銷了這個效應"
        "（兩邊同樣被汙染，比較仍是同尺）；但帶寬 0.10 仍活在被汙染的比例空間裡。"
        "⚠ 把值改成推導**沒有**修好機制，不得因為帶心推導了就當它解決了。",
        derivation="待重新設計：要嘛正規化成年均，要嘛改用不受窗長影響的統計量",
        pending=True),
    "STATUS_MIN_SAMPLE": BasisDeclaration(
        "本次母體",
        "⚠ 註解自承「**本案** 13 個主題有 3 個落在這裡」。"
        "5 這個數字有統計直覺支撐（樣本太小趨勢不可靠），但下限值本身是抓的。",
        derivation="待定：可考慮由母體規模推導，或改宣告為制度／統計慣例並附依據",
        pending=True),
    "MIN_CLUSTERING_DOCUMENTS": BasisDeclaration(
        "本次母體",
        "⚠ 註解自承「實機動因：**滑雪機** 60 筆專利，但各通道可用文件數不足 50」。"
        "它決定「這批資料能不能分群」，而分群是整條 deck 線的前提。",
        derivation="待定：應由該通道的可用文件數分布推導，而非固定 30",
        pending=True),
    "REPRESENTATIVE_MAX": BasisDeclaration(
        "本次母體",
        "代表專利取幾件——與「前三大申請人」對齊，取多於它會取到沒列在表上的"
        "申請人、讀者對不起來。這是**內部一致性**約束，不是從資料抓的數字。",
        derivation="＝`_compute_top_applicants` 的前三大，兩者同源"),
    "TECH_MEANS_MAX": BasisDeclaration(
        "本次母體",
        "功效主題列幾種技術手段——與「前三大申請人」同口徑。",
        derivation="同 REPRESENTATIVE_MAX，維持同一個口徑"),
}


def undeclared_or_missing() -> dict[str, list[str]]:
    """宣告表與實際程式的雙向對帳。

    ⚠ 兩個方向都要驗：
    - 宣告了不存在的常數＝這張表在騙人，而且不會有東西報錯
    - 程式裡有、表上沒有＝漏網（缺席型偏差）
    """
    from backend.app.clustering import runner as clustering_runner
    from backend.app.reports import cluster_analytics

    modules = {
        "cluster_analytics": cluster_analytics,
        "clustering_runner": clustering_runner,
    }
    #: 需要宣告的判準名（🔴 這份清單就是「哪些常數算分析判準」的定義）。
    #: ⚠ 只收**與資料比較**的門檻；版面幾何與單位換算不在此列。
    tracked = set(THRESHOLD_BASIS)

    present: set[str] = set()
    for mod in modules.values():
        present |= {n for n in tracked if hasattr(mod, n)}

    return {
        "declared_but_missing": sorted(tracked - present),
        "in_code_but_undeclared": sorted(
            n for mod in modules.values()
            for n in dir(mod)
            if n.startswith("STATUS_") and n not in tracked
            and isinstance(getattr(mod, n), (int, float, tuple))
            and not isinstance(getattr(mod, n), bool)),
    }
