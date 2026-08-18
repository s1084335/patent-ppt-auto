"""report-research 唯讀 MCP profile（P2 第 3 節）。

🔴 **本模組的最大目標**（2026-08-07 使用者校正）：讓系統的 Claude CLI
**能去資料庫找證據來寫簡報**。工具白名單與憑證隔離是配套護欄，不是重點——
先確保 CLI 查得到、查得準，護欄才有意義。

CLI 的典型使用序：
1. `list_report_catalog()` 看有哪些報表、各自回答什麼問題
2. `query_report_evidence(report_key=…, filters=…)` 取可直接引用的數據列
3. `lookup_company_evidence(…)` / `lookup_topic_evidence(…)` / `lookup_patent_evidence(…)`
   做具名查證（寫「扭矩是全球化布局者」「孟喬僅具前案價值」時要點得出依據）
4. 每筆回傳都帶 `evidence_ref`，直接放進 SlidePlan 的 narrative

⚠ 前七支讀的是**報表快照**（`report_data.json`），不是資料庫。要回答快照裡
沒有的問題（個別案件、完整同族、任意交叉統計）用 `query_database`——它是唯一
真的連 DB 的工具（2026-08-09 補上；在那之前「CLI 去資料庫找證據」並不成立，
因為工具全都在讀引擎已彙總的 chart_rows）。

護欄（PRT-012，2026-08-09 回寫版）：
- 只暴露唯讀工具；快照型工具用 typed 參數，`query_database` 收 SQL 但限單句
  SELECT／WITH，且連線層強制 `default_transaction_read_only` 與逾時
- 快照型查詢綁 `snapshot_id`，跨版本的證據不得混用
- 列數上限並**明說截斷**（不得靜默給一半讓 CLI 以為是全部）
- CLI 的 MCP config 不含任何 DB credential
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import re
from collections.abc import Callable  # noqa: F401
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.app.reports.report_definitions import REPORT_DEFINITIONS

# 唯讀工具白名單（allowlist contract test 盯著；新增工具會讓測試紅，強制複審）。
TOOL_NAMES: tuple[str, ...] = (
    "list_report_catalog",
    "preview_report_rows",
    "query_report_evidence",
    "get_chart_metadata",
    "lookup_company_evidence",
    "lookup_topic_evidence",
    "lookup_patent_evidence",
    "query_database",
)

# 單次查詢列數上限：CLI 要的是證據不是資料傾印；超過就截斷並明說。
MAX_EVIDENCE_ROWS = 200

# SQL 取證的列數上限與預設值。
# ⚠ 刻意**對齊原查詢閘道**（query_patents.py 預設 500／最高 2000）：取證通道
# 從 Bash 閘道換成 MCP 是換路，不是縮權——沿用 200 會讓需要逐案清單的查詢
# （例如列出某公司全部案件）被靜默截斷，那是換通道造成的能力退步。
# 🔴 2026-08-12 使用者裁決再放寬：**列數硬上限（原 2000）移除**——取證要拿
# 多少列是 CLI 的判斷，權限牆退場；效能保護改「保險絲」二件套：
# 逾時（_SQL_TIMEOUT_MS）＋回應量上限（SQL_PAYLOAD_FUSE_BYTES），
# 超過都**明示截斷**、不擋查詢也不靜默。
SQL_DEFAULT_ROWS = 500          # 預設分頁（呼叫端沒說要多少時的合理一頁）
SQL_PAYLOAD_FUSE_BYTES = 5_000_000   # 回應量保險絲：5MB——防的是效能，不是權限

# ⚠ snapshot_loader 註記為 object 而非 Callable：FastMCP 以 pydantic 產 JSON schema，
# Callable 無法序列化會讓整個 server 註冊失敗（2026-08-07 實測）。它是測試注入點，
# 正式路徑一律走 _default_snapshot_loader。

# 報表語意：CLI 靠它判斷「想回答這個問題該查哪張」。
# ⚠ 只描述用途，欄位與定義仍以 REPORT_DEFINITIONS 為準（不複製第二份清單）。
_REPORT_ANSWERS: dict[str, str] = {
    "application_trend": "每年申請幾件、哪年是高峰、是真爆發還是同族延伸",
    "publication_trend": "每年獲得授權公告幾件",
    "country_distribution": "各受理局申請幾件、其中還有多少現存有效",
    "applicant_ranking": "誰申請最多、有無共同申請與轉讓",
    "applicant_country_distribution": "哪家公司在哪些國家布局",
    "applicant_year_matrix": "哪家公司在哪幾年活躍",
    "applicant_strength_profile": "每家的布局形狀：件／族／國／技術廣度／法律狀態／種類",
    # ⚠ 2026-08-09 移除 `lifecycle`：該報表已由使用者裁決刪除，法律狀態改由
    # `country_distribution` 承接。留著等於在目錄語意表裡宣告一張不存在的報表。
    "ipc_main_distribution": "技術分類集中在哪些 IPC",
    "cpc_main_distribution": "CPC 有而 IPC 沒有的分類是什麼",
    "cluster_topic_table": "有哪些技術／功效主題、各幾件幾家",
    "opportunity_quadrant": "哪些主題是多方投入、哪些是空白區",
    "family_country_layout": "同族合併後各國還有多少存活家族",
}


class ReportResearchError(RuntimeError):
    """查詢不合契約（未知報表、缺 snapshot、逾越上限、傳 SQL 字串等）。"""


# ── 取證稽核（A7，2026-08-09）─────────────────────────────────────
# 🔴 動因：使用者問「你要怎知道 CLI 有沒有去資料庫找證據」，當時只能事後翻
# CLI transcript（開發機上的 jsonl）數 tool_use——正式部署根本沒有那個檔案。
# 取證通道自己要記：誰查的、查了什麼、回幾列、有沒有截斷、有沒有失敗。
#
# ⚠ audit 是**觀測不是防護**：它不阻擋任何查詢，只讓「查了沒有」從推論變成
# 事實。防護在別處（唯讀連線、單句 SELECT、工具白名單）。
# ⚠ 只記查詢不記資料：回傳的專利內容不進紀錄，稽核不該變成資料副本。
_QUERY_AUDIT: list[dict[str, Any]] = []


def reset_query_audit() -> None:
    """清空稽核紀錄（每個任務開始時呼叫，讓紀錄對得上單次規劃）。"""
    _QUERY_AUDIT.clear()


def get_query_audit() -> list[dict[str, Any]]:
    """回傳本次累積的取證紀錄（複本，呼叫端改不到內部狀態）。"""
    return [dict(entry) for entry in _QUERY_AUDIT]


#: 稽核落檔路徑的環境變數。
#: 🔴 MCP server 是 CLI 的**子行程**，runner 在 worker 行程——只記在記憶體
#: runner 一筆也拿不到。runner 起 CLI 前設這個變數，server 子行程繼承後逐筆
#: 寫 JSONL，任務結束再讀回。
#: ⚠ 未設就只留記憶體：不該因為有人 import 這個模組就在檔案系統留下東西。
AUDIT_PATH_ENV = "PATENT_QUERY_AUDIT_PATH"
NARRATIVE_WORKSPACE_ID_ENV = "PATENT_REPORT_WORKSPACE_ID"
NARRATIVE_SNAPSHOT_ID_ENV = "PATENT_REPORT_SNAPSHOT_ID"


@contextlib.contextmanager
def narrative_report_scope(
    *,
    workspace_id: int | None = None,
    snapshot_id: str | None = None,
):
    """鎖定 narrative CLI 本輪可查詢的 workspace / snapshot 範圍。"""
    previous_workspace = os.environ.get(NARRATIVE_WORKSPACE_ID_ENV)
    previous_snapshot = os.environ.get(NARRATIVE_SNAPSHOT_ID_ENV)
    if workspace_id is None:
        os.environ.pop(NARRATIVE_WORKSPACE_ID_ENV, None)
    else:
        os.environ[NARRATIVE_WORKSPACE_ID_ENV] = str(int(workspace_id))
    if snapshot_id is None:
        os.environ.pop(NARRATIVE_SNAPSHOT_ID_ENV, None)
    else:
        os.environ[NARRATIVE_SNAPSHOT_ID_ENV] = str(snapshot_id)
    try:
        yield
    finally:
        if previous_workspace is None:
            os.environ.pop(NARRATIVE_WORKSPACE_ID_ENV, None)
        else:
            os.environ[NARRATIVE_WORKSPACE_ID_ENV] = previous_workspace
        if previous_snapshot is None:
            os.environ.pop(NARRATIVE_SNAPSHOT_ID_ENV, None)
        else:
            os.environ[NARRATIVE_SNAPSHOT_ID_ENV] = previous_snapshot


def active_narrative_workspace_id() -> int | None:
    """讀取目前 narrative MCP scope；沒有 scope 時維持一般唯讀查詢行為。"""
    value = os.environ.get(NARRATIVE_WORKSPACE_ID_ENV)
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ReportResearchError("narrative workspace scope is invalid") from exc


@contextlib.contextmanager
def query_audit_file():
    """為一次 AI 任務開稽核落檔，並讓 MCP server 子行程看得到路徑。

    ⚠ 用暫存檔而不是固定路徑：多個任務可能並行，共用一個檔會互相污染。
    ⚠ 落點在此（`AUDIT_PATH_ENV` 的定義處）而非各 runner：解讀線與規劃線都要用，
    複製第二份會讓兩條線的稽核格式各自演進，而不一致本身不會報錯。
    """
    # ⚠ 不用 with：這個檔要活到 CLI 子行程寫完才讀，由 finally 負責刪除。
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
        prefix="query_audit_", suffix=".jsonl", delete=False)
    handle.close()
    path = Path(handle.name)
    previous = os.environ.get(AUDIT_PATH_ENV)
    os.environ[AUDIT_PATH_ENV] = str(path)
    try:
        yield path
    finally:
        if previous is None:
            os.environ.pop(AUDIT_PATH_ENV, None)
        else:
            os.environ[AUDIT_PATH_ENV] = previous
        path.unlink(missing_ok=True)


#: workspace 取證範圍的環境變數（2026-08-14 使用者裁決「加 workspace 參數過濾」）。
#: 通道與 AUDIT_PATH_ENV 同一條：runner 起 CLI 前設定，MCP server 子行程繼承。
#: ⚠ 防的是**正確性**不是安全——同一申請人出現在兩個 workspace 時，CLI 可能
#: 引到別包的專利且不會報錯；惡意繞過不在威脅模型（CLI 吃我們自己的 prompt）。
SCOPE_WORKSPACE_ENV = "PATENT_RESEARCH_WORKSPACE_ID"

#: patent 級資料表：scope 生效時查它們必須 join workspace_scope。
#: ⚠ 判準是「一列＝一件專利」的表；彙總表（company_aliases、workspaces）不在列。
_PATENT_SCOPED_TABLES = ("patents", "patent_attributes")
_SCOPED_TABLE_RE = re.compile(
    r"\b(" + "|".join(_PATENT_SCOPED_TABLES) + r")\b", re.IGNORECASE)


def _scope_workspace_id() -> int | None:
    """目前生效的 workspace scope；未設／壞值＝不啟用（壞值不該讓查詢掛掉）。"""
    raw = os.environ.get(SCOPE_WORKSPACE_ENV, "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


@contextlib.contextmanager
def workspace_scope_env(workspace_id: int | None):
    """為一次 AI 任務綁定取證範圍（None＝不綁，例如素材沒有 workspace）。

    ⚠ 落點在此（SCOPE_WORKSPACE_ENV 的定義處）而非各 runner——與
    query_audit_file 同理：deck 與 narrative 線都要用，複製第二份會漂移。
    """
    if workspace_id is None:
        yield
        return
    previous = os.environ.get(SCOPE_WORKSPACE_ENV)
    os.environ[SCOPE_WORKSPACE_ENV] = str(int(workspace_id))
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(SCOPE_WORKSPACE_ENV, None)
        else:
            os.environ[SCOPE_WORKSPACE_ENV] = previous


def _apply_workspace_scope(text: str, patent_ids: list[int]) -> str:
    """把 workspace 成員注入查詢（CTE）＋ join 閘門。

    閘門：查 patent 級資料表卻沒引用 workspace_scope → 拒絕。
    ⚠ 錯誤訊息就是使用說明——CLI 收到後知道怎麼改，偏差是「多出來的」
    （查詢被拒、可見），不是缺席（靜默查到別包）。
    """
    if not patent_ids:
        raise ReportResearchError(
            "workspace 取證範圍是空的（該 workspace 沒有成員專利）；"
            "scoped 查詢無意義，不得靜默退回全庫")
    if _SCOPED_TABLE_RE.search(text) and "workspace_scope" not in text.lower():
        raise ReportResearchError(
            "本任務已綁定 workspace 取證範圍：查 "
            + "／".join(_PATENT_SCOPED_TABLES)
            + " 時必須 JOIN workspace_scope（欄位 patent_id）過濾，例如 "
              "SELECT p.* FROM patents p JOIN workspace_scope s "
              "ON s.patent_id = p.patent_id WHERE …——"
              "workspace_scope 由系統注入，直接引用即可")
    values = ", ".join(f"({int(i)})" for i in patent_ids)
    cte = f"workspace_scope(patent_id) AS (VALUES {values})"
    if re.match(r"^WITH\s+RECURSIVE\b", text, re.IGNORECASE):
        return re.sub(r"^WITH\s+RECURSIVE\b", f"WITH RECURSIVE {cte},",
                      text, count=1, flags=re.IGNORECASE)
    if re.match(r"^WITH\b", text, re.IGNORECASE):
        return re.sub(r"^WITH\b", f"WITH {cte},", text, count=1, flags=re.IGNORECASE)
    return f"WITH {cte} {text}"


def _fetch_workspace_patent_ids(cur, workspace_id: int) -> list[int]:
    """workspace 成員（唯一來源＝app_layer.workspaces.patent_ids_json）。"""
    cur.execute(
        "SELECT patent_ids_json FROM app_layer.workspaces WHERE workspace_id = %s",
        (workspace_id,))
    row = cur.fetchone()
    if row is None:
        raise ReportResearchError(f"workspace {workspace_id} 不存在，無法綁定取證範圍")
    return [int(i) for i in (row[0] or [])]


def read_query_audit(path: Path) -> list[dict[str, Any]]:
    """讀回稽核 JSONL。⚠ 讀不到就回空清單——稽核缺失不得讓任務失敗。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def rows_fingerprint(rows: list[dict[str, Any]]) -> str:
    """查詢結果的指紋（2026-08-10）：讓「資料有沒有變」可查，而不必把結果存一遍。

    ⚠ 為什麼不存結果：typed 工具查的是 snapshot，同一個 snapshot_id 重跑必得同樣
    結果——存下來只是把 report_data 複製一份。只有 `query_database` 查的是即時 DB
    才有「當時查到什麼」的問題，而那用**指紋**就足夠：日後重跑若 hash 相同即證明
    資料未變、結論仍成立；不同則明確知道要重新檢視。

    指紋大小固定，不隨結果列數膨脹——否則就變回「把結果存一遍」了。
    欄位順序不影響結果（sort_keys）：欄序不同不算資料不同。
    """
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _audit(tool: str, **fields: Any) -> None:
    """記一筆取證紀錄。

    ⚠ 只記有資訊量的欄位（2026-08-10 使用者定案「紀錄的欄位盡量精簡」）：
    值為 None 的一律不寫——`snapshot_id=None`（非快照工具）、`error=None`（成功）
    這類預設值佔位沒有意義，只讓每筆紀錄變胖。
    ⚠ 也不記時間：稽核要回答的是「查了什麼」，時間由 job 本身的紀錄承擔。
    ⚠ 不記查詢結果：typed 工具的結果可由 snapshot 重現，`query_database` 則以
    完整 SQL ＋ `row_hash` 重現與驗證——稽核不該變成資料副本。
    """
    entry = {"tool": tool, **{k: v for k, v in fields.items() if v is not None}}
    _QUERY_AUDIT.append(entry)
    target = os.environ.get(AUDIT_PATH_ENV)
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # ⚠ 稽核寫不進去不得讓取證失敗——它是觀測，不是查詢的前置條件。
        pass


