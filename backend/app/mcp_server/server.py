"""Central Patent MCP Server — FastMCP 綁定與傳輸層。

用法（stdio，Claude Code 以子行程啟動；v1 預設）：
    uv run python -m backend.app.mcp_server.server
用法（streamable-http，中央部署時切換；工具碼不變）：
    uv run python -m backend.app.mcp_server.server --transport http --host 0.0.0.0 --port 8300

設計要點：
- 啟動時明確 load_dotenv(專案根/.env)：MCP 子行程不繼承開發 shell 的 PGPORT，
  沒載到 .env 會連到 5432 的空殼庫「成功地查到 0 筆」。呼叫方可用
  get_data_status 自查連線目標與資料量。
- logging 全導 stderr：stdio 模式的 stdout 是 JSON-RPC 通道，不可污染。
  引擎呼叫路徑經查無 print()；日後在工具路徑加程式勿印 stdout。
- 工具實作在 tools_reporting / tools_clustering（純函式，不依賴本檔），
  本檔只負責綁定與傳輸。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# .env 要在載入任何會讀 PG* 環境變數的模組之前載好。
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env", override=False)

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("patent-mcp")

from mcp.server.fastmcp import FastMCP  # noqa: E402（dotenv／logging 先設好再載）

from backend.app.db.connection import get_connection_kwargs  # noqa: E402
from backend.app.mcp_server import (  # noqa: E402
    tools_ai,
    tools_clustering,
    tools_market,
    tools_reporting,
)

mcp = FastMCP("patent")

# ── reporting tools（報表引擎）──────────────────────────────────
mcp.tool()(tools_reporting.list_reports)
mcp.tool()(tools_reporting.run_report_analysis)
mcp.tool()(tools_reporting.get_data_status)
mcp.tool()(tools_reporting.save_workflow_output)
mcp.tool()(tools_reporting.refresh_derived_data)
mcp.tool()(tools_reporting.generate_report_ppt)

# ── AI 任務工具（取數口＋敘述型回存）────────────────────────────
mcp.tool()(tools_ai.get_report_payload)
mcp.tool()(tools_ai.save_analysis_narrative)

# ── market evidence tools（市場資料證據庫）──────────────────────
mcp.tool()(tools_market.get_market_evidence)
mcp.tool()(tools_market.save_market_evidence)
mcp.tool()(tools_market.aggregate_market_evidence)
mcp.tool()(tools_market.prepare_market_evidence_task)
mcp.tool()(tools_market.save_market_evidence_candidates)
mcp.tool()(tools_market.accept_market_evidence_candidates)

# ── clustering tools（分群引擎，輕量七支）───────────────────────
mcp.tool()(tools_clustering.list_workspaces)
mcp.tool()(tools_clustering.get_workspace_dashboard)
mcp.tool()(tools_clustering.get_candidate_review_payload)
mcp.tool()(tools_clustering.apply_candidate_explanations)
mcp.tool()(tools_clustering.get_topic_labeling_payload)
mcp.tool()(tools_clustering.apply_topic_labels)
mcp.tool()(tools_clustering.get_merge_history)


def main() -> None:
    """解析傳輸參數並啟動 server（stdio 預設；http 供中央部署）。"""
    parser = argparse.ArgumentParser(description="Central Patent MCP Server")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="http 傳輸的綁定位址")
    parser.add_argument("--port", type=int, default=8100, help="http 傳輸的埠（8000 為 FastAPI）")
    args = parser.parse_args()

    kwargs = get_connection_kwargs()
    logger.info(
        "Patent MCP starting (transport=%s) DB target: %s:%s/%s",
        args.transport,
        kwargs.get("host", "(DATABASE_URL)"),
        kwargs.get("port", ""),
        kwargs.get("dbname", ""),
    )

    if args.transport == "http":
        # 第一版安全從簡＝內網 bearer token（已定案）；未設 token 一律拒啟 http，避免裸露。
        token = os.getenv("PATENT_MCP_TOKEN")
        if not token:
            logger.error("PATENT_MCP_TOKEN 未設，拒絕以 http 模式啟動（內網 token 為必要條件）")
            raise SystemExit(2)
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        import uvicorn

        from backend.app.mcp_server._auth import BearerTokenMiddleware

        app = BearerTokenMiddleware(mcp.streamable_http_app(), token)
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
