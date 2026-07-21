# -*- coding: utf-8 -*-
"""案件比對第六輪端到端煙測：extract_target → fetch PDF → render 全頁＋contact sheet
→ save_illustrations，並產出單頁 HTML 彙整（output/comparison/round6_smoke_{date}.html）。

範圍與安全：
- 讀 patent_ppt_understanding（127.0.0.1:5433）選有 PDF 連結者；正式庫 patent_ppt 不碰。
- 寫入僅限測試庫的 comparison run（create_case／save_target／save_illustrations，版本 append）。
- WIPS 直下載連結可能有時效/權限：逐件嘗試並如實記錄失敗樣態，不硬通。
- 若連結全數失效：以 pymupdf 程式內生成的迷你 PDF 走「管線演示」fallback
  （明確標注非該專利真說明書），仍驗證 render＋save_illustrations 鏈路與數字。

執行：$env:RUN_NET_TESTS='1'; $env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/comparison_round6_smoke.py
"""
from __future__ import annotations

import base64
import html
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

MAX_LINK_ATTEMPTS = 3  # 逐件嘗試的連結數上限（全失效即記錄樣態，不硬通）

SMOKE_DB_KW: dict = dict(host="127.0.0.1", port=int(os.getenv("PGPORT", "5433")),
                         user=os.getenv("PGUSER", "postgres"), dbname="patent_ppt_understanding")
if os.getenv("PGPASSWORD"):
    SMOKE_DB_KW["password"] = os.getenv("PGPASSWORD")


def _pick_candidates(limit: int) -> list[int]:
    """唯讀挑有 PDF 連結且主權項有值的專利 id（取 id 最小者起，結果可重現）。"""
    import psycopg

    with psycopg.connect(**SMOKE_DB_KW) as c:
        rows = c.execute(
            'SELECT p.id FROM core_layer.patents p '
            "JOIN core_layer.patent_attributes a ON a.patent_id = p.id "
            'WHERE a."文圖像文件(PDF)連結" IS NOT NULL AND a."文圖像文件(PDF)連結" <> \'\' '
            'AND p."主權項" IS NOT NULL ORDER BY p.id LIMIT %s', (limit,)).fetchall()
    if not rows:
        raise SystemExit("測試庫無可用（有連結＋有主權項）之專利")
    return [r[0] for r in rows]


def _probe_failure_detail(url: str) -> str:
    """連結失效時補抓一次原始回應，萃取 HTML title 與訊息供失敗樣態記錄。"""
    import httpx

    try:
        r = httpx.get(url, timeout=20, follow_redirects=True)
        txt = r.content.decode("utf-8", errors="replace")
        title = re.search(r"<title>(.*?)</title>", txt, re.S)
        body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt)).strip()
        return (f"HTTP {r.status_code}, content-type={r.headers.get('content-type')}, "
                f"{len(r.content)} bytes, title={title.group(1).strip() if title else '?'}, "
                f"內文節錄：{body[:160]}")
    except Exception as exc:  # noqa: BLE001 — 樣態補查失敗也如實記錄
        return f"樣態補查失敗：{exc}"


def _make_demo_pdf(path: Path, pages: int = 3) -> None:
    """pymupdf 程式內生成迷你 PDF（fallback 管線演示用，非真說明書）。"""
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)  # A4
        page.insert_text((60, 80), f"ROUND6 PIPELINE DEMO - page {i + 1}/{pages}", fontsize=18)
        page.insert_text((60, 120), "Generated locally; NOT the patent's real specification PDF.")
        page.draw_rect(pymupdf.Rect(60, 160, 535, 700), width=1.5)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()


