"""負面關鍵字 → 英文比對詞（`ai:keyword_expand`，PRE-002，2026-08-21）。

## 這個 job 為什麼存在

初階篩選的比對欄位（`title`／`abstract`／獨立項）**全是英文**——實測 `割草` 於
`abstract` 命中 0。使用者輸入中文關鍵字時，不轉成英文就必然零命中，
而零命中看起來就像「這個詞沒問題」。⇒ 轉換是必要步驟，不是便利功能。

## 🔴 三條護欄

1. **產出一律未確認**：`store_expansion` 把 `terms_confirmed` **寫死 False**，
   且函式簽章**沒有**確認相關參數——有參數就有人會傳 True。
   使用者確認才生效（PRE-002），與 `store_ai_verdicts` 只能寫 `pending` 同一個設計。

2. **NO_TOOLS**：本 job 只做中英轉換，不需要讀檔、不需要查 DB、不需要上網。
   ⚠ **自行** `functools.partial(_gw_build_cli_command, tools=NO_TOOLS)`，
   **不從任何其他 runner import**——`ai_candidate_explanation_runner:52` 與
   `ai_topic_backfill_runner:41` 都留著同一個血淚註解：早期從別的 runner import，
   而那是 `partial(tools=RESEARCH_TOOLS)`，於是靜默取得 12 支工具＋MCP 取證權限。

3. **失敗要明確回報**：解析不出來就 raise，不得靜默寫入空陣列。
   ⚠ 靜默寫空的後果是使用者看到「轉換完成」卻一個詞都沒有，
   會以為是 AI 判斷沒有對應詞，實際是解析失敗——而且不阻斷他自行輸入（PRE-002）。
"""
from __future__ import annotations

import functools
import json
import re
from typing import Any

from .ai_payload_file import extract_json_payload
from .cli_gateway import DEFAULT_CLI_TIMEOUT_SECONDS, NO_TOOLS, parse_cli_result, run_cli
from .cli_gateway import build_cli_command as _gw_build_cli_command

#: 🔴 自行綁 NO_TOOLS，見模組 docstring 護欄 2。
build_keyword_expand_cli_command = functools.partial(
    _gw_build_cli_command, tools=NO_TOOLS)

PROMPT_VERSION = "keyword_expand_v1"

#: 只收純 ASCII 英文詞：比對欄位全為英文，中文詞放進去必然零命中。
_ASCII_TERM = re.compile(r"^[A-Za-z][A-Za-z0-9\-' ]*$")


class KeywordExpandError(RuntimeError):
    """轉換失敗（CLI 輸出無法解析、或解析後沒有可用的英文詞）。"""


def build_prompt(original_term: str) -> str:
    """組出轉換提示詞。

    ⚠ 只要求「英文比對詞」，不要求解釋、不要求信心分數——多要一項就多一個
    可能解析失敗的地方，而這個 job 的產出只有一種用途。
    """
    term = (original_term or "").strip()
    if not term:
        raise ValueError("original_term 不得為空")
    return (
        "任務：把一個專利檢索用的負面關鍵字轉成英文比對詞（系統派工、非互動）。\n\n"
        f"輸入關鍵字：{term}\n\n"
        "要求：\n"
        "1. 回傳該詞在專利文獻（標題／摘要／獨立項）中可能出現的**英文**表達，\n"
        "   含同義詞與常見詞形變化的**詞幹**。\n"
        "2. 只回英文；輸入若已是英文，仍要補上同義詞與詞形。\n"
        "3. 比對採**前綴詞界**（詞幹即可命中其衍生形），故給詞幹不必給全部變化：\n"
        "   例如給 `mow` 即可涵蓋 mower／mowing／mowed，不必逐一列出。\n"
        "4. 不要給過短或過於通用而會大量誤命中的詞（例如兩個字母的縮寫）。\n\n"
        "輸出：只輸出一個 JSON 物件，不要任何說明文字：\n"
        '{"terms": ["...", "..."]}'
    )


def extract_terms(raw: str) -> list[str]:
    """從 CLI 輸出取出英文比對詞（去重、小寫、排序）。

    ⚠ 排序固定：PRE-001 要求「關鍵字與資料皆未變動時重跑可重現」，
    而模型輸出順序不保證，不排序會讓兩次結果看起來不同。

    ⚠ 非英文詞直接濾掉而非報錯：模型偶爾夾帶原文是常態，
    整批打掉會讓可用的結果一起消失。但**全部被濾光**要當失敗處理。
    """
    try:
        payload = extract_json_payload(raw)
    except Exception as exc:  # noqa: BLE001
        raise KeywordExpandError(f"CLI 輸出無法解析為 JSON：{exc}") from exc

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise KeywordExpandError(f"CLI 輸出無法解析為 JSON：{exc}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("terms"), list):
        raise KeywordExpandError("CLI 輸出缺少 terms 陣列")

    seen: set[str] = set()
    for item in payload["terms"]:
        text = str(item or "").strip().lower()
        if not text or not _ASCII_TERM.match(text):
            continue
        seen.add(text)

    if not seen:
        raise KeywordExpandError("轉換後沒有可用的英文比對詞")
    return sorted(seen)


def run_keyword_expand(*, keyword_id: int, original_term: str,
                       cli_kind: str = "claude",
                       model: str | None = None,
                       cli_runner: Any | None = None,
                       timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
                       progress: Any | None = None) -> dict[str, Any]:
    """跑一次轉換：組指令 → CLI → 解析 → 落庫（未確認草稿）。

    ⚠ 轉換失敗一律 raise `KeywordExpandError`，由 job 層回報給使用者。
    使用者仍可自行輸入英文比對詞完成篩選（PRE-002「轉換不可用時不阻斷」）
    ——那條路徑不經過本函式，故本函式失敗不影響它。
    """
    def _tick(stage: str, percent: int) -> None:
        if progress is not None:
            progress(stage, percent)

    _tick("組出轉換提示詞", 10)
    argv = build_keyword_expand_cli_command(cli_kind, build_prompt(original_term))

    _tick("執行 CLI 轉換", 30)
    runner = cli_runner or run_cli
    result = runner(argv, timeout_seconds)
    envelope = parse_cli_result(result)
    raw = str(envelope.get("result") or "")

    _tick("解析比對詞", 80)
    terms = extract_terms(raw)

    _tick("寫回未確認草稿", 95)
    row = store_expansion(keyword_id, terms)
    _tick("完成", 100)
    return {
        "keyword_id": keyword_id,
        "original_term": original_term,
        "terms": terms,
        "terms_confirmed": row["terms_confirmed"],
        "prompt_version": PROMPT_VERSION,
        "cli_kind": cli_kind,
    }


def store_expansion(keyword_id: int, terms: list[str], *,
                    conn: Any | None = None) -> dict[str, Any]:
    """把轉換結果寫回關鍵字，**一律為未確認草稿**。

    🔴 `terms_confirmed=False` 是寫死的，且本函式**不接受**任何確認相關參數
    ——開放參數等於留一條讓 AI 產出直接生效的路（PRE-002）。
    使用者要讓它生效，走 `keywords.update_keyword(terms_confirmed=True)`。
    """
    from backend.app.prefilter import keywords as kw

    return kw.update_keyword(
        keyword_id,
        match_terms=terms,
        terms_confirmed=False,
        conn=conn,
    )