def _default_snapshot_loader(snapshot_id: str) -> dict[str, Any]:
    """正式路徑：讀該報表版本的 report_data.json（伺服器端檔案，CLI 碰不到）。"""
    from backend.app.reports.chart_runner import DEFAULT_OUTPUT_DIR

    path = Path(DEFAULT_OUTPUT_DIR) / snapshot_id / "report_data.json"
    if not path.exists():
        raise ReportResearchError(f"snapshot {snapshot_id!r} 不存在或尚未產出報表資料")
    return json.loads(path.read_text(encoding="utf-8"))


def _guard(report_key: str, snapshot_id: str, limit: int) -> None:
    if not str(snapshot_id or "").strip():
        raise ReportResearchError("缺 snapshot_id——證據必須綁定資料快照，跨版本不得混用")
    if limit > MAX_EVIDENCE_ROWS:
        raise ReportResearchError(f"limit 超過上限 {MAX_EVIDENCE_ROWS}")
    if report_key not in REPORT_DEFINITIONS:
        # SQL 字串會落在這裡（不是合法 report_key）——typed 參數，不吃 SQL。
        raise ReportResearchError(
            f"未知 report_key {report_key!r}；本工具只接受報表鍵，不接受 SQL")


def list_report_catalog() -> list[dict[str, Any]]:
    """報表目錄：名稱、中文標題、型別與**這張報表回答什麼問題**。"""
    _audit("list_report_catalog", snapshot_id=None, rows=len(REPORT_DEFINITIONS),
           truncated=False, error=None)
    return [
        {
            "name": name,
            "label_zh": definition.label_zh,
            "report_type": definition.report_type,
            "answers": _REPORT_ANSWERS.get(name, definition.label_zh),
        }
        for name, definition in sorted(REPORT_DEFINITIONS.items())
    ]


