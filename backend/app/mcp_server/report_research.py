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

import hashlib
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable  # noqa: F401

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
SQL_MAX_ROWS = 2000
SQL_DEFAULT_ROWS = 500

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
    "lifecycle": "前十大申請人的權利存續狀態分布",
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


def _audit(tool: str, **fields: Any) -> None:
    entry = {"tool": tool, **fields}
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

# 查詢逾時（毫秒）：取證查詢再複雜也不該跑滿分鐘級，逾時比拖垮 DB 好。
_SQL_TIMEOUT_MS = 30000


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


def query_database(sql: str, limit: int = SQL_DEFAULT_ROWS) -> dict[str, Any]:
    """唯讀 SQL 取證：查專利、申請人、法律狀態等原始資料。

    ⚠ 其餘工具讀的是**報表快照**（引擎已彙總的 chart_rows）；只有這一支真的
    連資料庫。要回答「快照裡沒有的問題」（例如某公司在特定年份的個別案件、
    某件專利的完整同族）就用它。

    只接受單句 SELECT／WITH。連線由 server 端強制 read-only 交易與逾時，
    CLI 端拿不到任何 DB credential。回傳含 `truncated`，截斷會明說。
    """
    try:
        if limit > SQL_MAX_ROWS:
            raise ReportResearchError(f"limit 超過上限 {SQL_MAX_ROWS}")
        text = validate_sql(sql)
    except ReportResearchError as exc:
        _audit("query_database", snapshot_id=None, sql=str(sql)[:200],
               rows=0, truncated=False, error=str(exc))
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
        cur.execute(text)
        columns = [d.name for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit + 1)
        conn.rollback()   # 唯讀交易，不需要 commit
    truncated = len(rows) > limit
    rows = rows[:limit]
    _audit("query_database", snapshot_id=None, sql=text[:200],
           rows=len(rows), truncated=truncated, error=None)
    return {
        "columns": columns,
        "rows": [[_jsonable(v) for v in row] for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "evidence_ref": f"sql:{hashlib.sha256(text.encode()).hexdigest()[:12]}",
    }


def build_cli_mcp_config(server_url: str, auth_token: str) -> dict[str, Any]:
    """CLI 專用的隔離 MCP config——**只有唯讀 profile，且不含任何 DB credential**。

    ⚠ token 是 MCP server 的存取權杖，不是資料庫憑證；DB 連線只存在伺服器端。
    """
    return {
        "mcpServers": {
            "patent-report-research": {
                "type": "http",
                "url": server_url,
                "headers": {"Authorization": f"Bearer {auth_token}"},
            }
        }
    }
