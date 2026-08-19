"""在組版前先驗 content.json：欄位齊不齊、字數超不超、圖表對不對得上。

為什麼要有這一步：make_deck.py 要等 PowerPoint 排完才知道溢出，回饋慢且看不出是哪一句太長。
本腳本純算字寬單位，秒級回饋，直接指出「哪個欄位、超出幾個字」。

用法：python check_content.py <content.json> [png_dir]
回傳碼：0 = 可以組版；1 = 有問題（缺欄位、字數超標、圖表對不上）
"""
from __future__ import annotations

import json
import io
import re
import sys
from pathlib import Path

from PIL import Image

def _force_utf8_console() -> None:
    """Windows 主控台輸出中文用；⚠ **只在當腳本執行時呼叫**。

    原本這段寫在模組層，於是 `import check_content` 就會把呼叫端的 `sys.stdout`
    換掉——pytest 的輸出攔截物件被替換後，teardown 會丟
    `ValueError: I/O operation on closed file`，而失敗訊息指向測試本身，
    完全看不出真因在這裡（2026-08-18 實際踩到）。
    """
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deck_layout import (LAYOUTS, MIN_CHART_PT_MULTI, budget,   # noqa: E402
                         label_page_fit, predict_chart_pt, units)

# ⚠ 2026-08-18（§7d）：`roadmap_title`／`roadmap_takeaway`／`roadmap` 已移除
#   ——路線圖頁併入結論頁、期程整個拿掉。留在必填清單裡會逼 CLI 生出一個
#   不會被畫出來的區塊。
REQUIRED = ["footer", "eyebrow", "deck_title", "subtitle", "meta", "stats", "stats_note",
            "boundary",         "pages",
            "limits"]
# 🔴 內部流程／判定用語，不得印在投影片上。
# ⚠ 2026-08-19（§9.6b）新增後四項：使用者「成立／不成立／證據不足這種事系統內部
#   判定，不要讓這種措辭外洩到報告去」。與既有的「待驗證」「降級」同一類——
#   **流程狀態不是分析結論**。讀者要看的是結論，不是系統的推導狀態。
# ⚠ **有限清單，不做模式比對**（沿 deepen 4.2 的作法）：模式比對會擋掉正常句子，
#   逼 CLI 亂改到過為止。
BLOCKED_SLIDE_TERMS = ("本簡報怎麼讀", "圖表原則", "待驗證", "降級",
                       "證據不足", "判定為成立", "判定為不成立", "covered")
VAGUE_EVIDENCE_TERMS = (
    "整體統計",
    "資料分析",
    "報表結果",
    "趨勢觀察",
    "專利資料",
    "AI 判斷",
)
INTERNAL_EVIDENCE_KEYS = ("family_country_layout",)

# ── 裸數字掃描 ────────────────────────────────────────────────
# 簡報上的數量沒帶量詞，讀者會當成瑕疵指出來（2026-08-11 使用者回饋）。
# ⚠ 這是**警告不是閘門**：合法的例外真的存在（「2020、2022、2024 年」的「年」
#   涵蓋整串、封面數字磚的量詞在下方 label），硬擋會逼人寫出更差的句子。
# ⚠ 寫這個掃描器時踩過的坑：忘了吃掉數字與量詞之間的空白，「4 件」被判成裸數字，
#   一次噴出 110 處假警報。比對量詞前必須容許空白。
_NUM_SKIP = re.compile(r"(?:[A-Za-z]{1,4}[ -]?)\d[\d\-]*|\d{5,}|\{[a-z_]+\}")
_NUM = re.compile(r"(?<![\d\-–])(\d+(?:\.\d+)?)")
_NUM_OK = re.compile(r"[ 　]?(?:件|個|家|族|年|月|日|名|階|種|國|頁|次|條|項|倍|"
                     r"成|位|張|天|週|季|度|%|％|–|-)")


