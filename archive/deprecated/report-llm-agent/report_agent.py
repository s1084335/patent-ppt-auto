"""報表引擎的 LLM 調用端（後端模組）——自然語言提問 → 模型決定跑哪些報表 → 彙整回答。

形態＝message：走 OpenAI 相容端點（HF router / DeepSeek / 任何相容服務），
工具迴圈由本模組自己掌握——送 messages＋tools → 收 tool_calls →
執行 run_reports_batch → append tool 結果 → 迴圈到模型給出最終回答。
現配模型：Qwen/Qwen3-32B（2026-07-15 使用者選定，開發期用開源模型）。

Claude（anthropic SDK 形態）2026-07-15 使用者裁決先不用；
之後要啟用時在 ask_reports 加 provider 分支即可（工具契約不變）。

環境變數（.env / .env.example）：
    REPORT_LLM_PROVIDER   huggingface（目前唯一實作）
    REPORT_LLM_BASE_URL   OpenAI 相容端點（預設 HF router）
    REPORT_LLM_MODEL      模型 id（預設 Qwen/Qwen3-32B）
    REPORT_LLM_API_KEY    金鑰；未設時回退 HF_TOKEN

CLI（開發期驗證用）:
    uv run python -m backend.app.llm.report_agent "美國的申請趨勢和佈局如何?"
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from backend.app.reports.report_definitions import REPORT_DEFINITIONS
from backend.app.reports.report_engine import run_reports_batch

# --- 預設組態 -----------------------------------------------------------------

DEFAULT_PROVIDER = "huggingface"
DEFAULT_BASE_URL = "https://router.huggingface.co/v1"
DEFAULT_MODEL = "Qwen/Qwen3-32B"

MAX_TOKENS = 2048  # 2026-07-15 使用者指定：回答走精簡路線，控制成本與延遲
MAX_TOOL_ROUNDS = 6
# 回傳給模型的每張報表列數上限：控制 token 成本；detail 型大表尤其需要。
DEFAULT_ROW_LIMIT = 50

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Qwen3 系列會輸出 <think>…</think> 推理段，最終回答要剝掉。
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def load_env_file(path: Path | None = None) -> None:
    """本機開發用：把專案根目錄 .env 中尚未設定的變數載入環境（不覆蓋既有值）。"""
    env_path = path or (_PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value


def resolve_config() -> dict[str, str]:
    """整理 provider / base_url / model / api_key（金鑰不落 log）。"""
    load_env_file()
    provider = (os.environ.get("REPORT_LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    api_key = os.environ.get("REPORT_LLM_API_KEY", "").strip() or os.environ.get("HF_TOKEN", "").strip()
    return {
        "provider": provider,
        "base_url": os.environ.get("REPORT_LLM_BASE_URL", DEFAULT_BASE_URL).strip(),
        "model": os.environ.get("REPORT_LLM_MODEL", DEFAULT_MODEL).strip(),
        "api_key": api_key,
    }


def agent_available() -> bool:
    """前端用：目前組態是否可用（有金鑰）。"""
    return bool(resolve_config()["api_key"])


# --- 工具定義（兩個 provider 共用同一份契約） ----------------------------------

def report_catalog_text() -> str:
    """把可用報表清單組成說明文字，讓模型知道每張報表是什麼。"""
    lines = []
    for name, definition in sorted(REPORT_DEFINITIONS.items()):
        scope = "全庫口徑、不支援篩選" if not definition.supports_patent_ids else "支援篩選"
        lines.append(f"- {name}: {definition.label_zh}（{definition.report_type}，{scope}）")
    return "\n".join(lines)


TOOL_NAME = "run_patent_reports"
TOOL_DESCRIPTION = (
    "執行專利統計報表並取回數據，可一次執行多張。"
    "report_names 的可用值見系統提示中的報表目錄。"
)
# JSON Schema 手寫一份、兩個 provider 共用（anthropic 用 input_schema、OpenAI 相容用 parameters）。
TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "report_names": {
            "type": "array",
            "items": {"type": "string"},
            "description": "要執行的報表 key 清單",
        },
        "country_code": {"type": "string", "description": "受理局代碼篩選，如 US、CN、EP"},
        "application_year_from": {"type": "integer", "description": "申請年下限（含）"},
        "application_year_to": {"type": "integer", "description": "申請年上限（含）"},
        "applicant_display_name": {"type": "string", "description": "申請人顯示名精確比對"},
        "limit": {"type": "integer", "description": "每張報表回傳列數上限，預設 50"},
    },
    "required": ["report_names"],
}


def execute_report_tool(arguments: dict[str, Any], call_log: list[dict[str, Any]]) -> str:
    """工具實體：組 filters → run_reports_batch → JSON 字串（兩個 provider 共用）。"""
    filters: dict[str, Any] = {}
    country = (arguments.get("country_code") or "").strip()
    if country:
        filters["country_code"] = country.upper()
    year_range: dict[str, int] = {}
    if arguments.get("application_year_from") is not None:
        year_range["from"] = int(arguments["application_year_from"])
    if arguments.get("application_year_to") is not None:
        year_range["to"] = int(arguments["application_year_to"])
    if year_range:
        filters["application_year"] = year_range
    applicant = (arguments.get("applicant_display_name") or "").strip()
    if applicant:
        filters["applicant_display_name"] = applicant

    report_names = list(arguments.get("report_names") or [])
    limit = int(arguments.get("limit") or DEFAULT_ROW_LIMIT)
    results = run_reports_batch(report_names, filters=filters or None, limit=limit)
    call_log.append({"report_names": report_names, "filters": filters, "limit": limit})
    return json.dumps(results, ensure_ascii=False, default=str)


SYSTEM_PROMPT_TEMPLATE = """你是專利分析報表助手，透過 run_patent_reports 工具查詢公司的專利資料庫統計報表。

