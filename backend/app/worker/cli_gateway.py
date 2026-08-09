"""headless CLI 呼叫的**唯一入口**（2026-08-09 使用者定案「能整合的都要整合」）。

原本 `_CLI_SPECS`／`build_cli_command` 散在**七個** runner 各自定義，其中三份
一字不差只差例外類別名。⚠ 實害不是重複程式碼，是**改一處不會同步**——把
MCP 取證白名單加上去要改七處，漏一處那條線就查不到資料庫，而且不會報錯。

## 權限是等級，不是同一份白名單

整合的是 argv 骨架，**不是權限**。四支最小權限任務（company_zh_name、
irrelevant_filter、patent_note、report_ppt 舊路徑）資料內嵌 prompt，本來就
不需要任何工具；把它們併進同一份白名單是**擴權**，是安全退步不是整合。
所以權限做成三個顯式等級，由各 runner 宣告自己要哪一級。

| 等級 | 內容 | 用在哪 |
|---|---|---|
| `NO_TOOLS` | 空白名單 | 資料內嵌 prompt 的任務 |
| `READ_ONLY_TOOLS` | `Read` | 走資料檔（ai_payload_file） |
| `RESEARCH_TOOLS` | 檔案工具＋MCP 唯讀取證 | 敘述線、規劃線 |

## 取證一律走 MCP（2026-08-09 定案）

敘述線原本靠 `Bash(uv run:*)` 呼叫查詢閘道 `query_patents.py`。改走 MCP 的
理由是介面本身就是護欄：typed 參數、工具清單即能力清單，不必靠提示詞約束
「該查哪張表」。⚠ 但 MCP 的 `report_research` 原本七支工具讀的是報表快照
（`report_data.json`）**不是資料庫**——所以同時補了 `query_database`，否則
「統一到 MCP」等於把敘述線的取證能力砍掉。
"""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.mcp_server.report_research import TOOL_NAMES as _RESEARCH_TOOL_NAMES

# repo 根（本檔在 backend/app/worker/ 之下三層）。
REPO_ROOT = Path(__file__).resolve().parents[3]

# MCP config 內的 server 名稱＝CLI 工具前綴 `mcp__<名稱>__<工具>`。
# ⚠ 用底線不用連字號：工具名要能被白名單字串安全比對。
MCP_SERVER_NAME = "patent_research"


class CliGatewayError(RuntimeError):
    """CLI 指令組裝或執行不合契約（未知 cli_kind、不支援 model、啟動失敗等）。"""


@dataclass
class CliResult:
    """headless CLI 的一次執行結果。"""

    exit_code: int
    stdout: str
    stderr: str


_CLI_SPECS: dict[str, dict[str, Any]] = {
    "claude": {
        "binary": "claude",
        # -p 進 headless、json 輸出；工具白名單由呼叫端以 tools 參數指定。
        "prompt_flag": "-p",
        # 指定模型的旗標名（模型值由任務 payload 帶，不寫死；None＝該 CLI 不支援指定）。
        "model_flag": "--model",
        "tail_args": ["--output-format", "json"],
    },
    "opencode": {
        # OpenCode 對接介面（Companion 雙 CLI 定案）；binary 與旗標到位時由此替換。
        "binary": "opencode",
        "prompt_flag": "run",
        "model_flag": "--model",
        "tail_args": ["--format", "json"],
        # 未提供工具白名單旗標，tools 參數對它無效。
        "supports_tools": False,
    },
}

# ── 權限等級 ──────────────────────────────────────────────────
NO_TOOLS = ""
READ_ONLY_TOOLS = "Read"

# 取證等級：檔案工具＋MCP 唯讀工具（工具名由 TOOL_NAMES 推導，不另抄一份）。
RESEARCH_TOOLS: tuple[str, ...] = (
    "Read", "Glob", "Grep", "Write",
    *(f"mcp__{MCP_SERVER_NAME}__{name}" for name in _RESEARCH_TOOL_NAMES),
)


def build_stdio_mcp_config() -> dict[str, Any]:
    """CLI 專用的隔離 MCP config：**只掛唯讀 profile**，不含任何 DB credential。

    走 stdio 而非 http——Companion 與 CLI 同機，stdio 免 token、免開埠。
    DB 連線只存在 server 端（唯讀交易由連線層強制）。
    """
    return {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": "uv",
                "args": ["run", "python", "-m", "backend.app.mcp_server.server",
                         "--profile", "research"],
                "cwd": str(REPO_ROOT),
            }
        }
    }


def _tool_args(tools: str | Sequence[str]) -> list[str]:
    """把權限等級轉成 argv 片段；含 MCP 工具時一併掛上隔離 config。

    ⚠ 放行 `mcp__*` 卻沒帶 `--mcp-config` 的話工具根本起不來，而且是**靜默**
    失效——CLI 只會當作沒有那些工具，照樣產出看似合理的內容。
    """
    names = [tools] if isinstance(tools, str) else list(tools)
    args: list[str] = []
    if any(n.startswith("mcp__") for n in names):
        args += ["--mcp-config", json.dumps(build_stdio_mcp_config())]
    # ⚠ --allowedTools 吃多個位置參數，一律排在最後：後面再接旗標會踩到解析邊界。
    return args + (["--allowedTools", *names] if names else ["--allowedTools", ""])


def build_cli_command(
    cli_kind: str,
    prompt: str,
    *,
    model: str | None = None,
    tools: str | Sequence[str] = NO_TOOLS,
) -> list[str]:
    """組 headless argv。權限由 `tools` 明示，預設最小權限（無工具）。"""
    spec = _CLI_SPECS.get(cli_kind)
    if spec is None:
        raise CliGatewayError(f"未知 cli_kind：{cli_kind!r}（可用：{sorted(_CLI_SPECS)}）")
    model_args: list[str] = []
    if model:
        model_flag = spec.get("model_flag")
        if not model_flag:
            raise CliGatewayError(f"{cli_kind!r} 不支援指定 model")
        model_args = [model_flag, model]
    tail = list(spec["tail_args"])
    if spec.get("supports_tools", True):
        tail += _tool_args(tools)
    return [spec["binary"], spec["prompt_flag"], prompt, *model_args, *tail]


def parse_cli_result(result: CliResult) -> dict[str, Any]:
    """解析 headless CLI 的 `--output-format json` 輸出（envelope，內文在 result 欄）。"""
    if result.exit_code != 0:
        raise CliGatewayError(
            f"CLI 失敗（exit={result.exit_code}）：{(result.stderr or '').strip()[:400]}")
    text = (result.stdout or "").strip()
    if not text:
        raise CliGatewayError("CLI stdout 為空")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliGatewayError(f"CLI 輸出非 JSON：{text[:200]}") from exc


def run_cli(argv: Sequence[str], timeout: float) -> CliResult:
    """實際起 subprocess。

    ⚠ `encoding="utf-8"` 不可省：父行程 UTF-8 解碼失敗會讓 stdout 回 None
    （2026-07-30 實機 job #132／#135／#137 的共同根因）。
    """
    try:
        completed = subprocess.run(
            list(argv),
            check=False,  # 退出碼交給 parse_cli_result 判讀（要保留 stderr 內文）
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=timeout,
        )
    except OSError as exc:
        raise CliGatewayError(f"無法啟動 CLI：{exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CliGatewayError(f"CLI 逾時（{timeout} 秒）") from exc
    return CliResult(exit_code=completed.returncode,
                     stdout=completed.stdout, stderr=completed.stderr)
