"""從 HTML 專利情報報表抽出簡報所需的全部素材。

輸出：
  <out>/charts/*.svg   每張圖表的原始 SVG（以內容雜湊去重）
  <out>/report.json    章節、表格、判讀文字（explanation）、圖表清單

⚠ 圖表不是外部檔案：報表把 SVG 以 URL-encoded data URI 內嵌在
  `<img src="data:image/svg+xml,...">`。用長度上限的 regex 去撈 src 會完全撈不到，
  必須解 data URI。同一張圖可能同時有 `.web.svg` 與非 web 版本，用內容雜湊去重。

用法：python extract_report.py <html_path> <out_dir>
"""
from __future__ import annotations

import hashlib
import html as htmllib
import json
import os
import re
import sys
from html.parser import HTMLParser
from urllib.parse import unquote


class ReportParser(HTMLParser):
    """逐節解析報表：section → h2 標題 / img(data URI) / table / 判讀文字。"""

    def __init__(self):
        super().__init__()
        self.sections: list[dict] = []
        self.cur: dict | None = None
        self.buf: list[str] = []
        self.capture: str | None = None
        self.table: dict | None = None
        self.row: list[str] | None = None
        self.cell: str | None = None
        self._p_cls = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "section" and "report-section" in cls:
            self.cur = {"title": None, "charts": [], "tables": [], "texts": [], "notes": []}
            self.sections.append(self.cur)
        elif tag == "h2":
            self.capture, self.buf = "h2", []
        elif tag == "img" and self.cur is not None:
            src = a.get("src", "")
            if src.startswith("data:image/svg+xml"):
                self.cur["charts"].append(
                    {"name": a.get("alt", "chart"), "svg": unquote(src.split(",", 1)[1])})
        elif tag == "table" and self.cur is not None:
            self.table = {"head": [], "rows": []}
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell, self.buf, self.capture = tag, [], "cell"
        elif tag == "p" and self.cur is not None and self.table is None:
            self.capture, self.buf, self._p_cls = "text", [], cls

    def handle_endtag(self, tag):
        txt = re.sub(r"\s+", " ", htmllib.unescape("".join(self.buf)).strip())
        if tag == "h2" and self.capture == "h2":
            if self.cur is not None:
                self.cur["title"] = txt
            self.capture, self.buf = None, []
        elif tag in ("td", "th") and self.cell:
            self.row.append(txt)
            self.cell, self.capture, self.buf = None, None, []
        elif tag == "tr" and self.row is not None:
            if self.table is not None:
                if not self.table["head"]:
                    self.table["head"] = self.row
                else:
                    self.table["rows"].append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            if self.cur is not None:
                self.cur["tables"].append(self.table)
            self.table = None
        elif tag == "p" and self.capture == "text":
            if txt and self.cur is not None:
                self.cur["notes" if "section-note" in self._p_cls else "texts"].append(txt)
            self.capture, self.buf = None, []
        elif tag == "section":
            self.cur = None

    def handle_data(self, data):
        if self.capture:
            self.buf.append(data)


def _report_meta(src: str, raw: str) -> dict:
    """報表層級的 metadata——封面要用，但它不屬於任何章節。

    ⚠ 2026-08-11 之前沒有輸出這個，導致撰稿時封面的資料期間／報表編號／參數
    完全沒有來源，只能自己摸（sub agent 實測：報表編號只好取自檔名）。
    這裡只抓**報表真的有寫**的東西；件數／家族數那類統計仍在各章節的表格裡。
    """
    def one(pat: str) -> str:
        """取最後一個捕獲群組——有些 pattern 的 group(1) 是拿來回指的標籤名。"""
        m = re.search(pat, raw, re.S)
        return " ".join(re.sub(r"<[^>]+>", " ", m.groups()[-1]).split()) if m else ""

    return {
        # 檔名去副檔名——多數報表的「編號」只存在於檔名，正文沒有
        "source_file": os.path.splitext(os.path.basename(src))[0],
        "doc_title": one(r"<title[^>]*>(.*?)</title>"),
        "h1": one(r"<h1[^>]*>(.*?)</h1>"),
        # 產表參數（ranking_limit／ipc_levels／cpc_levels 等），封面 meta 行的來源。
        # ⚠ 不要綁標籤名——實際是 <p class="meta-bar">，寫死 <div> 會抓到空字串
        "meta_bar": one(r'<(\w+)[^>]*class="meta-bar"[^>]*>(.*?)</\1>'),
    }


def main() -> int:
    src, out = sys.argv[1], sys.argv[2]
    raw = open(src, encoding="utf-8").read()
    p = ReportParser()
    p.feed(raw)

    chart_dir = os.path.join(out, "charts")
    os.makedirs(chart_dir, exist_ok=True)

    seen: dict[str, str] = {}
    manifest = []
    for si, s in enumerate(p.sections):
        for c in s["charts"]:
            key = hashlib.sha1(c["svg"].encode("utf-8")).hexdigest()
            fn = re.sub(r"\.web\.svg$|\.svg$", "", c["name"]) + ".svg"
            if key in seen:                       # 同圖的 web / 非 web 版本
                c["file"], c["dup"] = seen[key], True
                continue
            with open(os.path.join(chart_dir, fn), "w", encoding="utf-8") as fh:
                fh.write(c["svg"])
            seen[key] = fn
            c["file"], c["dup"] = fn, False
            manifest.append({"file": fn, "alt": c["name"], "section": s["title"],
                             "section_index": si})

    report = {
        "report_meta": _report_meta(src, raw),
        "sections": [{"title": s["title"], "notes": s["notes"], "texts": s["texts"],
                      "charts": [{"file": c["file"], "alt": c["name"], "dup": c["dup"]}
                                 for c in s["charts"]],
                      "tables": s["tables"]} for s in p.sections],
        "chart_manifest": manifest,
    }
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    print(f"章節 {len(p.sections)} 個｜不重複圖表 {len(manifest)} 張")
    for s in report["sections"]:
        ch = ", ".join(f"{c['file']}{'(dup)' if c['dup'] else ''}" for c in s["charts"]) or "-"
        print(f"  [{s['title']}] 圖：{ch}｜表 {len(s['tables'])}｜判讀 {len(s['texts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