# ── 措辭紀律掃描 ──────────────────────────────────────────────
# 這幾條全部是**斷言強度超過證據**的同一種錯，2026-08-11 由使用者逐頁抓出來。
# 版面與數字都對，簡報照樣可能不專業——差別在「這句話有沒有超過你手上的證據」。
# ⚠ 一樣是**警告不是閘門**：有些情況真的成立（圖上同時證明了申請人數就能說「多家」），
#   但預設要被問一次。
_WORDING = [
    (r"多家獨立投入|多源投入|多家投入",
     "件數與家族數同步只能證明「多個獨立家族／發明」，證明不了「多家申請人」——"
     "除非同一張圖也給了申請人數。改說「由多個獨立家族形成，而非單一家族跨局延伸」"),
    (r"權利化.{0,4}落後|落後申請.{0,4}年|審查期.{0,3}約",
     "沒有逐案算申請日到授權日，就不能把時間相關性寫成審查期結論。"
     "改說「N 年授權公告高峰在時間上接續 M 年申請高峰」"),
    (r"自由度評估|自由度分析",
     "「自由度」會被讀成 FTO 結論。情報頁改說「競爭監測可優先聚焦…」，"
     "並補一句「實際 FTO 仍須依目標市場逐案檢索有效權利」"),
    (r"實為同體|實為一體|即為同一(?:家|公司)",
     "共同申請的兩個申請人在法律上仍是兩個主體。"
     "改說「布局高度重疊，分析上視為同一競爭陣營」"),
    (r"用語(?:彼此)?不重疊|用詞不重疊",
     "「用語不重疊」不等於「claim scope 不重疊」。"
     "改說「獨立項的核心構成要素存在明顯差異，可據此建立逐要素比對」"),
    (r"藍海|市場空白|沒有競爭",
     "低件數只代表「低密度、可探索」，不得寫成藍海（見 SKILL.md 優先順序第 2 條）"),
]


def scan_wording(c: dict) -> list[str]:
    """找出強度超過證據的措辭；回傳「位置 → 原因」提示。"""
    out = []
    for path, text in _walk_text(c, ""):
        for pat, why in _WORDING:
            m = re.search(pat, text)
            if m:
                out.append(f"{path}「{m.group(0)}」→ {why}")
    # 口徑一致性：同一份簡報不要同時出現「技術群」與「技術主題」
    joined = " ".join(t for _p, t in _walk_text(c, ""))
    if "技術群" in joined and "技術主題" in joined:
        out.append("全篇：同時出現「技術群」與「技術主題」——同一個東西只能有一個叫法")
    return out


def _walk_text(node, path):
    """走訪 content dict 裡所有字串，回傳 (路徑, 文字)。"""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_text(v, f"{path}.{k}")
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _walk_text(v, f"{path}[{i}]")


def scan_bare_numbers(c: dict) -> list[str]:
    """找出沒有接量詞的數量詞；回傳可讀的提示列表（不含 stats 數字磚）。"""
    out = []
    for path, text in _walk_text(c, ""):
        if path.startswith(".stats["):     # 數字磚的量詞在下方 label，設計如此
            continue
        masked = _NUM_SKIP.sub(lambda m: "#" * len(m.group(0)), text)
        for m in _NUM.finditer(masked):
            if _NUM_OK.match(masked, m.end()):
                continue
            out.append(f"{path}：…{text[max(0, m.start() - 12):m.end() + 12]}…")
    return out