可用報表目錄：
{catalog}

規則：
- 回答一律使用繁體中文；專利號、公司名、代碼保留原文。
- 數據只能來自工具回傳結果，不得依常識或記憶捏造數字；工具沒回傳的就說沒有。
- 需要多張報表時一次呼叫（report_names 傳多個），不要分次。
- 「國家佈局」指 family_country_layout（現有保護、家族數）；「受理局/國別分布」指 country_distribution（歷史申請件數）。兩者口徑不同，引用時要說明用的是哪個。
- 被引用數是資料下載時點的快照。家族層級報表（family_*）為全庫口徑：帶篩選呼叫時篩選會被自動忽略並在結果附註記，引用其數字時要說明是全庫值。
- 回答時附上關鍵數字，簡潔為主。
- 職責範圍僅限本系統的專利報表查詢與解讀。與此無關的問題（天氣、閒聊、一般知識、時事等）一律不回答內容，簡短說明職責並引導使用者提出報表相關問題。"""


# --- provider 實作 --------------------------------------------------------------

class ModelServiceError(RuntimeError):
    """模型服務端的暫時性錯誤（逾時/限流/5xx），呼叫端可轉成友善訊息。"""


def _ask_openai_compatible(question: str, config: dict[str, str]) -> dict[str, Any]:
    """message 形態：OpenAI 相容端點（HF router 等），工具迴圈由本函式掌握。

    穩定度：單次請求 90 秒 timeout、SDK 自動退避重試 2 次；
    重試耗盡仍失敗時拋 ModelServiceError（不外洩 traceback 給前端）。
    """
    import openai
    from openai import OpenAI

    client = OpenAI(
        base_url=config["base_url"],
        api_key=config["api_key"],
        timeout=90.0,
        max_retries=2,
    )
    call_log: list[dict[str, Any]] = []
    tools = [{
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": TOOL_DESCRIPTION,
            "parameters": TOOL_INPUT_SCHEMA,
        },
    }]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(catalog=report_catalog_text())},
        {"role": "user", "content": question},
    ]

    final_text = ""
    for _round in range(MAX_TOOL_ROUNDS):
        try:
            response = client.chat.completions.create(
                model=config["model"],
                messages=messages,
                tools=tools,
                max_tokens=MAX_TOKENS,
            )
        except (openai.APITimeoutError, openai.RateLimitError, openai.APIConnectionError,
                openai.InternalServerError) as exc:
            raise ModelServiceError("模型服務忙碌或逾時，請稍後再試") from exc
        choice = response.choices[0]
        message = choice.message
        if message.tool_calls:
            # append 助手回合（含 tool_calls）後逐一執行工具，再把結果以 tool 角色回填。
            messages.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [tc.model_dump() for tc in message.tool_calls],
            })
            for tool_call in message.tool_calls:
                try:
                    arguments = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                if tool_call.function.name == TOOL_NAME:
                    result = execute_report_tool(arguments, call_log)
                else:
                    result = json.dumps({"error": f"unknown tool: {tool_call.function.name}"}, ensure_ascii=False)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })
            continue
        final_text = _THINK_RE.sub("", message.content or "").strip()
        break

    return {"answer": final_text, "tool_calls": call_log, "model": config["model"], "provider": config["provider"]}


def ask_reports(question: str) -> dict[str, Any]:
    """自然語言提問 → LLM 決定跑哪些報表 → 回傳彙整答案與執行紀錄。

    回傳 {"answer", "tool_calls", "model", "provider"}；金鑰未設時 raise RuntimeError。
    """
    config = resolve_config()
    if not config["api_key"]:
        raise RuntimeError("HF_TOKEN 或 REPORT_LLM_API_KEY 未設定（.env 或環境變數），無法使用 AI 問答。")
    return _ask_openai_compatible(question, config)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the report agent a question in natural language.")
    parser.add_argument("question", help="自然語言問題（繁體中文）")
    args = parser.parse_args()
    result = ask_reports(args.question)
    print(json.dumps(
        {"provider": result["provider"], "model": result["model"], "tool_calls": result["tool_calls"]},
        ensure_ascii=False, indent=2,
    ))
    print()
    print(result["answer"])


if __name__ == "__main__":
    main()