def _rows_of(snapshot: dict[str, Any], report_key: str) -> list[dict[str, Any]]:
    rows = (snapshot.get("chart_rows") or {}).get(report_key)
    if rows:
        return rows
    for bucket in ("reports", "family_reports"):
        entry = (snapshot.get(bucket) or {}).get(report_key) or {}
        if entry.get("rows"):
            return entry["rows"]
    return []


def query_report_evidence(
    report_key: str,
    snapshot_id: str,
    filters: dict[str, list[Any]] | None = None,
    limit: int = MAX_EVIDENCE_ROWS,
    snapshot_loader: object = None,
) -> dict[str, Any]:
    """取某張報表的數據列供敘述引用；可用 typed filters 篩具名對象。

    回傳帶 `evidence_ref`（供 SlidePlan narrative 引用）、`population_note`
    （母體口徑）與 `truncated`（截斷要明說）。
    """
    try:
        _guard(report_key, snapshot_id, limit)
    except ReportResearchError as exc:
        _audit("query_report_evidence", snapshot_id=snapshot_id, report_key=report_key,
               rows=0, truncated=False, error=str(exc))
        raise
    loader = snapshot_loader or _default_snapshot_loader
    snapshot = loader(snapshot_id)  # type: ignore[operator]
    rows = _rows_of(snapshot, report_key)
    if filters:
        for column, values in filters.items():
            wanted = {str(v) for v in values}
            rows = [r for r in rows if str(r.get(column)) in wanted]
    total = len(rows)
    _audit("query_report_evidence", snapshot_id=snapshot_id, report_key=report_key,
           rows=min(total, limit), truncated=total > limit, error=None)
    return {
        "report_key": report_key,
        "snapshot_id": snapshot_id,
        "rows": rows[:limit],
        "total": total,
        "truncated": total > limit,
        "population_note": (snapshot.get("population") or {}).get(report_key, ""),
        "evidence_ref": f"{snapshot_id}:{report_key}",
    }


