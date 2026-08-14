"""在組版前先驗 content.json：欄位齊不齊、字數超不超、圖表對不對得上。

為什麼要有這一步：make_deck.py 要等 PowerPoint 排完才知道溢出，回饋慢且看不出是哪一句太長。
本腳本純算字寬單位，秒級回饋，直接指出「哪個欄位、超出幾個字」。

用法：python check_content.py <content.json> [png_dir]
回傳碼：0 = 可以組版；1 = 有問題（缺欄位、字數超標、圖表對不上）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deck_layout import (MIN_CHART_PT_MULTI, budget,   # noqa: E402
                         label_page_fit, predict_chart_pt,
                         roadmap_page_overflow, units)

REQUIRED = ["footer", "eyebrow", "deck_title", "subtitle", "meta", "stats", "stats_note",
            "boundary", "read_me", "chart_rule", "rec_title", "rec_takeaway",
            "recommendations", "pages", "roadmap_title", "roadmap_takeaway", "roadmap",
            "limits"]

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


def main() -> int:
    c = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    png_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    caliber_bad = _check_caliber_verbatim(
        c, Path(sys.argv[1]).resolve().parent / "caliber_facts.json")
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
        body = c[k][1]
        chk(f"封面 {k}", body, B["cover_panel"])
        # 頁碼手寫必錯：頁數一改就忘了同步。一律用 {chart_first}/{chart_last}/{last}
        if re.search(r"第\s*\d+", body) and "{" not in body:
            bad.append(f"封面 {k} 直接寫死頁碼 → 改用 "
                       f"{{chart_first}}／{{chart_last}}／{{last}} 佔位符")
    for _n, label in c["stats"]:
        chk("封面統計標籤", label, B["stat_label"])

    chk("結論頁標題", c["rec_title"], B["page_title"])
    chk("結論頁結論句", c["rec_takeaway"], B["takeaway"])
    if len(c["recommendations"]) not in (3, 4, 5):
        bad.append(f"研發建議應為 3–5 個方向，目前 {len(c['recommendations'])} 個")
    for r in c["recommendations"]:
        chk(f"建議卡標題 {r['title'][:4]}", r["title"] + r["tag"], B["rec_title"])
        for ln in r["lines"]:
            chk(f"建議卡內文 {r['title'][:4]}", ln, B["rec_line"])

    used = []
    for i, p in enumerate(c["pages"], start=3):
        chk(f"P{i} 標題", p["title"], B["page_title"])
        chk(f"P{i} 結論句", p["takeaway"], B["takeaway"])
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

    chk("路線圖結論句", c["roadmap_takeaway"], B["takeaway"])
    for r in c["roadmap"]:
        for it in r["items"]:
            chk(f"路線圖 {r['label'][:2]}", it, B["roadmap_item"])
    # ⚠ 單項合格 ≠ 整頁合格：`roadmap_item` 只管「每則 ≤3 行」，但三張卡各三則
    #   再加限制框就可能撐爆整頁。以前這個檢查只存在於 make_deck，秒級閘門放行、
    #   組版才擋，等於白等一輪（2026-08-11 sub agent 實測踩到）。幾何直接 import，
    #   不在這裡另寫一份。
    over = roadmap_page_overflow(c)
    if over:
        bad.append(f"路線圖頁總高：需求 {over[1]:.2f}in > 可用 {over[0]:.2f}in"
                   f" → 把最長的幾則壓短（每張卡每則約 {over[2]:.1f} 單位為一行）")
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
    raise SystemExit(main())
