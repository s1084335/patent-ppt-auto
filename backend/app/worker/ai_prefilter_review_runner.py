"""初階篩選：AI 對命中專利建議留或剔（`ai:prefilter_review`，PRE-008，2026-08-21）。

## 這條線在做什麼

負面關鍵字比對是確定性的（`prefilter.matching`），但**命中不等於該剔除**。
正式庫 #591 `VEHICLE WITH UNDER-BODY BLOWER` 被「吹葉機」命中，實際是
**帶吹風平台的割草載具**——屬於範圍內。本 runner 讀該專利的標題、摘要與
獨立項，對照使用者填的整批範圍描述，給出「保留／剔除」的建議與理由。

🔴 **只是建議**：不改變任何專利的排除狀態，使用者仍逐筆確認（PRE-008）。
護欄落在 `prefilter.suggestions.store_suggestions`（SET 裡沒有 status）。

## ⚠ 四個刻意的設計

1. **沒有範圍描述就拒跑**。沒有判讀依據卻硬產建議＝編造，而且會產出
   **看起來很肯定**的錯建議——比沒有建議更糟。
   ⚠ 不退化成「用 workspace 名稱湊合」：使用者裁決過，「自走式割草機」
   五個字判斷不了「刀片結構算不算範圍內」。

2. **三欄皆空者不呼叫 AI**。那是確定性判斷（`no_basis`）：沒有任何內容可讀，
   問了只會得到編造的答案，還要花錢。

3. **AI 不得自己宣稱 `no_basis`**。那是程式判定。讓 AI 回得了它，等於給一個
   「不想判就跳過」的出口，而那筆專利明明有內容——使用者會以為是資料缺漏。

4. **按字數切批不按件數**。獨立項單篇逾萬字（沿 `ai:patent_note` 既有口徑）。
   資料走 payload 檔不走 argv：Windows `CreateProcess` 上限 32,767。

## 工具權限：READ_ONLY_TOOLS

走 `pf.build_cli_command_with_payload`，只有 Read。不給 Bash／WebFetch——
專利文字是外部輸入，避免其中的 prompt injection 取得執行或連外能力。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from backend.app.prefilter import suggestions as sg

from .ai_payload_file import extract_json_payload
from .cli_gateway import DEFAULT_CLI_TIMEOUT_SECONDS, parse_cli_result, run_cli

PROMPT_VERSION = "prefilter_review_v1"

#: 單批字元預算。沿 `ai:patent_note` 的 12,000 口徑——同樣是「獨立項全文」等級的內容。
DEFAULT_CHAR_BUDGET = 12_000

#: AI 可以回的建議值。
#: 🔴 由 `suggestions.VALID_VERDICTS` **推導**，不另寫一份字面值——
#: 兩處各寫一份的話，日後加值只改一邊不會報錯，症狀是合法建議被當成非法而整批失敗。
#: `no_basis` 排除在外：那是程式判定，見模組 docstring 第 3 點。
AI_VERDICTS = tuple(v for v in sg.VALID_VERDICTS if v != "no_basis")


class ScopeReviewError(RuntimeError):
    """判讀失敗：缺範圍描述、CLI 輸出不合契約、或出現幻覺 patent_id。"""


def _blank(text: Any) -> bool:
    """欄位是否無內容（NULL 或全空白）。"""
    return not str(text or "").strip()


def split_by_basis(
    items: Sequence[tuple[int, Any, Any, Any]],
) -> tuple[list[tuple[int, Any, Any, Any]], list[int]]:
    """把待判讀項目切成「有依據的」與「三欄皆空的」。

    ⚠ 後者不進 AI：確定性判斷，見模組 docstring 第 2 點。
    """
    judgeable: list[tuple[int, Any, Any, Any]] = []
    no_basis: list[int] = []
    for pid, title, abstract, claims in items:
        if _blank(title) and _blank(abstract) and _blank(claims):
            no_basis.append(int(pid))
        else:
            judgeable.append((int(pid), title, abstract, claims))
    return judgeable, no_basis


def build_payload(scope_description: str,
                  batch: Sequence[tuple[int, Any, Any, Any]]) -> dict[str, Any]:
    """組 payload 檔內容。

    ⚠ 三個判讀欄位與範圍描述都必須真的送進去。prompt 寫「請依標題摘要獨立項
    判斷」但 payload 沒帶，AI 就是在**憑空判斷**——而它仍會給出很肯定的答案。
    """
    return {
        "instruction": (
            "判斷每一筆專利與「整批專利的技術範圍」是否相關，"
            "對每一筆給出建議：keep（屬於本批範圍，建議保留）或 "
            "exclude（不屬於本批範圍，建議剔除）。\n"
            "判讀依據只能是該筆的 title／abstract／claims 三個欄位，"
            "以及下方的 batch_scope。不得依賴其他知識或臆測。\n"
            "reason 用繁體中文，一句話說明它與 batch_scope 的關係，"
            "並指出你是從哪個欄位看出來的。\n"
            "⚠ 命中負面關鍵字不等於應該剔除：關鍵字只是把它挑出來讓人看，"
            "真正要判斷的是它與 batch_scope 的關係。"
        ),
        "batch_scope": scope_description,
        "output_contract": {
            "verdicts": [{"patent_id": 0, "verdict": "keep|exclude", "reason": ""}]
        },
        "items": [
            {
                "patent_id": pid,
                "title": title or "",
                "abstract": abstract or "",
                "claims": claims or "",
            }
            for pid, title, abstract, claims in batch
        ],
    }


def extract_verdicts(raw: str, known_ids: set[int]) -> list[dict[str, Any]]:
    """從 CLI 回覆取出建議並逐項驗證。

    🔴 三種情形一律 raise，不靜默略過：
    - 幻覺 `patent_id`（不在本批）——不把不存在的判斷寫進正式資料
    - 非法 `verdict`（含 AI 自稱 `no_basis`）
    - 空的 `reason`——沒有理由的建議使用者無從評估

    ⚠ 靜默略過的後果是那幾筆**永遠停在「尚無建議」**，而使用者不知道
    它其實跑過了——他會一直等一個不會來的東西。
    """
    try:
        payload = extract_json_payload(raw)
    except Exception as exc:  # noqa: BLE001
        raise ScopeReviewError(f"CLI 輸出無法解析成 JSON：{exc}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("verdicts"), list):
        raise ScopeReviewError("CLI 輸出缺少 verdicts 陣列")

    out: list[dict[str, Any]] = []
    for item in payload["verdicts"]:
        if not isinstance(item, dict):
            raise ScopeReviewError(f"verdicts 項目不是物件：{item!r}")
        try:
            pid = int(item.get("patent_id"))
        except (TypeError, ValueError) as exc:
            raise ScopeReviewError(
                f"patent_id 非整數：{item.get('patent_id')!r}") from exc
        if pid not in known_ids:
            raise ScopeReviewError(
                f"CLI 產出未知 patent_id：{pid}（本批：{sorted(known_ids)}）")
        verdict = str(item.get("verdict") or "").strip()
        if verdict not in AI_VERDICTS:
            raise ScopeReviewError(
                f"patent {pid} 的建議值 {verdict!r} 不在 {AI_VERDICTS}")
        reason = str(item.get("reason") or "").strip()
        if not reason:
            raise ScopeReviewError(f"patent {pid} 的建議沒有理由")
        out.append({"patent_id": pid, "verdict": verdict, "reason": reason})
    return out


def run_prefilter_review(
    *,
    workspace_id: int,
    scope_description: str | None = None,
    items: Iterable[tuple[int, Any, Any, Any]] | None = None,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: Any | None = None,
    store: Any | None = None,
    char_budget: int = DEFAULT_CHAR_BUDGET,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
    payload_root: Any = None,
) -> dict[str, Any]:
    """整條流程：取範圍描述與待判讀專利 → 分流 → 按字數切批 → 逐批呼 CLI → 寫回。

    `scope_description`／`items`／`store` 皆可注入，供測試以假資料取代，不碰 DB。
    """
    from . import ai_payload_file as pf

    def _tick(stage: str, percent: int) -> None:
        if progress is not None:
            progress(stage, percent)

    scope_text = scope_description
    if scope_text is None:
        from backend.app.prefilter import scope as scope_mod
        scope_text = scope_mod.get_scope_description(workspace_id)
    scope_text = str(scope_text or "").strip()
    if not scope_text:
        # 🔴 拒跑而不是湊合：見模組 docstring 第 1 點。
        raise ScopeReviewError(
            "尚未填寫這批專利的範圍描述，無法判斷命中專利是否屬於本批範圍。"
            "請先於初階篩選頁填寫一句範圍描述。")

    _tick("讀取待判讀的專利", 5)
    if items is None:
        rows = _fetch_targets(workspace_id)
    else:
        rows = list(items)

    judgeable, no_basis = split_by_basis(rows)
    writer = store if store is not None else sg.store_suggestions

    # 三欄皆空者先落地：不呼叫 AI，也不讓它們卡在「尚無建議」。
    if no_basis:
        writer(workspace_id,
               [{"patent_id": pid, "verdict": "no_basis",
                 "reason": "標題、摘要與獨立項皆為空，無判讀依據"}
                for pid in no_basis])

    if not judgeable:
        _tick("沒有可判讀的專利", 100)
        return {
            "workspace_id": workspace_id,
            "judged": 0,
            "no_basis": len(no_basis),
            "batches": 0,
            "cli_kind": cli_kind,
            "prompt_version": PROMPT_VERSION,
        }

    pf.cleanup_old_payloads(root=payload_root)
    batches = pf.split_into_batches(
        judgeable,
        max_chars=char_budget,
        # 只量真正會進 payload 的三個欄位，不含 patent_id 與 JSON 括號——
        # 估得比實際略小，但切批的目的是防爆不是精算。
        size_of=lambda it: sum(len(str(x or "")) for x in it[1:]),
    )

    runner = cli_runner if cli_runner is not None else run_cli
    total = len(batches)
    judged = 0
    for index, batch in enumerate(batches, start=1):
        _tick(f"判讀範圍相關性（第 {index}/{total} 批，{len(batch)} 件）",
              5 + int(90 * (index - 1) / total))

        path = pf.write_payload_file(
            "prefilter_review",
            build_payload(scope_text, batch),
            root=payload_root,
            label=f"batch{index:02d}",
        )
        argv = pf.build_cli_command_with_payload(
            cli_kind,
            instruction=("任務：判斷專利與整批專利技術範圍的相關性，"
                         "給出保留或剔除的建議（系統派工、非互動、一次性）。"),
            payload_path=path,
            model=model,
        )
        parsed = parse_cli_result(runner(argv, timeout_seconds))
        raw = str(parsed.get("result") or "")
        verdicts = extract_verdicts(raw, {pid for pid, *_ in batch})
        writer(workspace_id, verdicts)
        judged += len(verdicts)

    _tick("完成", 100)
    return {
        "workspace_id": workspace_id,
        "judged": judged,
        "no_basis": len(no_basis),
        "batches": total,
        "cli_kind": cli_kind,
        "prompt_version": PROMPT_VERSION,
    }


def _fetch_targets(workspace_id: int) -> list[tuple[int, Any, Any, Any]]:
    """取待判讀專利的三個判讀欄位。

    ⚠ 對象走 `suggestions.pending_targets`（待裁決且尚無建議），
    欄名走 `matching.MATCH_FIELDS` 唯一定義處——獨立項欄名是中文帶國別後綴，
    寫死在這裡就會變成第二個定義點。
    """
    from backend.app.clustering.exclusions import _conn_ctx
    from backend.app.prefilter import matching

    with _conn_ctx(None) as c:
        target_ids = sg.pending_targets(workspace_id, conn=c)
        if not target_ids:
            return []
        cols = ", ".join(f'"{col}"' for _, col, _ in matching.MATCH_FIELDS)
        with c.cursor() as cur:
            cur.execute(
                f"SELECT patent_id, {cols} FROM {matching.SOURCE_TABLE} "
                f"WHERE patent_id = ANY(%s) ORDER BY patent_id",
                (target_ids,))
            found = {int(r[0]): (r[1], r[2], r[3]) for r in cur.fetchall()}

    # ⚠ 在 report_patent_base 找不到的專利也要列入——它們同樣沒有判讀依據。
    #   略過的話會永遠停在「尚無建議」，使用者一直等。
    return [(pid, *found.get(pid, (None, None, None))) for pid in target_ids]