def _check_caliber_verbatim(c: dict, facts_path: Path) -> list[str]:
    """口徑逐字閘門（design §7.5）：引用口徑的行，定義原文必須逐字出現。

    規則：任一頁的 `主體｜內容` 行，若主體與事實包某條 term 相同，
    內容必須**逐字包含**該條 text（可在後面加註解，不可改寫）。
    ⚠ 這是「CLI 不改圖」紀律的文字版——口徑是後端唯一來源，CLI 重述＝
    第二落點，後端改了簡報還印舊說法，而且不會有任何東西報錯。
    ⚠ 沒引用的口徑不報錯：挑哪幾條上頁是 CLI 的合法判斷（§7.0 判斷留活）。
    """
    if not facts_path.is_file():
        return []          # 舊素材（HTML fallback intake）沒有事實包：閘門不適用
    facts = {f["term"]: f["text"]
             for f in json.loads(facts_path.read_text(encoding="utf-8"))}
    bad: list[str] = []
    for page in c.get("pages") or []:
        for line in page.get("lines") or []:
            head, sep, rest = str(line).partition("｜")
            term = head.strip()
            if not sep or term not in facts:
                continue
            if facts[term] not in rest:
                bad.append(
                    f"口徑「{term}」未逐字使用引擎原文——CLI 只能挑選與加註，"
                    f"不能改寫。應含：「{facts[term]}」；實際：「{rest[:48]}…」")
    return bad


def _bad_actions(row: dict) -> list[str]:
    """該列宣告的行動裡，不在動詞表上的那幾個。

    ⚠ 多值最容易漏的是「只驗第一個」——`["追蹤", "立即提出申請"]` 若只看
    `[0]`，法律承諾就從第二個位置整條溜過去。解析走
    `deck_layout.row_actions`（唯一定義處），這裡只負責比對白名單。
    """
    from deck_layout import ACTION_VERBS, row_actions

    return [v for v in row_actions(row) if v not in ACTION_VERBS]


def _check_conclusions(c: dict, facts_path: Path) -> list[str]:
    """結論頁閘門（design §7.7）：動詞白名單＋發現欄逐字＋三欄齊備。

    - 專利行動 ∈ `deck_layout.ACTION_VERBS`（唯一定義處）——表外即紅，
      避免 AI 寫出法律／商業承諾。
    - 發現欄＝機械（intake topic_facts）——CLI 逐字引用，改寫即紅
      （與口徑閘門同一條紀律）。
    """
    from deck_layout import ACTION_VERBS
    from deck_layout import row_actions as _row_actions

    bad: list[str] = []
    facts = {}
    if facts_path.is_file():
        facts = {f["topic"]: f["finding"]
                 for f in json.loads(facts_path.read_text(encoding="utf-8"))}

    cc = c.get("conclusions")
    if not cc:
        # 🔴 破口①（2026-08-18 修）：原本 `if not cc: return []` 無條件放行。
        #    結論頁的畫法（`slide_conclusions`）與本閘門都在，只有範本沒宣告，
        #    於是那頁**根本不產出**而閘門一聲不吭。範本已補（§7a.4）。
        #
        #    ⚠ 判準是「**引擎有主題就必須有結論**」，不是無條件必要：
        #    沒有主題事實就沒有結論可寫，要求它只是武斷。
        #    `assemble_from_version` 一律寫出 topic_facts.json，所以正式路徑
        #    不會因為「檔案剛好不在」而讓這頁悄悄消失。
        if facts:
            return [f"缺結論頁（conclusions），但引擎有 {len(facts)} 個主題——"
                    "有主題就要有結論；範本有範例，宣告後會取代建議頁"]
        return []

    rows = cc.get("rows") or []
    if not rows:
        bad.append("結論頁沒有任何列（conclusions.rows 空）")
    for i, r in enumerate(rows, 1):
        for field in ("topic", "finding", "reading"):
            if not str(r.get(field) or "").strip():
                bad.append(f"結論頁第 {i} 列缺「{field}」——主題／發現／判讀／行動都要齊")
        # ⚠ action 不能用 str() 判空：list 的 str() 恆為非空，空 list 會被放行。
        actions = _row_actions(r)
        if not actions:
            bad.append("結論頁第 %d 列缺「action」——主題／發現／判讀／行動都要齊" % i)
        for verb in _bad_actions(r):
            bad.append(
                f"結論頁第 {i} 列行動「{verb}」不在動詞表 {list(ACTION_VERBS)}"
                "——CLI 只能選不能自創")
        topic = str(r.get("topic") or "").strip()
        if not topic:
            continue
        if not facts:
            # 🔴 2026-08-19 修：沒有 topic_facts 檔時**不做**主題名比對。
            #    原本無條件比對，於是每一列都被判「不在引擎的主題清單裡
            #    （可用：[]）」——清單根本不存在，卻說你不在裡面，
            #    訊息本身就自相矛盾，讀的人會去改主題名而不是去補檔。
            #    ⚠ 這不是把破口③放回去：破口③是「主題名不在 facts 就跳過
            #      逐字比對」，前提是 facts 存在。facts 不存在時無從比對，
            #      與同函式的 `_check_conclusion_coverage`（`if not facts: return []`）
            #      同一條規則——沒有對帳基準就不對帳。
            #    ⚠ 正式路徑不會走到這裡：`assemble_from_version` 一律寫出
            #      topic_facts.json，且 `require_topic_facts` 會先擋。
            continue
        if topic not in facts:
            # 🔴 破口③：原本 `if topic in facts:` ——主題名不在 facts 就整個跳過
            #    逐字比對。CLI 只要換個主題名，這道比對就完全失效。
            bad.append(
                f"結論頁「{topic}」不在引擎的主題清單裡——"
                f"主題名必須與 topic_facts 一致（可用：{sorted(facts)[:4]}…）")
        elif str(r.get("finding") or "") != facts[topic]:
            bad.append(
                f"結論頁「{topic}」的發現欄未逐字使用引擎字串——"
                f"應為「{facts[topic]}」；實際「{str(r.get('finding'))[:40]}」")

    bad += _check_conclusion_coverage(cc, rows, facts)
    return bad


