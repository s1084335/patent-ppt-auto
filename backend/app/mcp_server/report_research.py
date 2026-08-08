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

護欄（PRT-012，2026-08-07 回寫版）：
- 只暴露唯讀工具，**不接受 SQL 字串**（typed 參數）
- 所有查詢綁 `snapshot_id`，跨版本的證據不得混用
- 列數上限並**明說截斷**（不得靜默給一半讓 CLI 以為是全部）
- CLI 的 MCP config 不含任何 DB credential
"""
from __future__ import annotations

import json
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
)

# 單次查詢列數上限：CLI 要的是證據不是資料傾印；超過就截斷並明說。
MAX_EVIDENCE_ROWS = 200

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
    _guard(report_key, snapshot_id, limit)
    loader = snapshot_loader or _default_snapshot_loader
    snapshot = loader(snapshot_id)  # type: ignore[operator]
    rows = _rows_of(snapshot, report_key)
    if filters:
        for column, values in filters.items():
            wanted = {str(v) for v in values}
            rows = [r for r in rows if str(r.get(column)) in wanted]
    total = len(rows)
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
