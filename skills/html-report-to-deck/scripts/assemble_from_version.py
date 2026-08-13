"""從報表**版本目錄**組出 deck 的中間格式（deck 第 1 步的正式入口）。

輸出（與 `extract_report.py` 完全相同的形狀，下游不需知道換了 intake）：
  <out>/charts/*.svg   每張圖表的 SVG
  <out>/report.json    章節、表格、判讀文字、圖表清單

## 為什麼不再走 HTML

`extract_report.py` 是把**產好的 HTML** 再解析回結構——繞路。引擎本來就有
`report_data.json`，HTML 只是它的一種呈現。繞這圈付兩次代價：解析器得跟著
HTML 版面走（線一改章節式即一例），且 HTML 丟失 `report_key`／`variant_key`
這類引擎原生識別。`extract_report.py` 自 2026-08-12 起降為 HTML fallback。

用法：python assemble_from_version.py <version_dir> <out_dir>
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any


def _read_json(path: Path, default: Any = None) -> Any:
    """讀 JSON；檔案不存在時回 default（不是每個版本都有 narratives.json）。"""
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _rows_to_table(rows: list[dict]) -> dict | None:
    """dict 列 → HTML intake 的 `{head, rows}` 形狀。

    ⚠ 下游（`plan_deck`／`check_content`）吃的是「表頭 ＋ 逐列的值陣列」，
    不是 dict 列。這裡負責轉換，換 intake 才不會逼下游改。
    欄序以第一列的鍵為準（引擎產出的 dict 有序，與報表欄序一致）。
    """
    if not rows or not isinstance(rows[0], dict):
        return None
    head = list(rows[0])
    return {"head": head,
            "rows": [[_cell(r.get(k)) for k in head] for r in rows]}


def _cell(value: Any) -> str:
    """值 → 顯示字串。None 顯示空字串，不寫成 "None"。"""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _section_notes(section: dict, table_display: dict) -> list[str]:
    """章節的 notes——三個來源合併。

    ⚠ 少收任何一個都不會報錯，只會讓簡報少掉口徑說明：
    1. `section.note`：該報表自己的口徑註記
    2. `table_display.encoding_notes[report_key]`：欄位編碼說明
    3. `table_display.reader_guide`：全報告共通的判讀指引（list of {title, body}）

    reader_guide 是**報告層級**的，只掛在第一個章節，避免每頁重複。
    掛哪一節由呼叫端決定（傳不傳 `include_guide`）。
    """
    notes: list[str] = []
    note = (section.get("note") or "").strip()
    if note:
        notes.append(note)
    encoding = (table_display.get("encoding_notes") or {}).get(section.get("report_key"))
    if encoding:
        notes.append(str(encoding).strip())
    return notes


def _guide_notes(table_display: dict) -> list[str]:
    """reader_guide → 每則 `標題：內文` 一行。"""
    out: list[str] = []
    for item in table_display.get("reader_guide") or []:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            body = str(item.get("body") or "").strip()
            out.append(f"{title}：{body}" if title else body)
        else:
            out.append(str(item).strip())
    return [s for s in out if s]


def _section_texts(section: dict, narratives: dict) -> list[str]:
    """該章節各 variant 的 AI 解讀。沒有 narratives.json 時回空清單。

    ⚠ 對回方式是 `narrative_key`（引擎產出時寫在 variant 上），不靠章節標題比對
    ——標題會因報表改版而變，key 不會。
    ⚠ **但不是每個 variant 都有這個鍵**：2026-08-13 自真實產物反解，帶 `rows` 的
    variant（Key Players、機會矩陣、主題演進）就沒有。缺鍵時以
    `report_key:variant_key` 推導——那正是有鍵者的組成方式。
    不做這層 fallback 的話，那些章節的解讀會**靜默消失**，沒有閘門會提醒。
    """
    if not narratives:
        return []
    report_key = section.get("report_key")
    texts: list[str] = []
    for variant in section.get("variants") or []:
        key = variant.get("narrative_key")
        if not key and report_key and variant.get("variant_key"):
            key = f"{report_key}:{variant['variant_key']}"
        entry = narratives.get(key) if key else None
        if not entry:
            continue
        text = entry.get("text") if isinstance(entry, dict) else entry
        if text:
            texts.append(str(text).strip())
    return texts


def _section_tables(section: dict, reports: dict) -> list[dict]:
    """章節的表格——rows 散在三處，逐處收。

    ⚠ 2026-08-13 自真實產物反解：不同報表把列放在不同地方，只讀一處會**靜默少表**。
    1. `reports[report_key].rows`：一般彙總報表
    2. `section.rows`：章節層級（如主題分析）
    3. `variant.rows`：變體各自帶（如機會矩陣、Key Players）
    """
    tables: list[dict] = []
    seen: list[list[str]] = []

    def add(rows: Any) -> None:
        if not isinstance(rows, list):
            return
        table = _rows_to_table(rows)
        # 同一批列可能同時掛在兩處（引擎為了方便前端），用表頭＋列數去重。
        if table and [table["head"], len(table["rows"])] not in seen:
            seen.append([table["head"], len(table["rows"])])
            tables.append(table)

    report_key = section.get("report_key")
    if report_key and isinstance(reports.get(report_key), dict):
        add(reports[report_key].get("rows"))
    add(section.get("rows"))
    for variant in section.get("variants") or []:
        add(variant.get("rows"))
    return tables


def assemble(version_dir: Path | str, out_dir: Path | str) -> dict:
    """版本目錄 → `<out>/report.json` ＋ `<out>/charts/`；回傳 report dict。"""
    version_dir, out_dir = Path(version_dir), Path(out_dir)
    report_data = _read_json(version_dir / "report_data.json", {}) or {}
    version_meta = _read_json(version_dir / "version_meta.json", {}) or {}
    narratives = _read_json(version_dir / "narratives.json", {}) or {}

    reports: dict = report_data.get("reports") or {}
    table_display: dict = report_data.get("table_display") or {}

    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)

    sections_out: list[dict] = []
    manifest: list[dict] = []
    copied: set[str] = set()
    guide = _guide_notes(table_display)

    for index, section in enumerate(report_data.get("sections") or []):
        charts: list[dict] = []
        # ⚠ 只走 `variants`，**不碰 `more_variants`**：那是第 11–20 名，
        #   排頁規則明訂剔除（SKILL.md「排名類只保留前 10」）。
        for variant in section.get("variants") or []:
            file_name = (variant.get("file") or "").strip()
            # ⚠ `file` 空字串＝該 variant 是解讀落點（如主題統計表，表格由組版端畫），
            #   不是圖表。列進來會讓下游找不到 PNG。
            if not file_name:
                continue
            src = version_dir / file_name
            if not src.is_file():
                continue
            if file_name not in copied:
                shutil.copyfile(src, chart_dir / file_name)
                copied.add(file_name)
                manifest.append({"file": file_name,
                                 "alt": variant.get("label") or file_name,
                                 "section": section.get("title"),
                                 "section_index": index})
                dup = False
            else:
                dup = True
            charts.append({"file": file_name,
                           "alt": variant.get("label") or file_name,
                           "dup": dup})

        notes = _section_notes(section, table_display)
        if index == 0 and guide:
            notes += guide
        sections_out.append({
            "title": section.get("title"),
            "notes": notes,
            "texts": _section_texts(section, narratives),
            "charts": charts,
            "tables": _section_tables(section, reports),
        })

    report = {
        "report_meta": _report_meta(version_meta, report_data),
        "sections": sections_out,
        "chart_manifest": manifest,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def _report_meta(version_meta: dict, report_data: dict) -> dict:
    """封面素材。欄位名沿用 HTML intake，值改由版本目錄供給。

    🔴 `workspace_name` 是封面技術名稱的來源（design 4b：使用者指定
    「技術名稱＝workspace 名稱」，如「自走式割草機」）；全庫版本沒有 workspace
    時退回報表標題。
    """
    workspace_name = (version_meta.get("workspace_name") or "").strip()
    parameters = report_data.get("parameters") or {}
    meta_bar = "｜".join(
        f"{k}={json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v}"
        for k, v in parameters.items()
        if k in ("ranking_limit", "ipc_levels", "cpc_levels", "generated_at"))
    return {
        "source_file": version_meta.get("version") or "",
        "doc_title": workspace_name or "專利分析報告",
        "h1": workspace_name or "專利分析報告",
        "meta_bar": meta_bar,
        # 版本目錄獨有、HTML intake 沒有的欄位——封面與追溯要用。
        "workspace_name": workspace_name,
        "workspace_id": version_meta.get("workspace_id"),
        "generated_at": version_meta.get("generated_at") or "",
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__.strip().splitlines()[-1])
        return 2
    report = assemble(sys.argv[1], sys.argv[2])
    print(f"章節 {len(report['sections'])} 個｜不重複圖表 "
          f"{len(report['chart_manifest'])} 張")
    for s in report["sections"]:
        ch = ", ".join(f"{c['file']}{'(dup)' if c['dup'] else ''}"
                       for c in s["charts"]) or "-"
        print(f"  [{s['title']}] 圖：{ch}｜表 {len(s['tables'])}"
              f"｜判讀 {len(s['texts'])}｜註記 {len(s['notes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