def _check_conclusion_coverage(cc: dict, rows: list, facts: dict) -> list[str]:
    """🔴 破口②：涵蓋率對帳（2026-08-18，§7b.3）。

    原本只驗 `rows` 非空——10 個主題只寫 1 列照樣全綠，讀者會以為
    「只有這一個主題值得結論」。

    ⚠ **不用最小列數**。規定「至少 N 列」是形式鎖，v5／v7／v9 三次同型失敗都是
    這樣來的：CLI 為了過鎖而硬湊，或乾脆刪掉整段（缺席，目視兜不住）。
    而且 design §2.3 已明訂「接不上依據的建議句直接擋下」——那等於規格授權了丟棄。

    改為要求**沒寫的要現形**：宣告 `covered N/M` 與 `uncovered` 逐條原因。
    它不規定要寫幾列，只把缺席型偏差轉成一份看得見的清單，讀者自己判斷是
    資料不夠還是 CLI 偷懶。
    """
    if not facts:
        return []          # 沒有引擎主題清單就無從對帳（例如未跑分群的版本）
    total = len(facts)
    written = {str(r.get("topic") or "").strip() for r in rows}
    written = {t for t in written if t in facts}
    if len(written) == total:
        return []          # 全涵蓋：不需要對帳，也不得誤擋

    bad: list[str] = []
    covered = str(cc.get("covered") or "").strip()
    uncovered = cc.get("uncovered") or []
    if not covered or not uncovered:
        return [f"結論頁只涵蓋 {len(written)}／{total} 個主題，"
                "但沒有對帳——請宣告 `covered: \"N/M\"` 與 `uncovered`"
                "（每筆含 topic 與 reason）。"
                "⚠ 不是要你寫滿，是沒寫的要講出來"]

    if covered != f"{len(written)}/{total}":
        bad.append(f"結論頁對帳「{covered}」與實際不符，應為 "
                   f"「{len(written)}/{total}」——對帳寫錯就只是裝飾")
    listed = {str(u.get("topic") or "").strip() for u in uncovered}
    missing = set(facts) - written - listed
    if missing:
        bad.append(f"這些主題既沒結論也沒列進 uncovered：{sorted(missing)}"
                   "——它們就這樣消失了，沒有人會發現")
    for u in uncovered:
        if not str(u.get("reason") or "").strip():
            bad.append(f"uncovered「{u.get('topic')}」沒寫原因——"
                       "「資料不夠」與「沒寫」在畫面上分不出來")
    return bad