def preview_report_rows(
    report_key: str,
    snapshot_id: str,
    limit: int = 5,
    snapshot_loader: object = None,
) -> dict[str, Any]:
    """先看幾列與欄位長相，再決定要不要細查（省 CLI 的 token）。"""
    result = query_report_evidence(report_key, snapshot_id, limit=limit,
                                   snapshot_loader=snapshot_loader)
    result["columns"] = sorted(result["rows"][0]) if result["rows"] else []
    # ⚠ 內部那次 query 已自行留痕，這裡再記一筆是刻意的：稽核要看得出
    # 「CLI 呼叫的是 preview」還是「直接 query」——兩者的意圖不同。
    _audit("preview_report_rows", snapshot_id=snapshot_id, report_key=report_key,
           rows=len(result["rows"]), truncated=result["truncated"], error=None)
    return result


def get_chart_metadata(
    report_key: str,
    snapshot_id: str,
    snapshot_loader: object = None,
) -> dict[str, Any]:
    """某張圖的視覺編碼與母體說明（CLI 寫判讀前要知道圖在畫什麼）。"""
    _guard(report_key, snapshot_id, 1)
    loader = snapshot_loader or _default_snapshot_loader
    snapshot = loader(snapshot_id)  # type: ignore[operator]
    encoding = (snapshot.get("chart_encoding") or {}).get(report_key, "")
    section = next((s for s in snapshot.get("sections") or []
                    if s.get("report_key") == report_key), {})
    return {
        "report_key": report_key,
        "snapshot_id": snapshot_id,
        "title": section.get("title", ""),
        "encoding_note": encoding,
        "note": section.get("note", ""),
        "variants": [v.get("variant_key") for v in section.get("variants") or []],
    }


