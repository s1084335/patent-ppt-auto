"""版面引擎的回歸測試：合成素材跑完整組版鏈，逐像素比對基準圖。

⚠ **為什麼需要它**：改 `deck_layout.py` 時，裕度表與 audit 都可能全綠而實物是壞的
   ——2026-08-11 的分隔線壓字就是這樣過關的（估算器與繪製用了不同參數，
   而裕度表用的正是同一個估算器，當然抓不到）。**只有像素比得出來。**

⚠ 素材是**合成**的，不放任何真實報表：一來不把某批專利釘進 skill，
   二來基準圖才不會因為換批而失效。它測的是版面引擎，不是解析器。

用法：
    python regression.py            # 比對基準，有差異回傳 1
    python regression.py --update   # 重新產生基準（改版面**故意**變動後才用）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = HERE.parent / "regression_baseline"

# PPTX→PNG 轉圖器（走 PowerPoint COM）。⚠ 2026-08-13 由寫死開發機路徑改為可覆寫：
# 目視轉圖已定案進產線（openspec add-deck-delivery-line design 4-0），
# 產線機器不會有 `D:\vscode\`，寫死等於一到伺服器就斷。
# 沿 `fit_render_charts.PLAYWRIGHT_HOME` 同一套慣例：環境變數優先、預設回開發機。
# ⚠ 2026-08-14 起基準比 SVG 截圖（Chromium），PPTX_TO_PNG（PowerPoint COM）
# 已不在回歸路徑；COM 只用於映射校驗（tasks 2.3①，改轉換器時重跑）。

# ── 合成圖表：涵蓋會影響版面的幾種長寬比 ───────────────────────
# 高瘦（會把圖內字壓小）、扁平（雙圖頁唯一成立的形狀）、近方形、含 chip
# ⚠ square 2026-08-14 由 900×700 改 900×560：字級門檻真的會擋之後
#   （make_deck 把 weak 計入回傳值），700 高的方圖 7.2pt < 9pt 直接紅——
#   回歸素材必須是**合法可交付**的 deck，不能拿門檻擋下的頁當基準。
SHAPES = {"tall": (1180, 560), "flat_a": (1180, 150),
          "flat_b": (1180, 160), "square": (900, 560)}


def _svg(w: int, h: int, *, chip: bool) -> str:
    """產生固定內容的 SVG（不可有隨機成分，否則基準圖每次都不同）。"""
    rows = []
    for i in range(5):
        y = 40 + i * (h - 60) // 5
        rows.append(f'<text x="12" y="{y}" font-size="15.1" fill="#1A1A1A">'
                    f'類別 {i + 1} 標籤</text>')
        rows.append(f'<rect x="150" y="{y - 14}" width="{(i + 1) * (w - 200) // 6}" '
                    f'height="18" fill="#2563EB"/>')
        if chip:
            rows.append(f'<rect class="chip" x="{w - 220}" y="{y - 14}" width="200" '
                        f'height="18" fill="#FCA5A5"/>'
                        f'<text class="chip" x="{w - 214}" y="{y}" font-size="15.1" '
                        f'fill="#1A1A1A">項目 {i + 1} 12件/5家</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}"><rect width="{w}" height="{h}" fill="#FFFFFF"/>'
            f'<text x="12" y="22" font-size="15.1" fill="#0B2545">合成圖表標題</text>'
            + "".join(rows) + "</svg>")


def _content() -> dict:
    """涵蓋全部頁型的 content：封面／建議卡／圖表頁／純文字頁／標籤欄頁／雙圖頁／路線圖。"""
    return {
        "footer": "回歸測試｜非 FTO／非侵權判斷",
        "eyebrow": "REGRESSION　版面回歸測試",
        "deck_title": "版面引擎回歸測試",
        "subtitle": "封面・建議卡・圖表頁・文字頁・標籤欄頁・雙圖頁・路線圖",
        "meta": ["資料期間 2011–2026 申請年　｜　來源：合成素材",
                 "排名一律取前 10 名　·　階層 4–5 階　·　REG-001"],
        "stats": [["<N>", "件專利"], ["<N>", "個家族"], ["<N>", "個受理局"], ["<N>", "個主題"]],
        "stats_note": "存活家族 46 個　·　受理局 CN·TW·US·EP　·　技術 5 個、功效 8 個主題",
        "boundary": "本檔為版面回歸測試，內容無情報意義。",
        "rec_title": "四類方向：2 個機會區、1 個差異化區、1 個迴避區",
        "rec_takeaway": "本頁測試建議卡的兩行文字與標籤是否溢出。",
        "recommendations": [
            {"title": f"{c}　合成主題", "tag": t, "color": col,
             "lines": ["依據：11 件 5 家、近期集中度 88.9%，是最擠的一區。",
                       "風險與下一步：先讀完那 4 件請求項再落筆，避開既有構型。"]}
            for c, t, col in (("A", "可探索", "amber"), ("B", "需驗證", "blue"),
                              ("C", "走差異化", "rose"), ("D", "先繞開", "rose"))],
        "pages": [
            {"title": "圖表頁：高瘦圖會把圖內字壓小", "takeaway": "測單圖頁的判讀帶撐高與圖表縮放。",
             "charts": ["tall"],
             "lines": ["結論句：高瘦圖的可用高度決定圖內字級，與圖寬無關。",
                       "支撐句：判讀帶每多一行約吃 0.27in，圖內字級掉約 0.6pt。"],
             "tag": None},
            {"title": "純文字頁：舊式，無 layout 鍵", "takeaway": "測向後相容——沒有 layout 的頁必須照舊排。",
             "charts": [],
             "lines": ["第一段。純文字頁的每一則是一段，整組垂直置中。",
                       "第二段。滿版行長約 50 個全形字，這正是標籤欄頁要解決的問題。",
                       "第三段。這頁沒有 layout 鍵，必須走原本的路徑。"],
             "tag": None},
            {"title": "標籤欄頁：左欄索引＋右欄內文", "takeaway": "測 layout=label 的列高、分隔線與全寬列。",
             "charts": [], "layout": "label",
             "lines": ["甲｜左欄標籤加粗上色，右欄行長約 40 個全形字；這一列刻意寫長，"
                       "讓它換到第三行，用來驗證列高有沒有把分隔線推開（CN 111167066、"
                       "CN 211798524、CN 223248694、CN 223248696）。",
                       "乙｜較短的一列，只有一行。",
                       "丙｜中等長度的一列，大約會換到第二行，用來看列距是否一致。",
                       "⚠ 沒有分隔號的列橫跨全寬，這一列同時驗證全寬列的排版。"],
             "tag": None},
            {"title": "雙圖頁：兩張扁平圖才成立", "takeaway": "測雙圖頁的 12pt 門檻與堆疊幾何。",
             "charts": ["flat_a", "flat_b"],
             "lines": ["結論句：兩張扁圖併頁時仍須各自達 12pt。",
                       "支撐句：達不到就會被 check_content.py 擋下要求拆頁。"],
             "tag": "風險"},
            {"title": "圖表頁：方形圖與標籤帶", "takeaway": "測 tag 標籤帶與方形圖的置中。",
             "charts": ["square"],
             "lines": ["結論句：方形圖的縮放以可用高度為準。",
                       "支撐句：圖表永遠等比縮放置中，絕不裁切。"],
             "tag": "機會"},
        ],
        "roadmap_title": "研發路線圖與專利行動",
        "roadmap_takeaway": "測路線圖三欄卡片的高度由內容決定、整組垂直置中。",
        "roadmap": [
            {"label": "短期　0–3 個月", "color": "cyan",
             "items": ["對主要持有者做 claim chart，量出邊界。", "追蹤審查中案件的結果。",
                       "把失效案整理成前案素材庫。"]},
            {"label": "中期　3–12 個月", "color": "amber",
             "items": ["以低密度區為差異化切入點。", "樣機驗證並補做跨域檢索。"]},
            {"label": "長期　1–3 年", "color": "green",
             "items": ["建立平台型組合並以組合請求項拉開距離。"]},
        ],
        "limits_title": "分析限制與非 FTO 聲明",
        "limits": ["本檔為版面回歸測試，內容為合成，不具情報意義。",
                   "母體為合成素材 <N> 件；不對應任何真實批次。",
                   "低件數主題僅代表「低密度、可探索」，須經逐案比對後才可視為機會。"],
    }


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="重新產生基準圖")
    ap.add_argument("--keep", action="store_true", help="保留工作目錄以便查看")
    a = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="deck_reg_"))
    charts, png = work / "charts", work / "png"
    charts.mkdir()
    for name, (w, h) in SHAPES.items():
        (charts / f"{name}.svg").write_text(_svg(w, h, chip=(name == "square")),
                                            encoding="utf-8")
    (work / "content.json").write_text(
        json.dumps(_content(), ensure_ascii=False, indent=1), encoding="utf-8")

    # 🔴 2026-08-14（tasks 2.3②）：基準改比 **SVG 截圖**（Chromium，倍率由
    # deck_layout.VISUAL_SCALE 導出），不再走 PowerPoint COM 轉圖。
    # 依據＝B 案「截 SVG＝截成品」：斷行寫死、絕對定位、關 wrap 後 PPTX 已無
    # 重排自由；SVG 截圖與實機開檔的對應由映射校驗（tasks 2.3①，
    # output/_verify/mapping）背書——那一次校驗成立後固定，日常回歸不再依賴
    # Office。⚠ COM 沒有從驗證體系消失：改動窄轉換器時要重跑映射校驗。
    # 組版仍產 pptx 並過 audit——回歸同時守「pptx 產得出來」與「SVG 長對」。
    py = [sys.executable]
    steps = [
        ("字級擬合與轉圖", py + [str(HERE / "fit_render_charts.py"), str(charts),
                                str(png), "20,19,18,17,16"]),
        ("內容檢查", py + [str(HERE / "check_content.py"),
                          str(work / "content.json"), str(png)]),
        ("組版", py + [str(HERE / "make_deck.py"), str(work / "content.json"),
                      str(png), str(work / "reg.pptx"), str(work / "svg")]),
        ("閘門", py + [str(HERE / "audit_deck.py"), str(work / "reg.pptx")]),
        ("SVG 截圖", py + [str(HERE / "shoot_pages.py"), str(work / "svg"),
                           str(work / "shots")]),
    ]
    for label, cmd in steps:
        r = _run(cmd)
        if r.returncode != 0:
            print(f"✗ {label} 失敗（exit {r.returncode}）\n{r.stdout}\n{r.stderr}")
            return 1
        if label == "組版" and "溢出區域：0 個" not in r.stdout:
            print(f"✗ 組版有溢出\n{r.stdout}")
            return 1
        print(f"✓ {label}")

    shots = sorted((work / "shots").glob("*.png"))
    if a.update:
        if BASELINE.exists():
            shutil.rmtree(BASELINE)
        BASELINE.mkdir(parents=True)
        for i, s in enumerate(shots, 1):
            shutil.copy(s, BASELINE / f"slide{i:02d}.png")
        print(f"\n已更新基準：{len(shots)} 張 → {BASELINE}")
        return 0

    if not BASELINE.is_dir():
        print(f"✗ 找不到基準目錄 {BASELINE}；先跑一次 --update")
        return 1

    from PIL import Image, ImageChops
    base = sorted(BASELINE.glob("*.png"))
    if len(base) != len(shots):
        print(f"✗ 頁數不符：基準 {len(base)} 頁、本次 {len(shots)} 頁")
        return 1
    diff = []
    for i, (b, s) in enumerate(zip(base, shots), 1):
        ba, sa = Image.open(b).convert("RGB"), Image.open(s).convert("RGB")
        if ba.size != sa.size or ImageChops.difference(ba, sa).getbbox() is not None:
            diff.append(i)
            shutil.copy(s, BASELINE.parent / f"regression_FAIL_slide{i:02d}.png")
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    if diff:
        print(f"\n✗ {len(diff)} 頁與基準不同：{diff}")
        print(f"  實際結果已存到 {BASELINE.parent}\\regression_FAIL_slide*.png")
        print("  版面是**故意**改的就跑 --update 重建基準；否則就是回歸。")
        return 1
    print(f"\n✓ {len(shots)} 頁全部與基準逐像素相同")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