def _check_figures(c: dict) -> list[str]:
    """圖形文法閘門（design §7.4）：type 白名單、節點數容量、文字長度。

    容量常數取自 `deck_layout`（唯一定義處）；撞版由渲染端裕度表把關，
    這裡只擋「進不了版型」的宣告，秒級回饋不用等組版。
    """
    from deck_layout import FIGURE_MAX_NODES, FIGURE_NODE_UNITS

    bad: list[str] = []
    for page in c.get("pages") or []:
        fig = page.get("figure")
        if not fig:
            continue
        title = str(page.get("title") or "")[:12]
        ftype = str(fig.get("type") or "")
        nodes = [str(n) for n in fig.get("nodes") or []]
        if ftype not in FIGURE_MAX_NODES:
            bad.append(f"頁「{title}」figure type {ftype!r} 不在文法內"
                       f"（可用：{sorted(FIGURE_MAX_NODES)}）——不得自由畫圖")
            continue
        cap = FIGURE_MAX_NODES[ftype]
        if not nodes:
            bad.append(f"頁「{title}」figure 沒有節點")
        elif len(nodes) > cap:
            bad.append(f"頁「{title}」figure 節點 {len(nodes)} 個，"
                       f"超出 {ftype} 容量 {cap}——拆頁或收斂節點")
        for n in nodes:
            if units(n) > FIGURE_NODE_UNITS:
                bad.append(f"頁「{title}」figure 節點文字過長"
                           f"（{units(n):.0f} 單位 > {FIGURE_NODE_UNITS:.0f}）→ {n[:20]}…")
    return bad


def _check_p2_evidence_rules(c: dict) -> list[str]:
    """P2 evidence gate：建議句要帶「依據：」，流程狀態不得印出。

    閘門只驗標記存在、有限空泛例句與內部欄位外洩，不判斷依據內容是否充分；
    充分性留給逐頁目視與真 CLI 驗收，避免把語意判斷寫死成形式鎖。
    """
    bad: list[str] = []
    for term in BLOCKED_SLIDE_TERMS:
        for path, text in _walk_text(c, ""):
            if term in text:
                bad.append(f"{path} 含流程/規則字串「{term}」——不得印在投影片上")

    # 🔴 §9.3：CLI 挑色的入口收斂到 `pages[].tag`（rec 頁退場後）。
    # ⚠ 挑庫外的標籤要在這裡擋——原本完全不驗，寫錯會在 `TAG_COLOR[tag]`
    #   KeyError，錯誤訊息與內容無關、修稿輪也修不掉。
    # ⚠ 不掛標籤是合法的：強制每頁都要標籤是形式鎖，會逼 CLI 硬掛。
    from deck_layout import TAG_COLOR

    for page_index, page in enumerate(c.get("pages") or [], 1):
        tag = page.get("tag")
        if tag and tag not in TAG_COLOR:
            bad.append(
                f"第 {page_index} 頁的標籤「{tag}」不在標籤庫 {sorted(TAG_COLOR)}"
                "——CLI 只能從庫裡挑，不得自創")

    # 🔴 §9.3：`recommendations` 已退場，`依據：` 紀律移到結論頁的 `evidence`
    # （§9.5 證據鏈）。⚠ 這條紀律**不能跟著 rec 一起消失**——它擋的是
    #   「接不上依據的建議句」，而結論頁的行動同樣是建議。
    for index, row in enumerate((c.get("conclusions") or {}).get("rows") or [], 1):
        joined = "\n".join([str(row.get("evidence") or ""),
                            str(row.get("reading") or "")])
        if "依據：" not in joined:
            bad.append(f"結論第 {index} 列缺「依據：」——接不上依據的行動不得放行")
        for term in VAGUE_EVIDENCE_TERMS:
            if f"依據：{term}" in joined:
                bad.append(f"結論第 {index} 列含空泛依據「依據：{term}」——請改用可追錨點")
        for key in INTERNAL_EVIDENCE_KEYS:
            if key in joined:
                bad.append(f"結論第 {index} 列含內部欄位「{key}」——投影片請改用中文顯示名稱")
    return bad


