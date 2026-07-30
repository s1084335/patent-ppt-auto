"""Patent Backend FastAPI 入口。

只負責建立 app 與掛載 route；不執行分群/報表等長時間工作（那些一律建 job
交 worker）。settings 先 import 以確保本機開發時 .env 已載入。
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app import settings
from backend.app.db import report_artifact_store
from backend.app.api import (
    ai_tasks,
    clustering,
    company_aliases,
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
# 公司中文名草稿確認：補上三態流程的「確認」環節（原本產得出草稿但無處確認）。
app.include_router(company_aliases.router, prefix=settings.API_V1_PREFIX)

_STATIC_DIR = settings.PROJECT_ROOT / "backend" / "app" / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


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


# ---------------------------------------------------------------------------
# 報表產物來源（2026-07-23 跨容器定案）
#
# Railway 上 worker 與 backend 是不同容器、檔案系統不共享，worker 產的報表目錄
# backend 讀不到，故 worker 產完即整包上傳 app_layer.report_artifacts。讀取端一律
# 先看本機檔案系統（本機開發／CLI 直接出圖的情境不變、不多打一次 DB），沒有才讀 DB。
#
# 兩種來源以同一個小介面包起來（name／read_bytes／exists），下面的組裝與端點只依賴
# 介面，API 形狀完全不變（content 回結構化內容、asset 回圖檔）。
# ---------------------------------------------------------------------------


class _DirRunSource:
    """本機檔案系統上的報表版本目錄。"""

    def __init__(self, path):
        self.path = path
        self.name = path.name

    def read_bytes(self, filename: str):
        """讀取該版本目錄下的檔案；不存在回 None。"""
        target = self.path / filename
        if not target.is_file():
            return None
        return target.read_bytes()

    def exists(self, filename: str) -> bool:
        return (self.path / filename).is_file()


class _DbRunSource:
    """存在 app_layer.report_artifacts 的報表版本（worker 容器產、backend 容器讀）。

    每個檔案單獨取回並在本次請求內快取——一次 content 組裝要問多個檔案的存在性
    （每張圖一次），不快取會對同一版重覆打 DB。
    """

    def __init__(self, version: str, *, has_narratives: bool | None = None):
        self.name = version
        # list_versions 已用 SQL 聚合算出有無解讀；帶進來讓列表端點不必為一個布林值撈內容。
        self.has_narratives_hint = has_narratives
        self._cache: dict[str, bytes | None] = {}

    def read_bytes(self, filename: str):
        if filename not in self._cache:
            self._cache[filename] = _db_read_artifact(self.name, filename)
        return self._cache[filename]

    def exists(self, filename: str) -> bool:
        return self.read_bytes(filename) is not None


def _db_read_artifact(version: str, filename: str):
    """從 DB 取單一產物；DB 不可用（未 migrate、連線失敗）時視為「沒有」而非 500。

    本機開發與測試環境不一定有 report_artifacts 表；跨容器讀取是**補位**路徑，
    失敗只該讓端點回 404（找不到報表），不該把整個報表頁打成 500。
    """
    try:
        return report_artifact_store.read_file(version, filename)
    except Exception:  # noqa: BLE001 - 補位路徑失敗即視為無此產物
        return None


def _list_run_sources():
    """所有有效報表版本（本機目錄 ＋ DB 產物），依版本名升冪＝時間序、同名以本機優先。

    效率：本機只 stat 檔案存在性、DB 只查 metadata（不選 content），版本一多也不會慢。
    """
    sources = {p.name: _DirRunSource(p) for p in _run_dirs()}
    try:
        for entry in report_artifact_store.list_versions():
            sources.setdefault(
                entry["version"],
                _DbRunSource(entry["version"], has_narratives=bool(entry.get("has_narratives"))),
            )
    except Exception:  # noqa: BLE001 - DB 不可用時仍回本機版本，不讓報表頁整個掛掉
        pass
    return [sources[name] for name in sorted(sources)]


def _latest_run_dir():
    """取最新的報表版本來源；無有效版本回 None。"""
    candidates = _list_run_sources()
    return candidates[-1] if candidates else None


def _resolve_run_dir(version: str):
    """把版本字串解析成報表版本來源；越界或非有效版本回 None（防 path traversal）。

    先試本機：resolve 後必須仍在輸出根內，且需含 report_data.json。本機沒有才查 DB
    產物；版本名帶路徑分隔或 .. 一律先擋掉（DB 端不走檔案系統，仍照同一套規則拒絕，
    避免兩條路徑防護不一致）。
    """
    root = REPORT_OUTPUT_ROOT.resolve()
    target = (root / version).resolve()
    if target != root and target.is_relative_to(root):
        if target.is_dir() and (target / "report_data.json").exists():
            return _DirRunSource(target)
    if not _is_safe_version(version):
        return None
    source = _DbRunSource(version)
    return source if source.exists("report_data.json") else None


def _is_safe_version(version: str) -> bool:
    """版本名必須是單一路徑元件（無分隔符、非 . / ..），DB 端沿用同一套防護。"""
    if not version or version in (".", ".."):
        return False
    return not any(sep in version for sep in ("/", "\\")) and ".." not in version


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


def _report_layout(report_key: str) -> str:
    """該報表的前端版面（唯一來源＝ReportDefinition.layout）。

    未知 report_key（動態頁、分群 section）回預設值，不 raise
    ——版面是呈現細節，取不到就用一般版面，不該讓整份 content 失敗。
    """
    from backend.app.reports.report_definitions import REPORT_DEFINITIONS

    d = REPORT_DEFINITIONS.get(report_key)
    return getattr(d, "layout", "side_by_side") if d else "side_by_side"


def _column_labels(rows: list) -> dict:
    """本批 rows 的欄位中文名對照（R2，2026-07-29）。

    唯一來源＝`chart_runner.DATA_COLUMN_LABELS`——那份對照表早就存在且完整
    （`patent_count → 專利件數` 等），前端卻自己用 `Object.keys(rows[0])`
    吐原始 key，等於同一資訊兩處落點。

    查無對照的欄不放進回應，前端據此退回顯示原 key
    ——後端日後新增欄位不必同步改前端。
    """
    if not rows or not isinstance(rows[0], dict):
        return {}
    from backend.app.reports.chart_runner import DATA_COLUMN_LABELS

    return {c: DATA_COLUMN_LABELS[c] for c in rows[0] if c in DATA_COLUMN_LABELS}


def _hidden_columns(report_key: str) -> list:
    """該報表不顯示的欄（唯一來源＝chart_runner.DATA_TABLE_EXCLUDED_COLUMNS）。

    ⚠ 「不顯示」不等於「不存在」：這些欄的資料仍在 rows 裡，供圖表與機制使用。
    例：`recent_assignee_count` 是 applicant_ranking 圖表的 segment_key（藍色區段），
    `topic_code` 是主題合併/拆分的識別鍵——移掉資料會壞掉，只能不顯示。

    前端原本硬排除 `source_field` 一欄，與後端這份清單各走各的（第 6 個兩處落點）；
    改由後端統一送出，前端零判斷。
    """
    from backend.app.reports.chart_runner import DATA_TABLE_EXCLUDED_COLUMNS

    return list(DATA_TABLE_EXCLUDED_COLUMNS.get(report_key, ()))


def _limit_rows_per_source(rows: list, limit: int) -> list:
    """列數上限**按 source_field 各自計**，不是整體取前 N。

    ⚠ 2026-07-28：分群報表的兩個通道（技術／功效）共用同一份 rows。整體
    `rows[:20]` 會讓排在後面的功效通道被整批切掉——技術主題 ≥ 20 個時一列不剩。
    前端切到「功效」濾出空陣列後，`sectionForReportView` 的
    `rows.length ? rows : section.rows` fallback 會**退回未過濾的全部列**，
    使用者按了功效卻看到技術資料且無提示，比空白更難發現。

    無 source_field 的 rows（非分群報表）走原本的整體上限，行為不變。
    通道內順序保持原樣（上游 build_topic_effect_table 已排好技術先功效後）。
    """
    if not rows:
        return rows
    if not any(r.get("source_field") for r in rows if isinstance(r, dict)):
        return rows[:limit]
    seen: dict[str, int] = {}
    out = []
    for row in rows:
        key = str(row.get("source_field") or "") if isinstance(row, dict) else ""
        if seen.get(key, 0) >= limit:
            continue
        seen[key] = seen.get(key, 0) + 1
        out.append(row)
    return out


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
    """把一個報表版本（本機目錄或 DB 產物）組成結構化內容（卡片＋rows＋圖 URL＋解讀）。

    最新版本與指定版本兩支端點共用同一份組裝，回傳形狀一致，前端才能用同一套渲染。
    讀不到 report_data.json 時回 JSONResponse(404)，由呼叫端直接回傳。
    """
    import json

    from fastapi.responses import JSONResponse

    version = run_dir.name
    raw = run_dir.read_bytes("report_data.json")
    if raw is None:
        return JSONResponse(status_code=404, content={"detail": "報表內容無法讀取：缺 report_data.json"})
    try:
        report_data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return JSONResponse(status_code=404, content={"detail": f"報表內容無法讀取：{exc}"})

    # 解讀：版本不符即整份標記過期（對齊 chart_runner._read_narratives 契約）。
    narratives: dict = {}
    narratives_expired = False
    narr_raw = run_dir.read_bytes("narratives.json")
    if narr_raw is not None:
        try:
            narr = json.loads(narr_raw.decode("utf-8"))
            if narr.get("based_on_version") == version:
                narratives = narr.get("reports", {}) or {}
            else:
                narratives_expired = True
        except (json.JSONDecodeError, UnicodeDecodeError):
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
                "chart_url": asset_base + file_name if run_dir.exists(file_name) else None,
                "narrative": narrative,
            })
        sections_out.append({
            "title": section.get("title", ""),
            "report_key": report_key,
            "note": section.get("note", ""),
            "links": section.get("links", []),
            "row_count": len(rows),
            # 顯示上限對齊引擎數據卡（20 列＋總列數）。分群報表兩通道各自計算上限，
            # 否則排在後面的功效通道會被整批切掉（見 _limit_rows_per_source）。
            "rows": _limit_rows_per_source(rows, 20),
            # 欄位中文名（R2）：唯一來源＝chart_runner.DATA_COLUMN_LABELS。
            # 前端原本用 Object.keys(rows[0]) 直接吐 patent_count 這種原始 key，
            # 而後端早就有完整對照表——第 5 個「同一資訊兩處落點」。
            # 只回本次 rows 實際有的欄，不整份倒給前端。
            "column_labels": _column_labels(rows),
            # 版面（2026-07-29）：直接放進 section，前端零判斷、零清單。
            # 前端的 REPORT_TYPES 是寫死清單且不含 layout，靠它判斷會是第 6 個
            # 「同一資訊兩處落點」——讓後端把答案送過來即可。
            "layout": _report_layout(report_key),
            # 不顯示但保留資料的欄（見 _hidden_columns）
            "hidden_columns": _hidden_columns(report_key),
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


def _pages_for_report_data(report_data: dict, builder=None) -> list[dict]:
    """以 build_ppt 的 `_expand_page_layout` 把 report_data 轉成前端頁面 JSON。

    ⚠ 頁面展開的**唯一實作**＝build_ppt（產檔時真正用的那一份）。原本 API 端
    自有一套展開（reports.py，甚至同檔兩份），與 build_ppt 三個維度全不一致：
    動態頁來源（全部報表定義 vs 該版實際產出）、插入錨點（標題關鍵字 vs page>=8）、
    順序（定義 dict 序 vs report_data 條目序）。而版型／座標覆寫都以**頁碼**為 key
    ——預覽頁碼 ≠ 產檔頁碼時，使用者在預覽第 N 頁拖的元件會套到成品另一頁上，
    且預覽會列出一堆該版根本沒產的空頁讓人白編輯。（2026-07-29 全線體檢定案）
    """
    if builder is None:
        from backend.app.worker import ai_report_ppt_runner

        builder = ai_report_ppt_runner._load_builder()
    return [
        {
            "page": int(spec.page),
            "kind": str(spec.kind),
            "title": str(spec.title),
            "subtitle": str(spec.subtitle or ""),
            "report_keys": list(spec.report_keys),
            "charts": list(spec.charts),
            "slots": list(spec.slots),
        }
        for spec in builder._expand_page_layout(report_data)
    ]


@report_versions_router.get("/reports/ppt-layout")
def get_report_ppt_layout(version: str | None = None):
    """PPT 預覽版型：theme ＋ 依該版 report_data 展開的頁面（未給 version＝最新版）。

    掛在 report_versions_router：這組路由會被搬到 app.routes 最前，天然避開
    `/reports/{job_id}` 把 `ppt-layout` 吃成 int 的 422——不再靠註解提醒宣告順序
    （舊實作在 reports.py 內就是靠註解，且重複兩份，已於 2026-07-29 移除）。

    kinds 回 build_ppt 的全部 renderer（換版型下拉的合法值域），不是「已用到的
    kind 集合」——否則使用者永遠換不到當前沒用的版型。

    503＝部署環境缺 skill 檔案（build_ppt.py／theme.json 不在容器 image 內）：
    明確報錯勝過默默壞掉——本機開發時祖先目錄的 .agents 會掩蓋此問題，
    只有部署後才暴露（與 9d 跨容器斷鏈同類）。
    """
    import json as _json

    from backend.app.worker import ai_report_ppt_runner

    source = _resolve_run_dir(version) if version else _latest_run_dir()
    if source is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "報表版本不存在：請先於報表種類頁產製報表。"},
        )
    raw = source.read_bytes("report_data.json")
    if raw is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"版本 {source.name} 缺 report_data.json"},
        )
    try:
        builder = ai_report_ppt_runner._load_builder()
        theme = _json.loads(ai_report_ppt_runner.THEME_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 部署缺檔要給可行動的錯誤，不吐 500 追蹤
        return JSONResponse(
            status_code=503,
            content={"detail": f"PPT 版型資源不可用（部署環境缺 skill 檔案？）：{exc}"},
        )
    report_data = _json.loads(raw.decode("utf-8"))
    return {
        "version": source.name,
        "theme": theme,
        "pages": _pages_for_report_data(report_data, builder=builder),
        "kinds": sorted(builder.RENDERERS),
    }


@report_versions_router.get("/reports/versions")
def list_report_versions(limit: int | None = None):
    """列出所有報表版本（新到舊），供前端「最新展開＋舊版收合」的版本清單。

    效率：只讀版本名與 narratives 的存在性，不開任何 report_data.json——版本一多也不會慢
    （本機端只 stat；DB 端 list_versions 不選 content，has_narratives 由 SQL 聚合直接得出）。
    卡片數等需要開檔的資訊留給展開時的 content 端點取（lazy）。
    limit 只截清單長度，total 仍回實際總數供前端「顯示更多」。
    """
    sources = list(reversed(_list_run_sources()))  # 新到舊
    total = len(sources)
    if limit is not None and limit > 0:
        sources = sources[:limit]
    versions = [
        {
            "version": source.name,
            "generated_at": _version_generated_at(source.name),
            "is_latest": idx == 0,
            "has_narratives": _has_narratives(source),
        }
        for idx, source in enumerate(sources)
    ]
    return {"versions": versions, "total": total}


def _has_narratives(source) -> bool:
    """該版本是否已有 AI 解讀。

    DB 來源用 list_versions 已算好的旗標（不為一個布林值把 narratives.json 內容撈回來）；
    本機來源直接 stat 檔案存在性。
    """
    hint = getattr(source, "has_narratives_hint", None)
    if hint is not None:
        return hint
    return source.exists("narratives.json")


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


@report_versions_router.get("/reports/versions/{version}/ppt-files")
def list_report_version_ppt_files(version: str):
    """列某報表版本下已產的 .pptx 清單（#10：PPT 版本掛在報表版本下，_rN 不覆蓋）。

    沿 report_artifact_store.list_ppt_files（DB 來源，只回 metadata 不撈 content）；
    每筆補 download_url 指向既有 /report-latest/ppt/{version}/{filename} 下載端點，
    前端可直接用。版本無 PPT 時回空清單（非 404，讓前端顯示「尚無 PPT」）。
    """
    files = report_artifact_store.list_ppt_files(version)
    base = f"{settings.API_V1_PREFIX}/report-latest/ppt/{version}/"
    ppt_files = [
        {
            "filename": f["filename"],
            "byte_size": f["byte_size"],
            "download_url": base + f["filename"],
        }
        for f in files
    ]
    return {"version": version, "ppt_files": ppt_files}


# 副檔名 → Content-Type：DB 來源沒有檔案路徑可讓 FileResponse 推斷，需明確給。
_REPORT_ASSET_MEDIA_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".json": "application/json",
    ".html": "text/html; charset=utf-8",
}


@app.get("/api/v1/report-latest/asset/{version}/{filename}")
def serve_latest_report_asset(version: str, filename: str):
    """serve 指定報表版本的圖檔／附件；限白名單副檔名且不得逃出輸出根。

    本機有檔就直接 serve（本機開發／CLI 出圖的情境不變）；沒有才向
    app_layer.report_artifacts 取**單一檔案**（跨容器情境）——不為了一張圖撈整版產物。
    """
    from fastapi.responses import FileResponse, JSONResponse, Response

    from pathlib import PurePosixPath

    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in _REPORT_ASSET_SUFFIXES:
        return JSONResponse(status_code=404, content={"detail": "不支援的檔案類型"})
    root = REPORT_OUTPUT_ROOT.resolve()
    target = (root / version / filename).resolve()
    if target.is_relative_to(root) and target.is_file():
        return FileResponse(str(target))
    if _is_safe_version(version) and PurePosixPath(filename).name == filename:
        content = _db_read_artifact(version, filename)
        if content is not None:
            return Response(
                content=content,
                media_type=_REPORT_ASSET_MEDIA_TYPES.get(suffix, "application/octet-stream"),
            )
    return JSONResponse(status_code=404, content={"detail": "檔案不存在"})


# 報告 PPT 的 MIME（openxml presentation）；DB 來源無檔案路徑，需明確指定。
_PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


@app.get("/api/v1/report-latest/ppt/{version}/{filename}")
def download_report_ppt(version: str, filename: str):
    """下載某報表版本的 .pptx（deterministic、Web 直呼、不經 AI）。

    ai:report_ppt runner 把 build_ppt.py 組出的 .pptx 一起 upload 進 report_artifacts；
    本路由沿既有 read_file 單檔取回（不自造新表／新存取）。本機有檔就直接 serve，
    沒有才向 DB 取（跨容器補位，同 asset 端點）。只接 .pptx、版本名防穿越；不存在回 404。
    """
    from pathlib import PurePosixPath

    from fastapi.responses import FileResponse, JSONResponse, Response

    if PurePosixPath(filename).suffix.lower() != ".pptx":
        return JSONResponse(status_code=404, content={"detail": "不支援的檔案類型"})
    root = REPORT_OUTPUT_ROOT.resolve()
    target = (root / version / filename).resolve()
    if target.is_relative_to(root) and target.is_file():
        return FileResponse(str(target), media_type=_PPTX_MEDIA_TYPE, filename=filename)
    if _is_safe_version(version) and PurePosixPath(filename).name == filename:
        content = _db_read_artifact(version, filename)
        if content is not None:
            return Response(content=content, media_type=_PPTX_MEDIA_TYPE)
    return JSONResponse(status_code=404, content={"detail": "PPT 不存在"})


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