def _b64_png(path: Path) -> str:
    """把 PNG 轉 data URI，讓 HTML 自含縮圖。"""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def main() -> None:
    from backend.app.comparison.comparison_store import ComparisonStore
    from backend.app.comparison.patent_images import PatentImagePipeline, PymupdfRenderer
    from backend.app.comparison.pdf_fetch import PdfFetchError, fetch_patent_pdf
    from backend.app.comparison.target_source import extract_target_from_db

    report: dict = {"steps": [], "attempts": [], "failure": None, "demo": False}
    assets_base = PROJECT_ROOT / "data" / "patent_assets"
    candidates = _pick_candidates(MAX_LINK_ATTEMPTS)

    # ── 1. 標的抽取（DB 唯一來源，模擬標的；以第一件為標的）──
    t0 = time.perf_counter()
    target = extract_target_from_db(candidates[0], connect_kwargs=SMOKE_DB_KW)
    report["target"] = target
    report["steps"].append(("extract_target", f"patent_id={candidates[0]}",
                            f"{time.perf_counter()-t0:.2f}s"))

    # ── 2. 建 comparison run 並回存 target（版本 append，payload 標 simulated=true）──
    store = ComparisonStore(connect_kwargs=SMOKE_DB_KW)
    run_id = store.create_case(target["patent_number"] or str(candidates[0]),
                               f"第六輪煙測：以專利 {candidates[0]} 模擬標的", "round6-smoke")
    tv = store.save_target(run_id, target)
    report["run_id"], report["target_version"] = run_id, tv
    report["steps"].append(("save_target", f"run_id={run_id} v{tv}", "-"))

    # ── 3. 真下載：逐件嘗試，失敗樣態逐筆記錄，不硬通 ──
    fetched = None
    for pid in candidates:
        cand = target if pid == candidates[0] else extract_target_from_db(pid, connect_kwargs=SMOKE_DB_KW)
        try:
            t0 = time.perf_counter()
            fetched = fetch_patent_pdf(url=cand["pdf_url"], patent_number=cand["patent_number"],
                                       base_dir=str(assets_base))
            report["attempts"].append((pid, f"成功：{fetched['size_bytes']} bytes，"
                                            f"{time.perf_counter()-t0:.2f}s"))
            target = cand  # 下載成功者作為後續渲染對象
            break
        except PdfFetchError as exc:
            report["attempts"].append((pid, f"失敗：{str(exc)[:140]}"))

    if fetched is None:
        report["failure"] = ("WIPS 直下載連結全數失效（前 "
                             f"{len(report['attempts'])} 件同樣態）。樣態詳查："
                             + _probe_failure_detail(target["pdf_url"]))
        report["steps"].append(("fetch_pdf", "FAILED（全數連結）", "-"))
        # fallback：本地生成迷你 PDF 走管線演示（明確標注非真說明書）
        report["demo"] = True
        demo_pdf = PROJECT_ROOT / "data" / "patent_assets" / "_round6_demo" / "demo.pdf"
        _make_demo_pdf(demo_pdf)
        import hashlib
        sha = hashlib.sha256(demo_pdf.read_bytes()).hexdigest()
        fetched = {"pdf_path": str(demo_pdf), "sha256": sha,
                   "size_bytes": demo_pdf.stat().st_size, "from_cache": False}
        render_pn = "DEMO_ROUND6"
    else:
        report["steps"].append(("fetch_pdf", f"{fetched['size_bytes']} bytes "
                                             f"sha256={fetched['sha256'][:12]}… "
                                             f"from_cache={fetched['from_cache']}", "-"))
        render_pn = target["patent_number"]

    # ── 4. 全頁渲染＋contact sheet（真 renderer）→ 相對路徑回存 illustrations ──
    import pymupdf

    with pymupdf.open(fetched["pdf_path"]) as doc:
        page_count = len(doc)
    t0 = time.perf_counter()
    pipeline = PatentImagePipeline(str(assets_base), PymupdfRenderer())
    rendered = pipeline.render(render_pn, fetched["sha256"],
                               fetched["pdf_path"], list(range(1, page_count + 1)))
    rd_secs = time.perf_counter() - t0
    iv = store.save_illustrations(run_id, rendered["page_paths"] + [rendered["contact_sheet_path"]])
    report["rendered"], report["page_count"], report["fetched"] = rendered, page_count, fetched
    report["illustrations_version"] = iv
    label = "（fallback 演示 PDF）" if report["demo"] else ""
    report["steps"].append(("render" + label, f"{page_count} 頁 + contact sheet", f"{rd_secs:.2f}s"))
    report["steps"].append(("save_illustrations", f"v{iv}（{len(rendered['page_paths']) + 1} 檔）", "-"))
    _write_html(report, assets_base)