def _check_cover_stats(c: dict, stats_path: Path) -> list[str]:
    """封面四格數字必須逐字取自引擎（§2，一方產生、一方消費）。

    ⚠ 這四個數字原本由 CLI 自己填。CLI 手上沒有權威來源，只能從別的頁面反推——
    封面顯示 281 件而母體實際 55 件就是這樣來的。引擎已在 `report_data.cover_stats`
    供給，本閘門確保它真的被用上（與 `topic_facts` 的逐字比對同一條紀律）。

    ⚠ 只比對**數字**不比對標籤：標籤是排版用語（「件專利」vs「件」），
    鎖它會變成形式鎖；數字才是事實。
    """
    if not stats_path.is_file():
        return []
    engine = json.loads(stats_path.read_text(encoding="utf-8"))
    if not engine:
        return []
    stats = c.get("stats") or []
    if len(stats) != 4:
        return [f"封面統計不是四格（實際 {len(stats)}）——"
                "2026-08-18 定案為件／族／受理局／專利類型"]
    written = [str(n).strip() for n, _label in stats]
    tally = engine.get("kind_tally") or {}
    expected = [
        str(engine.get("patent_count", "")),
        str(engine.get("family_count", "")),
        str(engine.get("jurisdiction_count", "")),
        "·".join(str(tally.get(k, 0)) for k in ("發明", "新型", "設計")),
    ]
    bad: list[str] = []
    for i, (got, want) in enumerate(zip(written, expected), 1):
        if got != want:
            bad.append(
                f"封面第 {i} 格數字「{got}」與引擎不符，應為「{want}」"
                "——封面數字一律逐字取自 cover_stats.json，不得自行推算")
    return bad


