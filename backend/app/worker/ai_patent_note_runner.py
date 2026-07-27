"""文獻備註 headless CLI runner（ai:patent_note 任務的核心）。

用途：把每件專利的**獨立項**交給 headless CLI 摘要成一段簡短的「文獻備註」，
寫回 `core_layer.patents."文獻備註"`（0032 起搬到主表；一專利一列，回寫直接 WHERE id）。

規格唯一來源：`.agents/context/patent-display-spec.md`「文獻備註（#6）」節。定案重點：

1. **來源＝獨立項**（`core_layer.patents."主權項"`，主權項即第一項獨立項），
   **不是** `abstract` 摘要欄。
2. **一律輸出繁體中文**：來源獨立項中英混雜（實測有全英文專利），不論來源語言為何，
   產出一律繁中；技術術語、型號、化學式可保留英文。
3. **字數兩層線**（2026-07-26 定案）：prompt 給 **70 字目標線**、**100 字死線**，
   程式仍以 100 字硬切保底。原本只寫「100 字以內」，模型把上限當目標寫滿，
   實測幾乎每筆都逾 100 字被截斷、斷在句中；補一條較低的目標線，讓模型有餘裕
   自己收完整句。兩個數字都不是下限——prompt 不得要求寫滿或設下限。
4. **不加來源標記、不分 AI／原生**（使用者定案）：直接寫進原生欄，不另立 label_source
   之類的 guard。這是 2026-07-17「結構型 AI 產出帶 guard」的**明示例外**，理由限
   「原生欄實務恆空」，不推及其他 AI 產出。
5. **來源無值就空著**：獨立項為空的專利根本不成批、不呼叫 CLI。

效率（使用者紅線）：
- **批次按字數切、不得按件數切**：實測獨立項中位 1,000 字、p95 2,905、最長 10,008，
  總量 231 萬字。固定件數／批遇到長獨立項會撐爆 context，故 `build_batches` 以累計
  字數為界動態成批；單筆本身超過預算者截斷到預算並獨立成批。
- **DB 寫入不得 N+1**：每批一次 `executemany`，不逐筆 UPDATE。
- **已有備註者預設跳過**：可重跑但不重複燒 token。

設計沿用 `ai_topic_label_runner`／`ai_narrative_runner`：CLI 呼叫抽成可注入的
`cli_runner`（測試餵 fake，不跑二進位），指令組裝共用 `ai_narrative_runner.build_cli_command`。
⚠ 這條線不需要網路工具，CLI 白名單維持最小權限（見 `_NOTE_TAIL_ARGS`）。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Sequence

from .ai_narrative_runner import (
    DEFAULT_CLI_TIMEOUT_SECONDS,
    CliResult,
    CliRunner,
    NarrativeRunnerError,
    _CLI_SPECS,
    _subprocess_cli_runner,
    parse_cli_result,
)


# 備註流程版本；隨 prompt 契約升版而變，寫進結果供追溯。
PROMPT_VERSION = "patent_note_v1"

# 單批送進 CLI 的獨立項字數預算（非件數）。取值理由：實測中位 1,000 字，
# 12,000 字約可裝 10 件中位長度的獨立項，仍遠低於單次 context 上限；
# 遇到 p95（2,905 字）也還能一批 4 件，不會退化成一件一批。
DEFAULT_CHAR_BUDGET = 12_000

# 落點欄與來源欄（字面值集中此處，SQL 由此拼，不散落各處）。
NOTE_COLUMN = "文獻備註"
CLAIM_COLUMN = "主權項"

# 備註字數兩層線（2026-07-26 使用者定案）：
# - NOTE_TARGET_CHARS：**給模型的目標線**。只寫死線時模型會當成目標寫滿，
#   實測 8 筆幾乎每筆逾 100 字後被硬切、斷在句中（「…第二段減小，形」）。
#   把目標壓到 70，模型有餘裕自己把最後一句收完整。
# - NOTE_MAX_CHARS：**程式的死線**。仍是硬性上限，超過即截斷（保底，非目標）。
NOTE_TARGET_CHARS = 70
NOTE_MAX_CHARS = 100

# 🔴 最小權限：本任務只需模型讀 prompt 內文並回 JSON，不讀檔、不寫檔、不上網。
# 明確不加 WebSearch／WebFetch／Read／Write，避免 CLI 白名單擴權。
_NOTE_TAIL_ARGS = ["--output-format", "json", "--allowedTools", ""]


class PatentNoteRunnerError(RuntimeError):
    """文獻備註流程失敗（CLI 產出不合契約、回吐未知 patent_id 等）。"""


def build_cli_command(cli_kind: str, prompt: str, *, model: str | None = None) -> list[str]:
    """組 headless argv；沿用 ai_narrative_runner 的 CLI 對照表，但覆寫 tail_args。

    覆寫理由：解讀任務需要 Read／Write（要落 narratives.json），本任務**不需要任何工具**——
    prompt 自帶全部輸入、輸出走 stdout。維持最小權限白名單，不讓這條線取得檔案或網路能力。
    opencode 等未提供工具白名單旗標的 CLI 沿用其原 tail_args。
    """
    spec = _CLI_SPECS.get(cli_kind)
    if spec is None:
        raise PatentNoteRunnerError(f"未知 cli_kind：{cli_kind!r}（可用：{sorted(_CLI_SPECS)}）")
    model_args: list[str] = []
    if model:
        model_flag = spec.get("model_flag")
        if not model_flag:
            raise PatentNoteRunnerError(f"{cli_kind!r} 不支援指定 model")
        model_args = [model_flag, model]
    tail = _NOTE_TAIL_ARGS if cli_kind == "claude" else list(spec["tail_args"])
    return [spec["binary"], spec["prompt_flag"], prompt, *model_args, *tail]


def build_batches(
    items: Iterable[tuple[int, str]],
    *,
    char_budget: int = DEFAULT_CHAR_BUDGET,
) -> list[list[tuple[int, str]]]:
    """把 (patent_id, 獨立項) 依**累計字數**動態成批（不按件數）。

    規則：
    - 空／全空白來源直接丟棄（來源無值就空著，不呼叫 CLI 也不寫入）。
    - 單筆本身超過 char_budget（實測最長 10,008 字）→ 截斷到 char_budget 並**獨立成批**，
      避免一筆長文把整批撐爆；截斷只影響送給模型的輸入，不改動 DB 來源資料。
    - 其餘依序累加，超過預算即另起一批。故各批件數不固定——這正是「按字數切」的表現。
    """
    batches: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_chars = 0
    for patent_id, text in items:
        claim = (text or "").strip()
        if not claim:
            continue
        if len(claim) > char_budget:
            # 超長單筆：先收掉手上這批，截斷後自成一批。
            if current:
                batches.append(current)
                current, current_chars = [], 0
            batches.append([(patent_id, claim[:char_budget])])
            continue
        if current and current_chars + len(claim) > char_budget:
            batches.append(current)
            current, current_chars = [], 0
        current.append((patent_id, claim))
        current_chars += len(claim)
    if current:
        batches.append(current)
    return batches


def build_prompt(batch: Sequence[tuple[int, str]]) -> str:
    """把一批專利的獨立項組成 headless CLI 提示。

    ⚠ 兩條 prompt 紅線都在這裡執行：
    - **繁中**：明寫「不論來源語言」，因為實測有全英文專利，不特別交代模型會跟著回英文。
    - **不湊字數**：只寫「100 字以內」「更少也可以」，**不寫**任何下限或「寫滿」字樣。

    ⚠ **本線的安全保證來自任務設計，不是 CLI 沙箱**（2026-07-23 使用者定案）：
    獨立項全文**直接內嵌在 prompt 內**，CLI 不需讀取任何檔案、不需連網即可完成任務——
    「沒必要做」比「不准做」可靠。CLI 端的權限旗標（`_NOTE_TAIL_ARGS` 的空白名單、
    Codex 的 `-s read-only` 與 config 的 `network_access`）只是額外保險，且**只在本機
    設定得了**；使用者端機器的 CLI 設定不在本專案控制範圍內，故不得把安全性寄託於此。
    """
    blocks: list[str] = []
    for patent_id, claim in batch:
        blocks.append(f"### patent_id: {patent_id}\n{claim}")
    claims_block = "\n\n".join(blocks)

    return (
        "任務：閱讀每件專利的獨立項（申請專利範圍第一項），為每件寫一段「文獻備註」\n"
        "（系統派工、非互動、一次性）。\n\n"
        "撰寫要求：\n"
        "1. 說明該專利保護的技術方案重點：解決什麼問題、用什麼結構或手段。\n"
        f"2. **一律輸出繁體中文**，不論來源語言為何——來源獨立項可能是英文或中英混雜，\n"
        "   仍必須以繁體中文書寫。技術術語、型號、化學式、代號可保留英文原文。\n"
        f"3. 長度以 {NOTE_TARGET_CHARS} 字為目標，{NOTE_MAX_CHARS} 字為絕對不可超過的上限。\n"
        "   兩個數字都是天花板而非目標——更短更好，句子夠用就停，不要為湊長度補充空話、\n"
        "   不要重複同一件事。\n"
        f"4. **最後一句必須完整**，以句號結束。超過 {NOTE_MAX_CHARS} 字的部分會被系統直接\n"
        "   截掉，寫太長會斷在句子中間變成殘句；寧可寫短、寫完整，也不要寫長被截斷。\n"
        "5. 只寫獨立項讀得出來的內容，不臆測用途、不加評價、不編造數據。\n"
        "6. 不要輸出「本專利」「本發明」以外的來源標記，也不要標註是誰產生的。\n\n"
        f"{claims_block}\n\n"
        "輸出契約：只輸出一個 JSON 物件，形狀為\n"
        '{"notes": [{"patent_id": 123, "note": "..."}, ...]}\n'
        "patent_id 必須原樣取自上方清單，不得新增、改寫或遺漏；不要輸出多餘說明文字。"
    )


def _extract_notes(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """從 headless CLI 的 JSON 輸出取出 notes 陣列。

    `claude -p --output-format json` 會把模型回覆包在 `result` 字串內，故先解外層再解內層；
    CLI 直接回契約形狀者（如 opencode 或未來變更）也一併支援，不寫死單一形狀。
    """
    candidate: Any = parsed
    if "notes" not in candidate and isinstance(candidate.get("result"), str):
        text = candidate["result"].strip()
        # 模型偶爾用 ```json 圍欄包住輸出，這裡剝掉圍欄再解析。
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        try:
            candidate = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PatentNoteRunnerError(
                f"CLI 回覆非合法 JSON：{exc}；原始輸出：{text[:500]}"
            ) from exc
    notes = candidate.get("notes") if isinstance(candidate, dict) else candidate
    if not isinstance(notes, list):
        raise PatentNoteRunnerError(f"CLI 輸出缺少 notes 陣列：{str(parsed)[:300]}")
    return [item for item in notes if isinstance(item, dict)]


class PatentNoteStore:
    """文獻備註的 DB 落點（讀候選＋批次寫回）。

    落點語意（0032 起）：文獻備註欄在 `core_layer.patents` 主表（一專利一列）。回寫直接
    `UPDATE ... WHERE id = %s`，保證命中該專利那一列、不需選 raw_record 列，也不會 UPDATE 0 列
    靜默失敗。搬主表理由（回寫可靠性）見 0032 migration 頂部說明，與 0026 主附圖同一模式。
    """

    # 讀候選：來源＝patents."主權項"（獨立項），非 abstract。
    # skip_existing 為 True 時排除**主表**已有備註者（0032 後備註在 patents，不再 JOIN
    # patent_attributes 取 latest note）——可重跑但不重複燒 token。
    READ_SQL = f"""
        SELECT p.id, p."{CLAIM_COLUMN}"
        FROM core_layer.patents p
        WHERE NULLIF(BTRIM(p."{CLAIM_COLUMN}"), '') IS NOT NULL
          AND (%(workspace_id)s::bigint IS NULL OR EXISTS (
              -- 0021：workspace 成員為 workspaces.patent_ids_json 陣列
              -- （明細表 workspace_patents 已下沉 legacy_0021），沿 workspace_queries 讀法。
              SELECT 1
              FROM app_layer.workspaces w
              JOIN LATERAL jsonb_array_elements(w.patent_ids_json) AS m(pid) ON TRUE
              WHERE w.workspace_id = %(workspace_id)s::bigint
                AND (m.pid)::bigint = p.id
          ))
          AND (NOT %(skip_existing)s OR NULLIF(BTRIM(p."{NOTE_COLUMN}"), '') IS NULL)
        ORDER BY p.id
    """

    # 批次寫回：主表一專利一列，直接 WHERE id，一次 executemany 送整批（不逐筆往返、不選 raw_record 列）。
    WRITE_SQL = f"""
        UPDATE core_layer.patents
        SET "{NOTE_COLUMN}" = %s
        WHERE id = %s
    """

    def __init__(self, connect_kwargs: dict[str, Any] | None = None) -> None:
        """保存連線參數；未給時走專案既有 connection 設定。"""
        self._connect_kwargs = connect_kwargs

    def _connect(self):
        """延遲載入 psycopg 與連線設定，避免匯入期就拉進 DB 相依。"""
        import psycopg

        from backend.app.db.connection import get_connection_kwargs

        return psycopg.connect(**(self._connect_kwargs or get_connection_kwargs()))

    def fetch(
        self,
        *,
        workspace_id: int | None,
        skip_existing: bool = True,
        limit: int | None = None,
    ) -> list[tuple[int, str]]:
        """單次查詢取回全部候選 (patent_id, 獨立項)，不逐件撈（非 N+1）。"""
        sql = self.READ_SQL + ("\n        LIMIT %(limit)s" if limit else "")
        params = {
            "workspace_id": workspace_id,
            "skip_existing": skip_existing,
            "limit": limit,
        }
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [(int(pid), claim or "") for pid, claim in rows]

    def write(self, pairs: Sequence[tuple[int, str]]) -> int:
        """批次寫回備註；一次 executemany 送整批，不逐筆 UPDATE。"""
        if not pairs:
            return 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(self.WRITE_SQL, [(note, pid) for pid, note in pairs])
            conn.commit()
        return len(pairs)


def run_patent_note(
    *,
    workspace_id: int | None = None,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: CliRunner | None = None,
    store: Any | None = None,
    char_budget: int = DEFAULT_CHAR_BUDGET,
    skip_existing: bool = True,
    limit: int | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
    patents: Any = None,
    apply_notes: Any = None,
    payload_root: Any = None,
) -> dict[str, Any]:
    """整條文獻備註流程：讀候選 → 按字數分批 → 逐批呼 CLI → 批次寫回。

    cli_runner／store 皆可注入，供測試以 fake 取代，不真跑 CLI、不碰 DB。
    每批完成即回報進度（0→100 線性推進，帶「第 n/N 批」階段文字），不留無限 spinner。
    回傳：候選數、實際寫入數、批次數與 cli_kind／prompt_version 供追溯。
    """
    from . import ai_payload_file as pf

    runner = cli_runner if cli_runner is not None else _subprocess_cli_runner
    # patents 直接給定時（測試／呼叫端已備妥資料）不建 store，避免無謂連線。
    note_store = store if store is not None else (
        None if patents is not None else PatentNoteStore())
    pf.cleanup_old_payloads(root=payload_root)

    if progress is not None:
        progress("讀取待產生文獻備註的專利", 5)
    candidates = list(patents) if patents is not None else note_store.fetch(
        workspace_id=workspace_id, skip_existing=skip_existing, limit=limit
    )
    batches = build_batches(candidates, char_budget=char_budget)
    if not batches:
        if progress is not None:
            progress("沒有待產生文獻備註的專利", 100)
        return {
            "workspace_id": workspace_id,
            "candidates": len(candidates),
            "batches": 0,
            "notes_written": 0,
            "cli_kind": cli_kind,
            "prompt_version": PROMPT_VERSION,
        }

    total_batches = len(batches)
    written = 0
    for index, batch in enumerate(batches, start=1):
        if progress is not None:
            # 5→95 之間線性推進：每批一格，看得到動、也留尾巴給收尾。
            percent = 5 + int(90 * (index - 1) / total_batches)
            progress(f"產生文獻備註（第 {index}/{total_batches} 批，{len(batch)} 件）", percent)

        known_ids = {pid for pid, _ in batch}
        # 資料走檔案不走命令列（2026-07-27）：主權項全文塞 argv 在 Windows
        # （CreateProcess 上限 32,767）會 WinError 206；本支雖已按 CHAR_BUDGET 分批、
        # 目前尚未超標，但與 topic_label 同屬「靠剛好塞得下」的脆弱設計，一併收斂。
        path = pf.write_payload_file(
            "patent_note",
            {
                "instruction": (
                    f"為每一筆專利的主權項產生繁體中文文獻備註，"
                    f"目標約 {NOTE_TARGET_CHARS} 字、不得超過 {NOTE_MAX_CHARS} 字。"
                ),
                "output_contract": {"notes": [{"patent_id": 0, "note": ""}]},
                "items": [{"patent_id": pid, "claim": text} for pid, text in batch],
            },
            root=payload_root,
            label=f"batch{index:02d}",
        )
        argv = pf.build_cli_command_with_payload(
            cli_kind,
            instruction="任務：為專利主權項產生文獻備註（系統派工、非互動、一次性）。",
            payload_path=path,
            model=model,
        )
        parsed = parse_cli_result(runner(argv, timeout_seconds))

        pairs: list[tuple[int, str]] = []
        for item in _extract_notes(parsed):
            try:
                patent_id = int(item.get("patent_id"))
            except (TypeError, ValueError) as exc:
                raise PatentNoteRunnerError(
                    f"CLI 產出 patent_id 非整數：{item.get('patent_id')!r}"
                ) from exc
            if patent_id not in known_ids:
                # 幻覺 patent_id 直接失敗，不把不存在的備註寫進正式資料。
                raise PatentNoteRunnerError(
                    f"CLI 產出未知 patent_id：{patent_id}（本批：{sorted(known_ids)}）"
                )
            note = str(item.get("note") or "").strip()
            if not note:
                continue
            # 上限保底：模型偶爾超寫，截到上限即可（上限不是目標，不補足下限）。
            pairs.append((patent_id, note[:NOTE_MAX_CHARS]))

        # 每批一次批次寫入（executemany），不逐筆 UPDATE。
        if apply_notes is not None:
            written += int(apply_notes(pairs=pairs).get("updated", 0))
        else:
            written += note_store.write(pairs)

    if progress is not None:
        progress(f"文獻備註完成（共 {written} 件）", 100)
    return {
        "workspace_id": workspace_id,
        "candidates": len(candidates),
        "batches": total_batches,
        "notes_written": written,
        "cli_kind": cli_kind,
        "prompt_version": PROMPT_VERSION,
    }
