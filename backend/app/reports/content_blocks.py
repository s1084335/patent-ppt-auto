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


def key_player_profiles(
    rows: list[dict[str, Any]],
    ranking: list[str] | None = None,
) -> list[dict[str, Any]]:
    """前十大競爭者的 profile：件數、申請年、軌跡、共同/獨立拆分、四面向。

    🔴 **名單以排名頁為準**（2026-08-07 使用者定案）：`ranking`＝申請人排名頁
    的順序名單（前十），本函式**不在此另用件數切一次**——排名頁若換口徑或
    經人工調整，兩邊必須是同一份名單，否則就是同一份知識兩個落點。
    ⚠ 排名頁有、本批資料查無的名字一律略過，不捏造空 profile。
    ranking 未給（例如尚未產排名頁）才退回件數降冪＋名稱排序並取前十。

    rows 每列需含 applicant_display_name、patent_id、application_year（可缺年）。
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
            # ⚠ 該家**全部**專利的 id（2026-08-10 使用者定案「每家全取」）。
            # 用途：讓 CLI 知道要查哪些專利——摘要由 CLI 自行透過 MCP
            # `query_database` 讀 `patents."文獻備註"` 產生，不在此預先算。
            # 排序固定（升冪）：同一批資料兩次產出要給 CLI 同一份清單，
            # 否則 prompt 變動會讓 AI 產出無謂地不一致。
            "patent_ids": sorted(pids),
        })
    if ranking:
        by_name = {p["applicant"]: p for p in profiles}
        profiles = [by_name[name] for name in ranking if name in by_name]
    else:
        profiles.sort(key=lambda p: (-p["patent_count"], p["applicant"]))
        profiles = profiles[:KEY_PLAYER_LIMIT]
    # 四面向掛在 Key Player 上（2026-08-07 使用者定案：用在 10 個競爭者那裡，
    # **申請人排名頁不動**）——同一份計算，不在兩處各算一次。
    strength = {p["applicant"]: p for p in rights_strength_profiles(rows)}
    for profile in profiles:
        extra = strength.get(profile["applicant"], {})
        for key in ("family_count", "country_count", "granted_count", "pending_count",
                    "dead_count", "unknown_count", "kind_counts",
                    "topic_count", "ipc_subclass_count"):
            if key in extra:
                profile[key] = extra[key]
    return profiles


def key_player_groups(
    rows: list[dict[str, Any]],
    ranking: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """依**有無軌跡**分兩組（分頁依據，不是件數排名）。

    ⚠ 取捨已知且刻意：件數較高但無軌跡者會排在技術內容組——規則按軌跡分，
    版面就按軌跡分，不得讓版面反過來覆蓋規則（2026-08-05 定案）。
    """
    profiles = key_player_profiles(rows, ranking=ranking)
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


def rights_strength_profiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """權利強度四面向（Q10，2026-08-05 定案）：**並列，不合成分數**。

    面向與用途——回答「這家公司的專利實力是什麼形狀」，而不是「幾分」：
    - **布局量**：件／族／國三個數分開看。件多族少＝同一發明多國申請；
      族少國多＝地域防禦廣（實測美商扭矩 4 件 1 族 4 國即此形狀）。
    - **法律穩定性**：授權／審查中／失效件數（走 transforms/legal_status
      四桶唯一定義處，不自行比對字面）。實測孟喬 5 件 0 授權 2 失效＝
      「僅具前案價值」，敘述可直接這樣寫。
    - **技術廣度**：涉入幾個技術主題／幾個 IPC subclass。⚠ 2026-08-07 補做——
      原始需求（問題 10）四項是「布局強度＋技術廣度＋法律穩定性＋權利範圍」，
      先前實作漏了廣度。件數集中單一主題與跨三個主題，壁壘完全不同。
    - **專利種類**：發明／新型／設計三分（走 transforms/patent_kind）。

    🔴 **合成分數已否決**（同日定案）：權重是主觀選擇，簡報上出現「權利強度
    82 分」會被當成客觀指標；且合成會壓掉上述形狀資訊。本函式刻意不回任何總分。
    ⚠ 不做請求項數／權利範圍維度（範例刻意不放，易被誤讀成專利品質）。

    rows 走展開口徑（共同申請各自計數），需含 applicant_display_name、
    patent_id、country_code、family_id、legal_status、patent_type、document_kind。
    """
    from backend.app.transforms.legal_status import (
        BUCKET_DEAD,
        BUCKET_GRANTED,
        BUCKET_PENDING,
        BUCKET_UNKNOWN,
        status_bucket,
    )
    from backend.app.transforms.patent_kind import patent_kind

    bucket_field = {BUCKET_GRANTED: "granted_count", BUCKET_PENDING: "pending_count",
                    BUCKET_DEAD: "dead_count", BUCKET_UNKNOWN: "unknown_count"}
    acc: dict[str, dict[str, Any]] = {}
    seen: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        name = str(row.get("applicant_display_name") or "").strip()
        if not name:
            continue
        pid = int(row.get("patent_id") or 0)
        if pid in seen[name]:
            continue          # 同申請人同專利多列（多國別名等）只算一次
        seen[name].add(pid)
        entry = acc.setdefault(name, {
            "applicant": name, "patent_count": 0,
            "families": set(), "countries": set(),
            "granted_count": 0, "pending_count": 0,
            "dead_count": 0, "unknown_count": 0,
            "kind_counts": defaultdict(int),
            "topics": set(), "ipc_subclasses": set(),
        })
        entry["patent_count"] += 1
        family = str(row.get("family_id") or "").strip() or f"__pid{pid}"
        entry["families"].add(family)
        country = str(row.get("country_code") or "").strip()
        if country:
            entry["countries"].add(country)
        entry[bucket_field[status_bucket(row.get("legal_status"))]] += 1
        entry["kind_counts"][patent_kind(row)] += 1
        # 技術廣度（問題 10 原始需求四項之一）：涉入幾個主題／幾個 IPC subclass。
        # ⚠ 件數再多都集中一個主題，壁壘與跨三個主題完全不同——件數看不出這件事。
        topic = str(row.get("topic_key") or row.get("topic_code") or "").strip()
        if topic:
            entry["topics"].add(topic)
        ipc = str(row.get("ipc_subclass") or row.get("Orig. IPC(Main)") or "").strip()
        if ipc:
            entry["ipc_subclasses"].add(ipc[:4])

    profiles: list[dict[str, Any]] = []
    for entry in acc.values():
        profiles.append({
            "applicant": entry["applicant"],
            "patent_count": entry["patent_count"],
            "family_count": len(entry["families"]),
            "country_count": len(entry["countries"]),
            "granted_count": entry["granted_count"],
            "pending_count": entry["pending_count"],
            "dead_count": entry["dead_count"],
            "unknown_count": entry["unknown_count"],
            "kind_counts": dict(entry["kind_counts"]),
            "topic_count": len(entry["topics"]),
            "ipc_subclass_count": len(entry["ipc_subclasses"]),
        })
    profiles.sort(key=lambda p: (-p["patent_count"], p["applicant"]))
    return profiles[:KEY_PLAYER_LIMIT]
