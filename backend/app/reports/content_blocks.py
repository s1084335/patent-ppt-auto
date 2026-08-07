"""可重用內容元件（P1 tasks 2.3–2.4）：Key Player profiles ＋ 讀圖須知。

定位：**deterministic 資料元件**——報表頁、PPT 與日後 goal-driven SlidePlan
共同消費同一份計算，不各自再算一次。頁面編排不在此（屬 P2）。

Key Player 定案（2026-08-05 使用者）：
- 取前 10 大申請人（本案第 11 名起皆 1 件，正好切在件數 ≥2）。
- 軌跡判準＝**不同申請年 ≥3 個**。⚠ 不是「最晚年 − 最早年 ≥3」——軌跡要的是
  幾個時點，兩件相隔十年也只有兩個點、畫不成軌跡。
- 分頁依據是**有無軌跡**、不是件數排名；共同申請必須點出並拆「共同 / 各自獨立」。

⚠ 輸入走展開口徑（共同申請一件兩列，同 patent_id 出現多次）——共同件數即由
同 patent_id 的其他申請人推得，不另查一張表。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

# 軌跡判準：不同申請年的**時點數**下限（唯一定義處）。
TRAJECTORY_MIN_YEARS = 3
# Key Player 取前幾大（與報表 CHART_ROW_LIMIT 的前十一致口徑同源語意）。
KEY_PLAYER_LIMIT = 10


def key_player_profiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """前十大申請人的 profile：件數、申請年、軌跡、共同/獨立拆分。

    rows 每列需含 applicant_display_name、patent_id、application_year（可缺年）。
    回傳依件數降冪（同件數以名稱排序，確定性）。
    """
    by_applicant: dict[str, set[int]] = defaultdict(set)
    years: dict[str, set[int]] = defaultdict(set)
    holders: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        name = str(row.get("applicant_display_name") or "").strip()
        if not name:
            continue
        pid = int(row.get("patent_id") or 0)
        by_applicant[name].add(pid)
        holders[pid].add(name)
        year = row.get("application_year")
        if year:
            years[name].add(int(year))

    profiles: list[dict[str, Any]] = []
    for name, pids in by_applicant.items():
        partners: dict[str, int] = defaultdict(int)
        joint = 0
        for pid in pids:
            others = holders[pid] - {name}
            if others:
                joint += 1
                for other in others:
                    partners[other] += 1
        year_list = sorted(years[name])
        profiles.append({
            "applicant": name,
            "patent_count": len(pids),
            "years": year_list,
            "has_trajectory": len(year_list) >= TRAJECTORY_MIN_YEARS,
            "joint_count": joint,
            "solo_count": len(pids) - joint,
            "joint_with": [{"applicant": p, "count": c}
                           for p, c in sorted(partners.items(), key=lambda kv: (-kv[1], kv[0]))],
        })
    profiles.sort(key=lambda p: (-p["patent_count"], p["applicant"]))
    return profiles[:KEY_PLAYER_LIMIT]


def key_player_groups(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """依**有無軌跡**分兩組（分頁依據，不是件數排名）。

    ⚠ 取捨已知且刻意：件數較高但無軌跡者會排在技術內容組——規則按軌跡分，
    版面就按軌跡分，不得讓版面反過來覆蓋規則（2026-08-05 定案）。
    """
    profiles = key_player_profiles(rows)
    return {
        "trajectory": [p for p in profiles if p["has_trajectory"]],
        "technical": [p for p in profiles if not p["has_trajectory"]],
    }


def reader_guide_blocks() -> list[dict[str, str]]:
    """讀圖須知：全報告共用的口徑說明（固定內容，不吃資料）。

    ⚠ 刻意不吃 rows：這是「怎麼讀這份報告」的通則，各頁專屬的母體與排除
    原因由各頁註記負責（population.py 唯一定義處），兩邊不重複維護。
    """
    return [
        {
            "title": "計數單位",
            "body": "全報告只有兩個單位：「件」（專利件數）與「群」（分群主題數）。"
                    "同族合併後仍以「件」計，不改稱家族數。",
        },
        {
            "title": "同族合併",
            "body": "同一發明在多國申請會產生多件專利；標示「同族合併後」的數字"
                    "已依 WIPS 同族 ID 併為一件，用於看「有幾個發明」而非「有幾份文件」。",
        },
        {
            "title": "共同申請",
            "body": "一件專利可能由多位申請人共同提出。申請人相關統計採展開口徑"
                    "（各自計數），因此件數總和會大於專利總件數——這是專利分析慣例，"
                    "頁面均已加註。",
        },
        {
            "title": "分類覆蓋",
            "body": "外觀設計案沒有技術請求項，不進技術／功效分群，其分類欄為空白"
                    "屬正確呈現；各頁母體與排除原因均標在頁尾。",
        },
    ]