def lookup_company_evidence(
    applicant: str,
    snapshot_id: str,
    snapshot_loader: object = None,
) -> dict[str, Any]:
    """單一公司的具名證據（四面向優先，退回排名列）。

    寫「扭矩 4 件 1 家族 4 國＝技術面窄、地域防禦廣」這種句子時，數字從這裡來。
    """
    loader = snapshot_loader or _default_snapshot_loader
    snapshot = loader(snapshot_id)  # type: ignore[operator]
    for key in ("applicant_strength_profile", "applicant_ranking"):
        for row in _rows_of(snapshot, key):
            if str(row.get("applicant_display_name")) == applicant:
                return {"applicant": applicant, "snapshot_id": snapshot_id,
                        "source_report": key,
                        "evidence_ref": f"{snapshot_id}:{key}:{applicant}", **row}
    raise ReportResearchError(f"snapshot {snapshot_id} 查無申請人 {applicant!r}")


def lookup_topic_evidence(
    topic_key: str,
    snapshot_id: str,
    snapshot_loader: object = None,
) -> dict[str, Any]:
    """單一主題的具名證據（件數、家數、代表專利）。"""
    loader = snapshot_loader or _default_snapshot_loader
    snapshot = loader(snapshot_id)  # type: ignore[operator]
    for row in _rows_of(snapshot, "cluster_topic_table"):
        if str(row.get("topic_code")) == topic_key or str(row.get("topic_key")) == topic_key:
            return {"topic_key": topic_key, "snapshot_id": snapshot_id,
                    "evidence_ref": f"{snapshot_id}:cluster_topic_table:{topic_key}", **row}
    raise ReportResearchError(f"snapshot {snapshot_id} 查無主題 {topic_key!r}")