def _write_html(report: dict, assets_base: Path) -> None:
    """產出單頁 HTML 彙整：標的摘要＋嘗試紀錄＋（縮圖與數字或失敗樣態）。"""
    t = report.get("target", {})

    def esc(v, limit=300):
        s = "" if v is None else str(v)
        return html.escape(s[:limit] + ("…" if len(s) > limit else ""))

    rows = "".join(
        f"<tr><th>{k}</th><td>{esc(v)}</td></tr>" for k, v in [
            ("patent_id", t.get("patent_id")), ("patent_number", t.get("patent_number")),
            ("title", t.get("title")), ("abstract", t.get("abstract")),
            ("main_claim", t.get("main_claim")), ("pdf_url", t.get("pdf_url")),
            ("simulated", t.get("simulated")),
            ("run_id / target 版本", f"{report.get('run_id')} / v{report.get('target_version')}"),
        ])
    steps = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td></tr>"
                    for a, b, c in report["steps"])
    attempts = "".join(f"<tr><td>{pid}</td><td>{esc(msg, 200)}</td></tr>"
                       for pid, msg in report["attempts"])

    body = [f"<h1>案件比對第六輪煙測（{date.today().isoformat()}）</h1>",
            f"<h2>標的資料摘要（模擬標的，DB 唯一來源）</h2><table>{rows}</table>",
            f"<h2>執行步驟與耗時</h2><table><tr><th>步驟</th><th>結果</th><th>耗時</th></tr>{steps}</table>",
            f"<h2>PDF 下載嘗試（逐件）</h2><table><tr><th>patent_id</th><th>結果</th></tr>{attempts}</table>"]

    if report.get("failure"):
        body.append("<h2 class='fail'>下載失敗樣態（如實記錄）</h2>"
                    f"<p class='fail'>{esc(report['failure'], 900)}</p>")

    if report.get("rendered"):
        r, f = report["rendered"], report["fetched"]
        demo_note = ("<p class='fail'><b>注意：</b>以下渲染對象為本地生成之管線演示 PDF"
                     "（連結全數失效的 fallback），<b>非該專利真說明書</b>。</p>"
                     if report["demo"] else "")
        body.append("<h2>下載／渲染數字</h2>" + demo_note + "<ul>"
                    f"<li>PDF：{f['size_bytes']:,} bytes，sha256={f['sha256']}，from_cache={f['from_cache']}</li>"
                    f"<li>頁數：{report['page_count']}；頁圖 {len(r['page_paths'])} 檔＋contact sheet 1 檔</li>"
                    f"<li>illustrations 回存版本：v{report['illustrations_version']}"
                    "（相對路徑，基準 data/patent_assets/）</li></ul>")
        cs_abs = assets_base / r["contact_sheet_path"]
        body.append(f"<h2>Contact sheet 縮圖</h2><img src='{_b64_png(cs_abs)}' "
                    "style='max-width:100%;border:1px solid #ccc'>")
        items = "".join(f"<li><code>{html.escape(p)}</code></li>" for p in r["page_paths"])
        body.append(f"<h2>頁圖清單（相對 data/patent_assets/）</h2><ol>{items}</ol>")

    doc = ("<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
           "<title>round6 smoke</title><style>body{font-family:'Microsoft JhengHei',sans-serif;"
           "max-width:960px;margin:2rem auto;padding:0 1rem}table{border-collapse:collapse;width:100%}"
           "th,td{border:1px solid #ccc;padding:4px 8px;text-align:left;vertical-align:top}"
           "th{background:#f5f5f5;white-space:nowrap}.fail{color:#b00020}</style></head><body>"
           + "".join(body) + "</body></html>")
    out = PROJECT_ROOT / "output" / "comparison" / f"round6_smoke_{date.today().isoformat()}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(f"HTML: {out}")
    if report.get("failure"):
        print(f"FAILURE MODE: {report['failure'][:300]}")
    print(f"run_id={report['run_id']} pages={report.get('page_count')} "
          f"illustrations=v{report.get('illustrations_version')} demo={report['demo']}")


if __name__ == "__main__":
    main()