def main() -> int:
    c = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    png_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    work_dir = Path(sys.argv[1]).resolve().parent
    caliber_bad = _check_caliber_verbatim(c, work_dir / "caliber_facts.json")
    caliber_bad += _check_conclusions(c, work_dir / "topic_facts.json")
    caliber_bad += _check_cover_stats(c, work_dir / "cover_stats.json")
    caliber_bad += _check_figures(c)
    caliber_bad += _check_p2_evidence_rules(c)
    fc = png_dir / "font_choice.json" if png_dir else None
    fonts = json.loads(fc.read_text(encoding="utf-8")) if fc and fc.is_file() else None
    B = budget()
    bad: list[str] = list(caliber_bad)

    def chk(label: str, text: str, cap: float):
        u = units(text)
        if u > cap:
            bad.append(f"{label}：{u:.0f} 單位，超出上限 {cap:.0f}（多 {u - cap:.0f}）→ {text[:28]}…")

    for k in REQUIRED:
        if k not in c or c[k] in ("", [], None):
            bad.append(f"缺欄位：{k}")
    # plan_deck.py 產出的骨架頁沒填完就組版，會交出一頁「<待撰稿填寫>」給使用者
    for path, text in _walk_text(c, ""):
        if "<待撰稿填寫>" in text or "_todo" in path:
            bad.append(f"骨架未填寫：{path} → {text[:36]}")
    if bad:
        print("\n".join("✗ " + b for b in bad))
        return 1

    chk("封面標題", c["deck_title"], B["deck_title"])
    for t in [c["subtitle"], *c["meta"], c["stats_note"]]:
        chk("封面文字", t, B["cover_line"])
    chk("封面邊界句", c["boundary"], B["boundary"])
    for k in ("read_me", "chart_rule"):
        if k not in c:
            continue
        body = c[k][1]
        chk(f"封面 {k}", body, B["cover_panel"])
        # 頁碼手寫必錯：頁數一改就忘了同步。一律用 {chart_first}/{chart_last}/{last}
        if re.search(r"第\s*\d+", body) and "{" not in body:
            bad.append(f"封面 {k} 直接寫死頁碼 → 改用 "
                       f"{{chart_first}}／{{chart_last}}／{{last}} 佔位符")
    for _n, label in c["stats"]:
        chk("封面統計標籤", label, B["stat_label"])

    # 🔴 §9.3：`recommendations` 的 3–5 數量鎖已移除，rec 頁本身也退場。
    # ⚠ 數量鎖與 v5／v7／v9 形式鎖同族——它不說「該寫什麼」，只說「不准超過 N」，
    #   於是 CLI 為了過鎖而硬湊或乾脆刪掉整段。使用者 2026-08-19：
    #   「不一定只能提四項，需要的是完整由資料驅動所得出的結論或建議。」
    # ⚠ 而且原本的鎖是**假的**：閘門允許 5 張，版面高度卻寫死兩排
    #   （`need_total = 2 * gh`），5 張要 3 排，裕度檢查少算一整排。
    # 完整性改由「行動空間完整掃描」保證（§9.6），不用數量下限。
    cc = c.get("conclusions") or {}
    if cc:
        chk("結論頁標題", cc.get("title", ""), B["page_title"])
        chk("結論頁結論句", cc.get("takeaway", ""), B["takeaway"])

    used = []
    for i, p in enumerate(c["pages"], start=3):
        chk(f"P{i} 標題", p["title"], B["page_title"])
        chk(f"P{i} 結論句", p["takeaway"], B["takeaway"])
        # 版型必須是清單裡的（2026-08-18，§7a）。唯一定義處＝deck_layout.LAYOUTS；
        # ⚠ 「chart」是隱式的（有 charts 就走圖表頁），仍要能被明寫，
        #   否則 CLI 無從表達意圖，而閘門也擋不掉打錯的版型名。
        declared = str(p.get("layout") or ("chart" if p.get("charts") else "text"))
        if declared not in LAYOUTS:
            bad.append(f"P{i} 版型「{declared}」不在版型庫 {sorted(LAYOUTS)}"
                       "——CLI 只能用清單裡的版型")
        if declared == "table":
            # 表格頁（2026-08-18，§7c）：欄數／欄寬由版型算，這裡只擋結構錯。
            # ⚠ 每列長度必須等於欄頭數——不等長會讓儲存格錯位，而轉圖後才看得出來。
            tbl = p.get("table") or {}
            heads = tbl.get("headers") or []
            if not heads:
                bad.append(f"P{i} 表格頁缺 table.headers")
            for j, row in enumerate(tbl.get("rows") or [], 1):
                if len(row) != len(heads):
                    bad.append(f"P{i} 表格第 {j} 列有 {len(row)} 欄，"
                               f"欄頭有 {len(heads)} 欄——欄數不符會讓儲存格錯位")
            if heads:
                try:
                    from deck_layout import table_col_widths

                    weights = tbl.get("weights")
                    table_col_widths(len(heads), tuple(weights) if weights else None)
                except ValueError as exc:
                    bad.append(f"P{i} 表格欄寬算不出來：{exc}")
            continue
        charts = p.get("charts") or []
        total = units(((p.get("tag") or "") and p["tag"] + "｜") + " ".join(p["lines"]))
        if not charts:
            if p.get("layout") == "label":
                # 標籤欄頁走逐列量測（與組版同一套幾何），不是扁平字數上限
                fit = label_page_fit(p["lines"])
                if fit["need"] > fit["avail"]:
                    bad.append(f"P{i} 標籤欄頁：需求 {fit['need']:.2f}in > "
                               f"可用 {fit['avail']:.2f}in → 縮短內文或拆頁")
                for head, u in fit["over_labels"]:
                    bad.append(f"P{i} 左欄標籤「{head}」{u:.1f} 單位 > "
                               f"{fit['label_cap']:.1f}，會換行擠掉右欄對齊 → 縮短標籤")
                if fit["n_labelled"] == 0:
                    bad.append(f"P{i} 標記了 layout=label 但沒有任何「主體｜內容」列")
            # 純文字頁：頁數不受圖表數限制，需要更多頁就用這種
            elif total > B["text_page"]:
                bad.append(f"P{i} 文字頁：{total:.0f} 單位，超出上限 {B['text_page']:.0f}")
        else:
            if total > B["band_max"]:
                bad.append(f"P{i} 判讀帶：{total:.0f} 單位，超出硬上限 {B['band_max']:.0f}"
                           f"（圖表會被壓到看不清楚）")
            elif total > B["band_total"]:
                print(f"⚠ P{i} 判讀帶 {total:.0f} 單位（建議 ≤{B['band_total']:.0f}）"
                      f"；圖表會相應變小，組版後確認該頁圖內字級仍 ≥9pt")
            if len(charts) > 2:
                bad.append(f"P{i} 一頁最多 2 張圖，目前 {len(charts)} 張")
            elif len(charts) == 2:
                # 雙圖頁預設不用；真要用，兩張圖都必須達 12pt，否則直接擋掉要求拆頁
                if not (png_dir and fonts):
                    bad.append(f"P{i} 雙圖頁需要 png_dir 與 font_choice.json 才能驗字級")
                else:
                    sizes = [Image.open(png_dir / f"{n}.png").size for n in charts]
                    pts = predict_chart_pt(sizes, [fonts[n] for n in charts],
                                           p["lines"], p.get("tag"))
                    if min(pts) < MIN_CHART_PT_MULTI:
                        bad.append(
                            f"P{i} 雙圖頁預估字級 {min(pts):.1f}pt < "
                            f"{MIN_CHART_PT_MULTI:.0f}pt → 拆成兩頁"
                            f"（{'、'.join(f'{n} {v:.1f}pt' for n, v in zip(charts, pts))}）")
                    else:
                        print(f"⚠ P{i} 雙圖頁預估 {min(pts):.1f}pt，達標但仍建議拆兩頁")
        used += charts

    for n in used:
        if png_dir and not (png_dir / f"{n}.png").is_file():
            bad.append(f"找不到圖檔：{n}.png")
    dup = {n for n in used if used.count(n) > 1}
    if dup:
        bad.append(f"圖表重複使用：{', '.join(sorted(dup))}")
    if png_dir:
        have = {p.stem for p in png_dir.glob("*.png")}
        unused = have - set(used)
        if unused:
            print(f"⚠ 有圖未使用：{', '.join(sorted(unused))}（若為刻意略過，回報時要說明）")

    # ⚠ 2026-08-19（§5.2）：路線圖頁的字數與溢出驗證整段移除。
    # §7d 只拿掉 `_compose` 的呼叫，這裡卻還在讀 `c["roadmap"]`——
    # 範本已無該鍵，真的走到就是 KeyError；而它從來走不到，所以沒人發現。

    # ⚠ 「單項合格 ≠ 整頁合格」這條紀律**沒有消失**——它移到結論頁：
    #   `slide_conclusions` 的 `note("結論頁列高總和", …)` 管整頁總高，
    #   而 §9.4 的自動分頁讓「裝不下」變成「換頁」而不是「溢出」。
    for t in c["limits"]:
        chk("限制", t, B["limit_line"])

    words = scan_wording(c)
    if words:
        print(f"⚠ 措辭超過證據 {len(words)} 處，逐條看過再決定改不改：")
        for w in words:
            print(f"    {w}")

    bare = scan_bare_numbers(c)
    if bare:
        print(f"⚠ 疑似裸數字（數量沒帶量詞）{len(bare)} 處，逐條看過再決定改不改：")
        for b in bare:
            print(f"    {b}")
        print("    合法例外：量詞涵蓋整串（「2020、2022、2024 年」）、年份、報表編號。")

    if bad:
        print("\n".join("✗ " + b for b in bad))
        print(f"\n共 {len(bad)} 項不合格，請修正後再組版。")
        return 1
    print(f"✓ 欄位齊全、字數皆在上限內、{len(used)} 張圖各用一次 → 可以組版")
    return 0


if __name__ == "__main__":
    _force_utf8_console()
    raise SystemExit(main())