def lookup_patent_evidence(
    patent_ids: list[int],
    snapshot_id: str,
    snapshot_loader: object = None,
) -> dict[str, Any]:
    """專利號級證據：簡報要點名某幾件時用（回專利號與標題）。"""
    loader = snapshot_loader or _default_snapshot_loader
    snapshot = loader(snapshot_id)  # type: ignore[operator]
    wanted = {int(p) for p in patent_ids}
    patents = [p for p in (snapshot.get("patents") or [])
               if int(p.get("patent_id", -1)) in wanted]
    return {"snapshot_id": snapshot_id, "patents": patents,
            "evidence_ref": f"{snapshot_id}:patents:{sorted(wanted)}"}


# 寫入類關鍵字（word boundary，`created_at` 不會誤中 CREATE）。
# ⚠ 這只是**提前報錯讓訊息好懂**；真正的防線是連線層 default_transaction_read_only。
_FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|VACUUM|CALL|DO)\b",
    re.IGNORECASE,
)
_SCOPED_SQL_AGGREGATE = re.compile(
    r"\b(COUNT|SUM|AVG|MIN|MAX|STRING_AGG|ARRAY_AGG|JSONB_AGG|JSON_AGG)\s*\(|\bGROUP\s+BY\b|\bOVER\s*\(",
    re.IGNORECASE,
)

# 查詢逾時（毫秒）：防拖垮 DB 的保險絲。
# 🔴 2026-08-12 使用者裁決放寬 30s→120s：複雜 JOIN 取證不該被砍在半路，
# 但仍留底線——真跑超過兩分鐘多半是查詢寫壞了，不是取證需要。
_SQL_TIMEOUT_MS = 120000


def _collect_rows(fetch, limit: int | None, fuse_bytes: int) -> tuple[list, bool]:
    """收集查詢結果，直到取盡／到 limit／觸發回應量保險絲。

    回 (rows, truncated)。⚠ 保險絲不是擋門：已收的照回傳、truncated 明示，
    CLI 看到旗標可自行縮小查詢範圍再查——與逾時同屬效能保護，非權限。
    大小以逐列 JSON 序列化長度估算（與實際回應同一量級即可，不求位元組精確）。
    """
    rows: list = []
    size = 0
    batch_size = 500
    while True:
        want = batch_size if limit is None else min(batch_size, limit + 1 - len(rows))
        batch = fetch(want)
        if not batch:
            return rows, False
        for row in batch:
            if limit is not None and len(rows) >= limit:
                return rows, True      # 多取到的那列＝後面還有資料
            size += len(json.dumps(row, ensure_ascii=False, default=str))
            if size > fuse_bytes:
                return rows, True      # 觸絲：已收的照回傳，明示截斷
            rows.append(row)


