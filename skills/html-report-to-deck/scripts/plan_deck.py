"""由 report.json 產生投影片骨架 plan.json——讓 CLI 不必自己決定圖表怎麼分頁。

規則（全部可機械判定，不需人工）：
  · 一頁一張圖，依報表章節順序排列
  · 重複圖（同內容不同檔名）自動剔除
  · 排名類的「第 11–20 名」圖自動剔除（只保留前 10）
  · chip 型圖表（SVG 內有 class="chip"）標記為可重排——使用者 2026-08-11 已授權此類

⚠ 本檔**只用結構判定**（欄名、表格列數、章節順序、SVG class），不比對任何
   與特定批次有關的字串。換一批專利、換一個技術領域都要能直接跑。

除了排頁，還負責把「每批都要做、但很容易漏做」的結構調整**產出成骨架**：
撰稿因此變成填空，而不是要自己想起來該加哪幾頁（2026-08-11 之前靠 SKILL.md
提醒，實測會漏）。產出的骨架頁帶 `_todo`，`check_content.py` 會擋掉沒填完的。

用法：python plan_deck.py <work_dir>            # 需先跑過 extract_report.py
輸出：<work_dir>/plan.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deck_layout import budget, units   # noqa: E402

# 「第 11–20 名」這類延伸排名圖：檔名或章節文字帶有這些訊號
TAIL_RANK = re.compile(r"_more$|第\s*1[1-9]\s*[–\-~]\s*\d+\s*名|11\s*[–\-~]\s*20")

# 主題時間線頁：這種頁最容易被寫成「哪一年有幾件」，要先給演進階段的上層結論。
# ⚠ 不要把「趨勢」放進來——年度申請趨勢頁講的是生命週期與觀測窗口，
#   不是技術演進階段；納入後 annual_trend 會被誤判（2026-08-11 實測踩到）。
TIMELINE = re.compile(r"timeline|演進|時間軸")

# 同一指標的不同階層（..._L4／..._L5）：這種才是「可能收成一頁」的候選。
# ⚠ 不要用「同章節有多張圖」當條件——分群分析章節的三張圖（技術象限、時間線、
#   功效象限）是三件不同的事，收在一起沒有意義（2026-08-11 實測過度觸發）。
LEVEL_SUFFIX = re.compile(r"_L\d+$", re.IGNORECASE)

# Key Players 表的識別欄：有這一欄才拿得到逐案 ID，也才可能補查請求項
ID_COLUMN = "patent_ids"

TODO = "<待撰稿填寫>"


def _table_rows(table) -> int:
    return len((table or {}).get("rows") or [])


def _parse_players(table) -> list[dict]:
    """從帶 patent_ids 欄的表格解析出「主體 → 專利 ID」；找不到就回空。

    ⚠ 只認欄名與欄位位置，不認任何公司名或技術詞。第一欄視為主體名稱
    （本報表格式的固定慣例），合計列（ID 欄為空或 '—'）自動略過。
    """
    head = (table or {}).get("head") or []
    if ID_COLUMN not in head:
        return []
    idx = head.index(ID_COLUMN)
    players = []
    for row in table.get("rows") or []:
        if idx >= len(row):
            continue
        ids = [int(x) for x in re.findall(r"\d+", str(row[idx]))]
        name = str(row[0]).strip()
        if not ids or not name or name in ("—", "-", ""):
            continue
        players.append({"name": name, "patent_ids": ids})
    return players


def _text_page(title: str, hint: str, lines: list[str], *, layout=None) -> dict:
    page = {"chart": None, "section": "（撰稿新增，非報表章節）",
            "title": TODO, "takeaway": TODO, "charts": [], "lines": lines,
            "tag": None, "_todo": hint,
            "source_notes": [], "source_texts": [], "source_table": None}
    if layout:
        page["layout"] = layout
    # 骨架頁沒有對應圖表，鍵順序與圖表頁一致以便 diff
    page["suggested_title"] = title
    return page


def main() -> int:
    work = Path(sys.argv[1])
    report = json.loads((work / "report.json").read_text(encoding="utf-8"))
    charts_dir = work / "charts"

    pages, skipped, checklist = [], [], []
    players: list[dict] = []
    kp_after = None                      # Key Players 頁在 pages 裡的位置

    for sec in report["sections"]:
        def _kept(ch):
            stem = ch["file"].rsplit(".", 1)[0]
            return not ch["dup"] and not (TAIL_RANK.search(stem)
                                          or TAIL_RANK.search(sec["title"] or ""))

        # 會真的進簡報的圖（已扣掉去重與延伸排名），再依「指標家族」分組
        live = [ch["file"].rsplit(".", 1)[0] for ch in sec["charts"] if _kept(ch)]
        family: dict[str, list[str]] = {}
        for stem in live:
            family.setdefault(LEVEL_SUFFIX.sub("", stem), []).append(stem)
        table = sec["tables"][0] if sec["tables"] else None
        rows = _table_rows(table)
        sec_players = _parse_players(table)
        if sec_players:
            players = sec_players

        for ch in sec["charts"]:
            stem = ch["file"].rsplit(".", 1)[0]
            if ch["dup"]:
                skipped.append({"chart": stem, "reason": "與其他圖同內容（去重）"})
                continue
            if TAIL_RANK.search(stem) or TAIL_RANK.search(sec["title"] or ""):
                skipped.append({"chart": stem, "reason": "延伸排名圖，只保留前 10"})
                continue
            svg = charts_dir / ch["file"]
            chip = svg.is_file() and 'class="chip"' in svg.read_text(encoding="utf-8")

            hints = []
            # ① 同一指標的多個階層 → 要求判斷該不該收成一頁。
            # ⚠ 這裡**不自動判斷訊號足不足**：試過用表格列數當代理值，但列數是分類
            #   明細的長度，跟「分類種類多不多」無關（實測 IPC 章節表 12 列，實際
            #   subclass 只有兩種）。與其留一個猜錯的門檻，不如在需要判斷的地方
            #   要求判斷，並把候選圖直接列出來。
            sibs = family.get(LEVEL_SUFFIX.sub("", stem), [])
            if len(sibs) >= 2:
                hints.append(
                    f"收頁判斷：同一指標有 {len(sibs)} 個階層（{'、'.join(sibs)}）。"
                    "看判讀原文確認分類種類夠不夠分頁；不夠就只留最有訊號的一張、"
                    "其餘轉判讀文字收成一頁——頁數要配訊號量，不是配圖表數。")
            # ② 時間演進頁 → 必須先給上層結論
            if TIMELINE.search(stem) or TIMELINE.search(sec["title"] or ""):
                hints.append(
                    "上層結論：先寫一句演進階段結論（階段名用技術語言，"
                    "不要用早期／中期／近期），圖退為證據。")
            # ③ 有逐案 ID → 這是競爭者章節，後面要接玩家頁
            if sec_players:
                hints.append(
                    f"本章節帶 {ID_COLUMN}（{len(sec_players)} 個主體）——"
                    "象限圖只給定位，後面已為你插入「主要玩家在做什麼」骨架頁。")

            pages.append({
                "chart": stem,
                "section": sec["title"],
                "rebuildable_chip_chart": chip,
                "table_rows": rows,
                "level_siblings": sibs,
                "hints": hints,
                "source_notes": sec["notes"],
                "source_texts": sec["texts"],
                "source_table": table,
                "title": "", "takeaway": "", "lines": [], "tag": None,
            })
            if sec_players:
                kp_after = len(pages)

    # ── 每批都要做的結構調整：直接產出骨架 ───────────────────────
    if players:
        skeleton = [f"{p['name']}｜{TODO}" for p in players[:6]]
        skeleton.append(
            "⚠ 來源揭露：未補查資料庫時寫「機構名詞取自報表判讀原文，"
            "更細的請求項用語需回讀 claim，本簡報不推測」；"
            "補查了就改寫成「取自各案獨立項原文（專利資料庫唯讀查詢）」並標來源專利號。")
        pages.insert(kp_after, _text_page(
            "主要玩家在做什麼：構型與演進",
            "填入各家實際主張的機構。有時序推進的用「年份 構型 → 年份 構型」演進鏈，"
            "單一平台的用「關鍵詞・關鍵詞」。左欄標籤上限約 9.3 單位（≈8 個全形字）。"
            "裝不下就拆兩頁，不要縮字。",
            skeleton, layout="label"))
        checklist.append("「主要玩家在做什麼」骨架頁已插入，需填寫（對 RD 價值最高的一頁）")

    # 檢查表逐章節一條（同章節多圖只列一次），避免同一個決定被重複點名
    seen_sections = set()
    for p in pages:
        for h in p.get("hints", []):
            if h.startswith("收頁判斷") and p["section"] not in seen_sections:
                seen_sections.add(p["section"])
                checklist.append(f"{p['section']}：{h}")
            elif h.startswith("上層結論"):
                checklist.append(f"{p['chart']}：主題時間線頁要先給演進階段結論")

    if skipped:
        (charts_dir / "_skipped").mkdir(exist_ok=True)
        for s in skipped:
            f = charts_dir / f"{s['chart']}.svg"
            if f.is_file():
                f.replace(charts_dir / "_skipped" / f.name)

    n_charts = sum(1 for p in pages if p["chart"])
    n_todo = len(pages) - n_charts
    plan = {
        "page_offset": 3,
        # ⚠ 不給 total_slides：收頁與加頁一定會發生，事前算的數字幾乎永遠是錯的
        #   （實測 plan 算 16 頁、實際交付 15 頁），寫出來只會誤導。
        "slide_estimate": (f"封面 1 ＋ 結論 1 ＋ 圖表 ≤{n_charts}（可收頁）"
                           f"＋ 文字頁 ≥{n_todo} ＋ 路線圖 1；實際頁數由撰稿決定"),
        "structure_checklist": checklist,
        "claim_lookup": {
            "available": bool(players),
            "players": players,
            "note": ("只有競爭者構型頁顆粒度不足時才補查，且只補敘述不補統計；"
                     "條件與規則見 SKILL.md「何時該去資料庫補查」。"
                     "要查就跑 fetch_claims.py，不要自己接 psycopg。"),
        },
        "budget_units": {k: round(v, 1) for k, v in budget().items()},
        "budget_note": "字寬單位：中日韓全形算 1.0、半形算 0.55（用 deck_layout.units() 量）",
        "pages": pages,
        "skipped_charts": skipped,
    }
    (work / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1),
                                    encoding="utf-8")

    print(plan["slide_estimate"])
    for i, p in enumerate(pages, start=3):
        if p["chart"]:
            flag = "  ← chip 型，可重排（已授權）" if p["rebuildable_chip_chart"] else ""
            print(f"  P{i:>2} {p['chart']:<34} 判讀 {len(p['source_texts'])} 段"
                  f"｜表 {p['table_rows']} 列{flag}")
            for h in p["hints"]:
                print(f"       ⚠ {h}")
        else:
            print(f"  P{i:>2} {'（骨架頁）' + p['suggested_title']:<34} ← 需撰稿填寫")
    for s in skipped:
        print(f"  略過 {s['chart']:<34} {s['reason']}")
    if players:
        print(f"\n可補查請求項的主體 {len(players)} 個："
              + "、".join(f"{p['name']} {len(p['patent_ids'])} 件" for p in players[:6]))
    if checklist:
        print("\n結構調整檢查表（撰稿必須逐項處理）：")
        for c in checklist:
            print(f"  · {c}")
    print("\n字數上限（單位）：" + "、".join(
        f"{k} {v:.0f}" for k, v in plan["budget_units"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
