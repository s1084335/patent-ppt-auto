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
        "⚠ 現況是絕對年份 (2011, 2019)，出自滑雪機那份資料的年度分布"
        "（註解自承「這三個數字是從實際資料切出來的」）。"
        "⚠ 更嚴重的是它是**絕對值**：到 2027 年「近期 2020–2024」會變成三年前，"
        "所有主題一起被判衰退，而且沒有任何警報。",
        derivation="應改為「母體最新申請年往前推 N 年」，N 取自公開延遲（制度事實）",
        pending=True),
    "STATUS_RECENT_YEARS": BasisDeclaration(
        "本次母體",
        "同 STATUS_EARLY_YEARS，是同一組切窗。⚠ 掃描器沒把它標紅只因為註解寫在"
        "上一個常數頭上——訊號式掃描會漏這種，結果要人看過。",
        derivation="同上，起點跟著母體走",
        pending=True),
    "STATUS_GROWTH_HIGH": BasisDeclaration(
        "本次母體",
        "⚠ 註解自承「全庫基準 R＝**38/55**」——55 是滑雪機的 workspace 成員數。"
        "套到別的 workspace 上就是別人的資料混進來（同 §1 母體閘門的病）。",
        derivation="應改為由本次母體的近期占比分布推導（比照 median_count 的作法）",
        pending=True),
    "STATUS_STAGNANT_BAND": BasisDeclaration(
        "本次母體",
        "⚠ 這一項是**機制寫死**不只是值寫死：(0.59, 0.79) 假設「近期占比」這個量"
        "有意義，而那只在早期窗與近期窗長度相近時成立——滑雪機是 9 年 vs 5 年。"
        "換一批年份分布不同的資料，同一個 ratio 代表的意思就不一樣。"
        "⚠ 把值改成 per-run 推導**不會**修好這個。",
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