def _jsonable(value: Any) -> Any:
    """DB 值轉可序列化：日期轉 ISO、Decimal 轉 float、bytes 只回長度。

    ⚠ bytes（主附圖 bytea）不進輸出——圖片對取證毫無用處，卻能單筆撐爆 context。
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, memoryview)):
        return f"<bytes:{len(value)}>"
    return value


def validate_sql(sql: str) -> str:
    """單句、唯讀語法的提前檢查；回傳去除尾端分號的 SQL。"""
    text = str(sql or "").strip()
    if not text:
        raise ReportResearchError("SQL 是空的")
    text = text.rstrip(";").strip()
    if ";" in text:
        raise ReportResearchError("只接受單一語句（偵測到分號串接）")
    if not re.match(r"^(SELECT|WITH)\b", text, re.IGNORECASE):
        raise ReportResearchError("只接受 SELECT／WITH 開頭的唯讀查詢")
    hit = _FORBIDDEN_SQL.search(text)
    if hit:
        raise ReportResearchError(
            f"查詢含寫入類關鍵字 {hit.group(0)!r}——本工具唯讀；"
            "若只是字串字面值撞名，請改寫避開")
    return text


def validate_scoped_narrative_sql(sql: str) -> str:
    """限制 scoped narrative 只能查 row-level patent evidence，避免全庫彙總外洩。"""
    text = validate_sql(sql)
    if _SCOPED_SQL_AGGREGATE.search(text):
        raise ReportResearchError(
            "scoped narrative query_database only allows row-level patent evidence; "
            "use snapshot report evidence for aggregate claims"
        )
    if not re.search(r"\b(patent_id|id)\b", text, re.IGNORECASE):
        raise ReportResearchError(
            "scoped narrative query_database must return patent_id or id for workspace filtering"
        )
    return text


def _workspace_patent_ids(cur, workspace_id: int) -> set[int]:
    """讀取 workspace 允許的 patent ids；資料庫仍在同一個 read-only transaction。"""
    cur.execute(
        """
        SELECT COALESCE(array_agg((item.value)::int), ARRAY[]::int[])
        FROM app_layer.workspaces AS w
        CROSS JOIN LATERAL jsonb_array_elements_text(
            COALESCE(w.patent_ids_json, '[]'::jsonb)
        ) AS item(value)
        WHERE w.workspace_id = %s
        """,
        (workspace_id,),
    )
    row = cur.fetchone()
    return {int(value) for value in (row[0] if row else [])}


def _filter_rows_to_workspace(
    columns: list[str],
    rows: list,
    allowed_patent_ids: set[int],
) -> list:
    """依查詢結果中的 patent_id/id 欄位做 workspace 過濾。"""
    lookup = {name.lower(): index for index, name in enumerate(columns)}
    column_index = lookup.get("patent_id", lookup.get("id"))
    if column_index is None:
        raise ReportResearchError(
            "scoped narrative query_database result must include patent_id or id"
        )
    filtered = []
    for row in rows:
        try:
            patent_id = int(row[column_index])
        except (TypeError, ValueError):
            continue
        if patent_id in allowed_patent_ids:
            filtered.append(row)
    return filtered


def query_database(sql: str, limit: int | None = SQL_DEFAULT_ROWS) -> dict[str, Any]:
    """唯讀 SQL 取證：查專利、申請人、法律狀態等原始資料。

    ⚠ 其餘工具讀的是**報表快照**（引擎已彙總的 chart_rows）；只有這一支真的
    連資料庫。要回答「快照裡沒有的問題」（例如某公司在特定年份的個別案件、
    某件專利的完整同族）就用它。

    只接受單句 SELECT／WITH。連線由 server 端強制 read-only 交易與逾時，
    CLI 端拿不到任何 DB credential。回傳含 `truncated`，截斷會明說。
    列數無硬上限（2026-08-12 使用者裁決）；limit 是呼叫端自選的分頁，
    傳 None＝要全部；效能保護＝逾時＋回應量保險絲（觸發即明示截斷）。
    """
    workspace_id = active_narrative_workspace_id()
    snapshot_id = os.environ.get(NARRATIVE_SNAPSHOT_ID_ENV)
    try:
        text = validate_scoped_narrative_sql(sql) if workspace_id is not None else validate_sql(sql)
    except ReportResearchError as exc:
        _audit("query_database", snapshot_id=snapshot_id, workspace_id=workspace_id,
               sql=str(sql), rows=0, truncated=False, error=str(exc), row_hash=None)
        raise

    import psycopg

    from backend.app.db.connection import get_database_url

    # 🔴 2026-08-09（A6 實測）：唯讀**不能**靠連線字串的 startup options。
    # 本專案的 DSN 走 Supabase transaction pooler（6543），它會**忽略** `-c`
    # 參數——實測 UPDATE／CREATE／DELETE 全部成功、statement_timeout 也沒作用。
    # 也就是說「連線層強制唯讀」在那之前只是註解，實際只有語法前置檢查一道。
    #
    # 改為綁在**交易**上：`SET TRANSACTION READ ONLY` 與 `SET LOCAL` 由後端
    # 在該筆交易內強制，pooler 換後端連線也帶得過去。
    with psycopg.connect(get_database_url()) as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(f"SET LOCAL statement_timeout = {_SQL_TIMEOUT_MS}")
        # 🔴 2026-08-18 合併 deck 線與主線時發現：**兩條線各自實作了 workspace 隔離**，
        #    讀的還是不同的環境變數——
        #      · deck：`PATENT_RESEARCH_WORKSPACE_ID` → 改寫 SQL（成員 CTE ＋ join 閘門），
        #        在**彙總之前**就把範圍限住，且查 patent 級表卻沒引用 scope 會被拒絕
        #      · 主線：`PATENT_REPORT_WORKSPACE_ID` → 執行後**過濾回傳列**
        #    ⚠ 兩者不是等價的：post-filter 擋不住 `SELECT count(*)` 這類彙總——
        #      數字會用全庫算完再濾列，結果是錯的但看起來很正常。
        #    合併時**兩層都保留**（誰的環境變數有設誰就生效），不弱化任何一邊；
        #    但「這個任務綁哪個 workspace」有兩個來源是同一份知識的兩個定義處，
        #    待收斂成一個（見 work-log 2026-08-18）。
        scope_ws = _scope_workspace_id()
        if scope_ws is not None:
            try:
                text = _apply_workspace_scope(
                    text, _fetch_workspace_patent_ids(cur, scope_ws))
            except ReportResearchError as exc:
                _audit("query_database", snapshot_id=None, sql=text,
                       rows=0, truncated=False, error=str(exc), row_hash=None)
                raise
        allowed_patent_ids = _workspace_patent_ids(cur, workspace_id) if workspace_id is not None else None
        cur.execute(text)
        columns = [d.name for d in cur.description] if cur.description else []
        rows, truncated = _collect_rows(cur.fetchmany, limit, SQL_PAYLOAD_FUSE_BYTES)
        if allowed_patent_ids is not None:
            rows = _filter_rows_to_workspace(columns, rows, allowed_patent_ids)
        conn.rollback()   # 唯讀交易，不需要 commit
    _audit("query_database", snapshot_id=snapshot_id, workspace_id=workspace_id, sql=text,
           rows=len(rows), truncated=truncated, error=None,
           row_hash=rows_fingerprint(rows))
    return {
        "columns": columns,
        "rows": [[_jsonable(v) for v in row] for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "evidence_ref": f"sql:{hashlib.sha256(text.encode()).hexdigest()[:12]}",
    }


# ⚠ 2026-08-13 刪除 build_cli_mcp_config（http 版 CLI MCP config）：
# 產品實際發給 CLI 的是 `cli_gateway.build_stdio_mcp_config()`（Companion 與 CLI
# 同機，走 stdio 免 token、免開埠），這支 http 版全庫只有測試在用。
# 🔴 更關鍵：它宣告的 server 名是 `patent-report-research`（連字號），而白名單前綴
# 是 `mcp__patent_research__*`（底線）——兩者對不上，誰哪天改用它，MCP 工具會
# **靜默**全部不可用（CLI 只會當作沒有那些工具，照樣產出看似合理的內容）。
# 憑證隔離與「只掛唯讀 profile」兩個判準已移到 test_report_research_profile
# 的 CredentialIsolationTests，改驗實際走的那條路徑。
# 日後若要恢復 http 通道，server 名必須由 `cli_gateway.MCP_SERVER_NAME` 同一常數推導。
