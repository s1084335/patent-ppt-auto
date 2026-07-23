"""Patent Backend FastAPI 入口。

只負責建立 app 與掛載 route；不執行分群/報表等長時間工作（那些一律建 job
交 worker）。settings 先 import 以確保本機開發時 .env 已載入。
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from backend.app import settings
from backend.app.api import (
    ai_tasks,
    clustering,
    comparison,
    events,
    imports,
    jobs,
    market,
    patents,
    reports,
    topics,
    workspaces,
)
from backend.app.repositories.topic_repository import TopicRepositoryUnavailableError

app = FastAPI(title="Patent Backend", version="0.1.0")

# 報表版本路由：路徑 /reports/versions 會先被 reports.router 的 /reports/{job_id}（int）
# 比對到而回 422，故獨立成 router，於本檔尾端定義完路由後、以 include 插到 reports.router
# 之前（FastAPI 依 app.routes 順序比對）。
report_versions_router = APIRouter()

app.include_router(jobs.router, prefix=settings.API_V1_PREFIX)
app.include_router(clustering.router, prefix=settings.API_V1_PREFIX)
app.include_router(ai_tasks.router, prefix=settings.API_V1_PREFIX)
app.include_router(reports.router, prefix=settings.API_V1_PREFIX)
app.include_router(workspaces.router, prefix=settings.API_V1_PREFIX)
app.include_router(imports.router, prefix=settings.API_V1_PREFIX)
app.include_router(topics.router, prefix=settings.API_V1_PREFIX)
app.include_router(comparison.router, prefix=settings.API_V1_PREFIX)
app.include_router(events.router, prefix=settings.API_V1_PREFIX)
app.include_router(patents.router, prefix=settings.API_V1_PREFIX)
app.include_router(market.router, prefix=settings.API_V1_PREFIX)


_REPORT_LATEST = settings.PROJECT_ROOT / "output" / "full_report_latest" / "index.html"

# 報表輸出根（其下每個子目錄＝一個版本，目錄名即版本）。模組層變數而非常數：
# 測試以 monkeypatch 指到 tmp 目錄，避免碰正式產出。
REPORT_OUTPUT_ROOT = settings.PROJECT_ROOT / "output" / "full_report_latest"

# 圖檔／附件白名單副檔名：只 serve 報表引擎產的圖與 JSON 附件，不開放任意檔案。
_REPORT_ASSET_SUFFIXES = {".svg", ".png", ".jpg", ".jpeg", ".json", ".html"}


@app.get("/api/v1/report-latest")
def serve_latest_report():
    """serve output/full_report_latest/index.html 如存在。"""
    from fastapi.responses import FileResponse, HTMLResponse

    if _REPORT_LATEST.exists():
        return FileResponse(str(_REPORT_LATEST))
    return HTMLResponse(
        content="<p>尚無報表產出。請先執行報表引擎產生 full_report_latest。</p>",
        status_code=404,
    )


def _run_dirs():
    """列出所有有效報表版本目錄（含 report_data.json 才算），依名稱升冪＝時間序。

    版本目錄名帶時間戳（report_trial_/analysis_ 前綴），依名稱排序即時間序；
    不限定前綴，任何含 report_data.json 的子目錄都算，避免綁死單一命名批次。
    只 stat 目錄與該檔存在性，不讀檔內容（列表端點才不會為了列版本全載 JSON）。
    """
    if not REPORT_OUTPUT_ROOT.is_dir():
        return []
    return sorted(
        (p for p in REPORT_OUTPUT_ROOT.iterdir()
         if p.is_dir() and (p / "report_data.json").exists()),
        key=lambda p: p.name,
    )


def _latest_run_dir():
    """取最新的報表版本目錄；無有效版本回 None。"""
    candidates = _run_dirs()
    return candidates[-1] if candidates else None


def _resolve_run_dir(version: str):
    """把版本字串解析成輸出根下的版本目錄；越界或非有效版本回 None（防 path traversal）。

    與 asset 端點同一套防護：resolve 後必須仍在輸出根內，且需含 report_data.json。
    """
    root = REPORT_OUTPUT_ROOT.resolve()
    target = (root / version).resolve()
    if target == root or not target.is_relative_to(root):
        return None
    if not target.is_dir() or not (target / "report_data.json").exists():
        return None
    return target


def _version_generated_at(name: str) -> str:
    """從版本目錄名尾端的 <8位日期>_<6位時間> 解析 ISO 產生時間；解析不出回空字串。

    只看目錄名，不開 report_data.json——列表端點靠這點避免全載。
    """
    import re

    m = re.search(r"(\d{8})_(\d{6})$", name)
    if not m:
        return ""
    d, t = m.group(1), m.group(2)
    return f"{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:{t[2:4]}:{t[4:]}"


def _section_report_key(section: dict) -> str:
    """卡片對應的 report key：有 report_key 用之，否則以第一個 variant 檔名去副檔名
    （與 reports/chart_runner.py 的 _section_report_name 同規則）。"""
    if section.get("report_key"):
        return section["report_key"]
    variants = section.get("variants") or []
    if not variants:
        return ""
    return str(variants[0].get("file", "")).rsplit(".", 1)[0]


def _lookup_rows(report_data: dict, report_key: str) -> list:
    """依 report_key 取數據 rows：reports → family_reports → chart_rows，
    查無且鍵帶 _L<n> 層級尾巴時退基底鍵（IPC/CPC 卡以檔名 fallback 會帶層級）。"""
    for key in (report_key, report_key.rpartition("_L")[0]):
        if not key:
            continue
        for bucket in ("reports", "family_reports"):
            entry = (report_data.get(bucket) or {}).get(key)
            if isinstance(entry, dict) and entry.get("rows"):
                return entry["rows"]
        chart_entry = (report_data.get("chart_rows") or {}).get(key)
        if isinstance(chart_entry, list) and chart_entry:
            return chart_entry
        if isinstance(chart_entry, dict) and chart_entry.get("rows"):
            return chart_entry["rows"]
        if not report_key.rpartition("_L")[2].isdigit():
            break
    return []


def _report_content_payload(run_dir):
    """把一個報表版本目錄組成結構化內容（卡片＋rows＋圖 URL＋解讀）。

    最新版本與指定版本兩支端點共用同一份組裝，回傳形狀一致，前端才能用同一套渲染。
    讀不到 report_data.json 時回 JSONResponse(404)，由呼叫端直接回傳。
    """
    import json

    from fastapi.responses import JSONResponse

    version = run_dir.name
    try:
        report_data = json.loads((run_dir / "report_data.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return JSONResponse(status_code=404, content={"detail": f"報表內容無法讀取：{exc}"})

    # 解讀：版本不符即整份標記過期（對齊 chart_runner._read_narratives 契約）。
    narratives: dict = {}
    narratives_expired = False
    narr_path = run_dir / "narratives.json"
    if narr_path.exists():
        try:
            narr = json.loads(narr_path.read_text(encoding="utf-8"))
            if narr.get("based_on_version") == version:
                narratives = narr.get("reports", {}) or {}
            else:
                narratives_expired = True
        except (json.JSONDecodeError, OSError):
            narratives = {}

    parameters = report_data.get("parameters", {}) or {}
    asset_base = f"{settings.API_V1_PREFIX}/report-latest/asset/{version}/"
    sections_out = []
    for section in report_data.get("sections", []) or []:
        report_key = _section_report_key(section)
        rows = _lookup_rows(report_data, report_key)
        entry = narratives.get(report_key) or narratives.get(report_key.rpartition("_L")[0]) or {}
        variants_out = []
        for variant in list(section.get("variants") or []) + list(section.get("more_variants") or []):
            file_name = str(variant.get("file", ""))
            variant_key = variant.get("variant_key", "default")
            narrative = None
            if not narratives_expired:
                narrative = (entry.get("variants") or {}).get(variant_key)
                if narrative is None and entry.get("text"):
                    narrative = {"text": entry["text"]}  # v1 相容：單一 text 當所有變體預設
            variants_out.append({
                "label": variant.get("label", ""),
                "variant_key": variant_key,
                "file": file_name,
                "chart_url": asset_base + file_name if (run_dir / file_name).exists() else None,
                "narrative": narrative,
            })
        sections_out.append({
            "title": section.get("title", ""),
            "report_key": report_key,
            "note": section.get("note", ""),
            "links": section.get("links", []),
            "row_count": len(rows),
            "rows": rows[:20],  # 顯示上限對齊引擎數據卡（前 20 列＋總列數）
            "variants": variants_out,
        })

    return {
        "version": version,
        "generated_at": parameters.get("generated_at", ""),
        "analysis_id": parameters.get("analysis_id"),
        "scope": parameters.get("scope", ""),
        "patent_count": parameters.get("patent_ids_count"),
        "narratives_expired": narratives_expired,
        "sections": sections_out,
    }


@app.get("/api/v1/report-latest/content")
def serve_latest_report_content():
    """最新報表版本的結構化內容，供前端預覽/編輯（形狀見 _report_content_payload）。"""
    from fastapi.responses import JSONResponse

    run_dir = _latest_run_dir()
    if run_dir is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "尚無報表產出：請先於報表種類頁產製報表。"},
        )
    return _report_content_payload(run_dir)


@report_versions_router.get("/reports/versions")
def list_report_versions(limit: int | None = None):
    """列出所有報表版本（新到舊），供前端「最新展開＋舊版收合」的版本清單。

    效率：只讀目錄名與兩個檔案的存在性，不開任何 report_data.json——版本一多也不會慢。
    卡片數等需要開檔的資訊留給展開時的 content 端點取（lazy）。
    limit 只截清單長度，total 仍回實際總數供前端「顯示更多」。
    """
    run_dirs = list(reversed(_run_dirs()))  # 新到舊
    total = len(run_dirs)
    if limit is not None and limit > 0:
        run_dirs = run_dirs[:limit]
    versions = [
        {
            "version": p.name,
            "generated_at": _version_generated_at(p.name),
            "is_latest": idx == 0,
            "has_narratives": (p / "narratives.json").exists(),
        }
        for idx, p in enumerate(run_dirs)
    ]
    return {"versions": versions, "total": total}


@report_versions_router.get("/reports/versions/{version}/content")
def serve_report_version_content(version: str):
    """指定報表版本的結構化內容；形狀同 /report-latest/content，前端共用同一套渲染。

    版本參數沿用 asset 端點的防護：resolve 後須仍在輸出根內，否則一律 404。
    """
    from fastapi.responses import JSONResponse

    run_dir = _resolve_run_dir(version)
    if run_dir is None:
        return JSONResponse(status_code=404, content={"detail": f"報表版本不存在：{version}"})
    return _report_content_payload(run_dir)


@app.get("/api/v1/report-latest/asset/{version}/{filename}")
def serve_latest_report_asset(version: str, filename: str):
    """serve 指定報表版本目錄下的圖檔／附件；限白名單副檔名且不得逃出輸出根。"""
    from fastapi.responses import FileResponse, JSONResponse

    from pathlib import PurePosixPath

    if PurePosixPath(filename).suffix.lower() not in _REPORT_ASSET_SUFFIXES:
        return JSONResponse(status_code=404, content={"detail": "不支援的檔案類型"})
    root = REPORT_OUTPUT_ROOT.resolve()
    target = (root / version / filename).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return JSONResponse(status_code=404, content={"detail": "檔案不存在"})
    return FileResponse(str(target))


@app.get("/")
def serve_frontend():
    """前端最小頁（單一 HTML + 原生 JS）。"""
    from pathlib import Path
    from fastapi.responses import HTMLResponse

    html_path = Path(__file__).resolve().parent / "static" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# 版本路由必須排在 reports.router 的 /reports/{job_id} 之前才不會被 int 參數攔成 422。
# include 會把路由 append 到尾端，故 include 後再搬到 app.routes 最前面。
_versions_route_count = len(report_versions_router.routes)
app.include_router(report_versions_router, prefix=settings.API_V1_PREFIX)
app.router.routes[:0] = [app.router.routes.pop() for _ in range(_versions_route_count)]


@app.exception_handler(TopicRepositoryUnavailableError)
async def topic_repo_unavailable_handler(request: Request, exc: TopicRepositoryUnavailableError):
    """Repository 未配置時回傳 503。"""
    return JSONResponse(status_code=503, content={"detail": str(exc)})
