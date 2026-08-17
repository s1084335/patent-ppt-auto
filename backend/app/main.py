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
    deck_exports,
    company_aliases,
    company_groups,
    comparison,
    events,
    imports,
    jobs,
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
app.include_router(company_groups.router, prefix=settings.API_V1_PREFIX)
# 公司中文名草稿確認：補上三態流程的「確認」環節（原本產得出草稿但無處確認）。
app.include_router(company_aliases.router, prefix=settings.API_V1_PREFIX)
app.include_router(deck_exports.router, prefix=settings.API_V1_PREFIX)

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

    ⚠ 存在性檢查與內容下載分離（2026-07-31 實機：content 端點 12 秒的根因）——
    舊版 `exists()` 直接走 `read_bytes()`，**把 30–100KB 的圖檔完整撈回來只為了
    回答「在不在」**，content 組裝逐變體問 20+ 次就疊出 12 秒。改為首次需要時
    一次撈整版檔名集合（只查 filename，一趟往返），exists 查集合。
    """

    #: 未提供 hint 的哨兵——None 是合法值（＝不歸屬任何 workspace），不能拿它當「沒帶」。
    _NO_HINT = object()

    def __init__(self, version: str, *, has_narratives: bool | None = None,
                 workspace_id=_NO_HINT):
        self.name = version
        # list_versions 已用 SQL 聚合算出有無解讀；帶進來讓列表端點不必為一個布林值撈內容。
        self.has_narratives_hint = has_narratives
        # 同理，workspace 歸屬也由同一趟聚合帶回（2026-08-17）——不逐版讀 meta 小檔。
        self.workspace_id_hint = workspace_id
        self._cache: dict[str, bytes | None] = {}
        self._filenames: set[str] | None = None

    def _names(self) -> set[str]:
        if self._filenames is None:
            self._filenames = _db_list_filenames(self.name)
        return self._filenames

    def read_bytes(self, filename: str):
        if filename not in self._cache:
            # 清單已知不存在的檔不再打 DB（一次清單查詢即涵蓋所有缺檔判斷）。
            if filename not in self._names():
                self._cache[filename] = None
            else:
                self._cache[filename] = _db_read_artifact(self.name, filename)
        return self._cache[filename]

    def exists(self, filename: str) -> bool:
        return filename in self._names()


def _db_list_filenames(version: str) -> set:
    """一次取回某版本的全部檔名（只查 filename，一趟往返）。

    供 _DbRunSource 的存在性檢查用——逐檔 exists 各打一次 DB 是 content 端點
    12 秒的元兇，這裡一次查完讓 20+ 次 exists 變成集合查找。
    """
    from backend.app.db import report_artifact_store

    return report_artifact_store.list_filenames(version)


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
                _DbRunSource(
                    entry["version"],
                    has_narratives=bool(entry.get("has_narratives")),
                    # ⚠ 用 `in` 判斷而非 `.get()`：workspace_id 為 None 是**有意義的值**
                    # （不歸屬任何 workspace），與「這批資料沒帶這個欄位」必須分得開。
                    workspace_id=(entry["workspace_id"] if "workspace_id" in entry
                                  else _DbRunSource._NO_HINT),
                ),
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


# ⚠ ppt_eligible_variant_keys 已隨 PPT 交付線移除（2026-08-10，remove-ppt-delivery-line）。


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

    from backend.app.reports.chart_runner import variant_narrative_ref

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

    from backend.app.reports.chart_profiles import resolve_web_asset

    parameters = report_data.get("parameters", {}) or {}
    asset_base = f"{settings.API_V1_PREFIX}/report-latest/asset/{version}/"
    sections_out = []
    for section in report_data.get("sections", []) or []:
        report_key = _section_report_key(section)
        # 🔴 section 自帶 rows＝顯示用轉置，優先於 reports 桶（2026-08-11，
        # 與 index.html 產表同一條機制；受理局交叉表靠它，不帶才回長格式）。
        rows = section.get("rows") or _lookup_rows(report_data, report_key)
        variants_out = []
        for variant in list(section.get("variants") or []) + list(section.get("more_variants") or []):
            file_name = str(variant.get("file", ""))
            variant_key = variant.get("variant_key", "default")
            # 🔴 解讀掛點**逐變體**解析，唯一來源＝chart_runner.variant_narrative_ref。
            # 產出時已寫進 report_data.json 的 narrative_key；舊版產出沒有這欄，
            # 現算一次（同一個函式，不是第二份規則）。
            # ⚠ 原本整張卡共用 `narratives.get(report_key)`，於是 `annual_trend` 與
            # 機會板兩個變體永遠查不到——PPT 端有 alias 接得起來、網頁端沒有，
            # 使用者看到的就是「AI 解讀尚未產生」（2026-08-03 實機）。
            # ⚠ 精確鍵優先於對照：narratives 真的有這個鍵就用它，對照是「查不到才要的橋」。
            ref = variant.get("narrative_key") or variant_narrative_ref(report_key, variant_key)
            candidates = [(report_key, variant_key), tuple(ref.rsplit(":", 1))]
            narrative = None
            if not narratives_expired:
                for narr_key, narr_variant in candidates:
                    entry = narratives.get(narr_key) or {}
                    narrative = (entry.get("variants") or {}).get(narr_variant)
                    if narrative is None and entry.get("text"):
                        narrative = {"text": entry["text"]}  # v1 相容：單一 text 當所有變體預設
                    if narrative is not None:
                        break
            variants_out.append({
                "label": variant.get("label", ""),
                "variant_key": variant_key,
                "file": file_name,
                # 網頁拿 web profile 的圖（P3）；舊版本沒有 `.web.svg` 時
                # resolve_web_asset 會退回原檔——不退回＝舊版本網頁全空。
                "chart_url": (asset_base + resolve_web_asset(file_name, run_dir.exists)
                              if run_dir.exists(file_name) else None),
                "narrative": narrative,
                "rows": variant.get("rows", []),
                "column_labels": _column_labels(variant.get("rows", [])),
                "thresholds": variant.get("thresholds", {}),
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


# ⚠ /reports/ppt-layout 與 _pages_for_report_data 已隨 PPT 交付線移除
# （2026-08-10，remove-ppt-delivery-line）。


def _version_workspace_id(source) -> int | None:
    """讀版本歸屬的 workspace_id（version_meta.json，~120B 小檔）。

    無 meta（舊版本）或無鍵＝不歸屬任何 workspace → 回 None。

    🔴 DB 來源優先用 `list_versions` 同一趟聚合帶回的 hint（2026-08-17）——
    逐版讀這個小檔要兩趟往返，48 版就是 96 趟、開頁 4.3 秒。同 `_has_narratives`
    的作法。本機目錄來源仍直接讀檔（本機 I/O，不是瓶頸）。
    """
    import json as _json

    hint = getattr(source, "workspace_id_hint", _DbRunSource._NO_HINT)
    if hint is not _DbRunSource._NO_HINT:
        return hint
    raw = source.read_bytes("version_meta.json")
    if raw is None:
        return None
    try:
        value = _json.loads(raw.decode("utf-8")).get("workspace_id")
        return int(value) if value is not None else None
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


@report_versions_router.get("/reports/versions")
def list_report_versions(limit: int | None = None, workspace_id: int | None = None):
    """列出報表版本（新到舊），供前端「最新展開＋舊版收合」的版本清單。

    workspace_id 給定時只回該 workspace 產的版本（2026-07-31 定案：版本與 PPT
    依 workspace 區隔）；⚠ 無 version_meta.json 的舊版本不歸屬任何 workspace，
    帶過濾時一律不顯示——「沒產過就不要顯示」，舊版本重產即可。
    不帶參數維持回全部（CLI 與既有呼叫相容）。

    效率：過濾只讀 ~120B 的 meta 小檔，不開 report_data.json；未過濾時完全不開檔。
    limit 只截清單長度，total 回過濾後總數供前端「顯示更多」。
    """
    sources = list(reversed(_list_run_sources()))  # 新到舊
    if workspace_id is not None:
        sources = [s for s in sources if _version_workspace_id(s) == workspace_id]
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


# ⚠ /reports/versions/{version}/ppt-files 已隨 PPT 交付線移除
# （2026-08-10，remove-ppt-delivery-line）。


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


# ⚠ /report-latest/ppt/{version}/{filename} 已隨 PPT 交付線移除
# （2026-08-10，remove-ppt-delivery-line）。HTML 與圖檔仍走 asset 端點。


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
