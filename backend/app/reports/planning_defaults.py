"""PPT 單一入口的預設策略（EXP-020，2026-08-09 使用者定案）。

「ppt 入口要統一一個，使用者有需求就以需求為重心，沒需求也要能跑出符合
我給你的兩個範例的專業程度」。

⚠ 未填目標 **不是**「退回固定頁序」——那是規劃失敗時的保底。未填目標時仍走
規劃，只是用預設目標與預設敘事鏈，品質標準不打折。

⚠ 預設敘事鏈取自兩份範例的共同 DNA（`content_standard.md` 第一節七條硬標準
與第二節目標架構）：結論先行 → 證據（時間／空間／技術／競爭）→ Key Player
深入 → 判讀說明。⚠ 它是**方向不是模板**：版型仍是備選庫，出哪幾頁由內容決定。
"""
from __future__ import annotations

from typing import Any

#: 未填目標時的預設最大目標——涵蓋兩份範例都在回答的三件事。
DEFAULT_NORTH_STAR_GOAL = (
    "盤點本批專利的技術布局與競爭格局，指出可切入的方向與需迴避的高密度區"
)

#: 預設敘事鏈（方向，不是固定頁序）。
DEFAULT_DIRECTIONS: tuple[str, ...] = (
    "結論先行：開頭就給技術分期與三個具名發現，不要把結論留到最後",
    "證據依序鋪陳：時間（申請趨勢）→ 空間（地域布局）→ 技術（分類與主題）→ 競爭（申請人）",
    "Key Player 深入：對主要競爭者給定位與軌跡，不只列件數排名",
    "收尾給判讀說明：母體口徑、可觀測性偏差與資料限制要講清楚",
)

#: 預設受眾（範例兩份都是給研發與 IP 決策者看的）。
DEFAULT_AUDIENCE = "研發與智財決策者"

DEFAULT_PAGE_BUDGET = 12


def build_brief(
    *,
    snapshot_id: str,
    workspace_id: int,
    selected_charts: list[dict[str, Any]],
    north_star_goal: str = "",
    audience: str = "",
    page_budget: int | None = None,
) -> dict[str, Any]:
    """組 ReportBrief：使用者填了就以他的為準，沒填就用預設策略。

    回傳帶 `used_default_goal` 供下游揭露（產出要能說明這份是依什麼編排的）。
    """
    goal = (north_star_goal or "").strip()
    used_default = not goal
    return {
        "north_star_goal": goal or DEFAULT_NORTH_STAR_GOAL,
        "audience": (audience or "").strip() or DEFAULT_AUDIENCE,
        "page_budget": int(page_budget or DEFAULT_PAGE_BUDGET),
        "workspace_id": workspace_id,
        "snapshot_id": snapshot_id,
        "selected_charts": selected_charts,
        "directions": list(DEFAULT_DIRECTIONS),
        "used_default_goal": used_default,
    }


def describe_fallback(reason: str) -> dict[str, str]:
    """規劃失敗時的保底說明——降級必須現形，不得靜默。"""
    return {
        "note": "本份 PPT 未依規劃編排（規劃失敗，改用固定頁序保底）",
        "reason": reason,
    }
