"""AI 任務資料檔共用核心：把大量資料落檔給 CLI 讀，不塞命令列。

## 為什麼需要這個模組

`ai:topic_label` 在本機 Companion 執行時 FileNotFoundError [WinError 206]。
實測 prompt 達 **128,101 字元**，而 Windows `CreateProcess` 的命令列上限是
**32,767**（含結尾 null）——超標 3.9 倍。

關鍵在於這不是「調參數就好」：
- Linux 上限約 2MB，容器內不會發生；但 AI 任務依架構定案**只由本機 Companion
  領取**（要使用者自己的 Claude CLI 登入態），必定在 Windows 上跑，必定超標。
- 縮小批次只是治標：主題數、專利獨立項長度都是變數，下一批照樣爆，
  而錯誤碼 206（ERROR_FILENAME_EXCED_RANGE，訊息為「檔名或副檔名太長」）
  與真因完全對不上，每次都要重查一輪。

故資料改走檔案，CLI 以 Read 讀取。此模式在本專案已有先例——
`ai_narrative_runner` 一直是「命令列只給報表目錄路徑，CLI 自己讀 report_data.json」。

## 定案（使用者 2026-07-27）

- 權限：從「零工具」放寬到**只有 Read**；不得寫檔、不得執行指令、不得上網。
  這是為了讀資料檔的最小必要放寬，原 🔴 最小權限精神仍在。
- 落點：`var/ai_payloads/<任務類型>/`，集中管理不散落到系統 temp。
  不用系統 temp 的理由：位置不可控（Windows／容器／不同使用者各異）、
  權限較寬鬆、且 CLI 逾時或行程被 kill 時 finally 跑不到會永久殘留。
- 保留 **7 天**（策略 A）後自動清理：出問題通常當天或隔天發現，7 天夠回頭查
  「AI 到底看到什麼」；每天數 MB，一週數十 MB 可接受。
- 檔名帶 run_id 與時間戳：可直接對到那筆 job，且並行不互相覆蓋。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

# 專案根（此檔位於 backend/app/worker/，往上 3 層）
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 資料檔集中落點。可用 AI_PAYLOAD_DIR 覆蓋（例如日後 AI 任務改由中央執行時搬家），
# 但**只影響本模組**，不動其他既有環境變數的語意。
DEFAULT_PAYLOAD_ROOT = PROJECT_ROOT / "var" / "ai_payloads"

# 保留天數（策略 A）。超過即在下次寫檔前一併清掉。
RETENTION_DAYS = 7

# 單批資料上限（字元）。
#
# ⚠ 分批的理由**不是** context window 塞不下——Claude Opus 5 的 context 是
# 1,000,000 tokens（約 150–200 萬中文字元），150KB 連 0.05% 都不到。真正的理由有三：
# 1. **輸出上限**：輸入吃得下，但單次回應上限 128,000 tokens；主題多時
#    label＋summary 的總輸出會逼近。
# 2. **品質**：一次讀數十篇專利獨立項再歸納數十個主題，注意力會分散；
#    分批讓模型專注在較少主題上。
# 3. **失敗隔離**：一批失敗只重跑那批，不必整份重來。
#
# 150KB（2026-07-27 使用者定）：118 筆專利／10 主題×5 篇＝128KB 落在單批內，
# 現行資料量不觸發分批；5000 筆／40 主題約 512KB → 約 4 批。
MAX_PAYLOAD_CHARS = 150_000

# CLI 讀資料檔所需的最小權限：只有 Read。
# 不給 Write（產物由 stdout JSON 回傳，不需 CLI 寫檔）、
# 不給 Bash／WebFetch（避免專利文字內的 prompt injection 取得執行或連外能力）。
READ_ONLY_TOOLS = "Read"


def payload_root(root: Path | None = None) -> Path:
    """解析資料檔根目錄，優先序：參數 > AI_PAYLOAD_DIR > AI_BRIDGE_STATE_DIR/ai_payloads
    > 專案 var/ai_payloads。

    路徑一律推導、**不寫死任何磁碟位置或使用者名稱**——程式安裝到哪就解到哪
    （開發機 `D:\\...\\var\\ai_payloads`、容器 `/app/var/ai_payloads`）。

    為何要吃 `AI_BRIDGE_STATE_DIR`：Companion 已用該變數把狀態與日誌指到使用者
    可寫的位置（例如 %LOCALAPPDATA%），因為 Installer 可能裝在 Program Files 這種
    一般使用者不可寫的目錄。資料檔跟著同一個狀態目錄走，Installer 只需設一個變數，
    不必再記第二個；仍保留 AI_PAYLOAD_DIR 供單獨改落點（例如日後 AI 任務改由中央執行）。
    """
    if root is not None:
        return Path(root)
    env = os.getenv("AI_PAYLOAD_DIR")
    if env:
        return Path(env).expanduser().resolve()
    state_dir = os.getenv("AI_BRIDGE_STATE_DIR")
    if state_dir:
        return Path(state_dir).expanduser().resolve() / "ai_payloads"
    return DEFAULT_PAYLOAD_ROOT


def write_payload_file(
    task_type: str,
    data: dict[str, Any],
    *,
    root: Path | None = None,
    run_id: int | None = None,
    label: str | None = None,
) -> Path:
    """把任務資料寫成 UTF-8 JSON，回傳檔案路徑。

    檔名格式：`{label}_{時間戳}_run{run_id}_{短亂數}.json`
    - run_id：出問題時能直接對到那筆 workflow_run；
    - 時間戳：排序與保留期判斷；
    - 短亂數：同一 run 分多批（如 patent_note）時不互相覆蓋。

    ensure_ascii=False 讓中文原樣落檔，CLI 讀到的與 DB 內容一致，也便於人工檢視。
    """
    directory = payload_root(root) / task_type
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    parts = [p for p in (label, stamp, f"run{run_id}" if run_id is not None else None) if p]
    name = "_".join(parts) + f"_{uuid4().hex[:6]}.json"
    path = directory / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def cleanup_old_payloads(*, root: Path | None = None, retention_days: int = RETENTION_DAYS) -> int:
    """刪除超過保留期的資料檔，回傳刪除數。

    在寫新檔時順帶呼叫即可，不另設排程——AI 任務本來就會週期性執行。
    刪不掉（檔案被佔用等）只跳過，不讓清理失敗影響任務本體。
    """
    base = payload_root(root)
    if not base.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for path in base.rglob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def split_into_batches(
    items: list[Any],
    *,
    max_chars: int = MAX_PAYLOAD_CHARS,
    size_of: Any = None,
) -> list[list[Any]]:
    """依字元預算把項目切批，回傳批次清單（至少一批，空輸入回空清單）。

    單一項目本身就超過預算時**獨立成一批**（不截斷、不丟棄）——寧可那批大一點，
    也不要悄悄少給 AI 資料而讓它憑不完整的內容作判斷。

    size_of 未給時以 JSON 序列化長度估算，與實際落檔大小一致。
    """
    if not items:
        return []
    measure = size_of or (lambda x: len(json.dumps(x, ensure_ascii=False)))
    batches: list[list[Any]] = []
    current: list[Any] = []
    current_size = 0
    for item in items:
        size = measure(item)
        if current and current_size + size > max_chars:
            batches.append(current)
            current, current_size = [], 0
        current.append(item)
        current_size += size
    if current:
        batches.append(current)
    return batches


def build_cli_command_with_payload(
    cli_kind: str,
    *,
    instruction: str,
    payload_path: Path,
    model: str | None = None,
) -> list[str]:
    """組 headless argv：命令列只帶「短指示 + 檔案路徑」，資料本體不進 argv。

    這是本模組的重點——不論資料多大（實測 128K 亦然），argv 都維持數百字元，
    永遠不會撞上 Windows 32,767 的命令列上限。

    工具權限固定為 Read（見 READ_ONLY_TOOLS 的說明）；不沿用各 runner 原本的
    tail_args，避免又出現「同一件事多個落點」。
    """
    from .ai_narrative_runner import NarrativeRunnerError, _CLI_SPECS

    spec = _CLI_SPECS.get(cli_kind)
    if spec is None:
        raise NarrativeRunnerError(
            f"未知 cli_kind：{cli_kind!r}（可用：{sorted(_CLI_SPECS)}）"
        )
    prompt = (
        f"{instruction}\n\n"
        f"資料檔（JSON，UTF-8）：{payload_path}\n"
        "請先用 Read 讀取該檔，依其中 instruction 與 output_contract 欄位作業。\n"
        "只輸出契約指定的 JSON 物件，不要輸出多餘說明，也不要修改任何檔案。"
    )
    model_args: list[str] = []
    if model:
        model_flag = spec.get("model_flag")
        if not model_flag:
            raise NarrativeRunnerError(f"{cli_kind!r} 不支援指定 model")
        model_args = [model_flag, model]
    return [
        spec["binary"], spec["prompt_flag"], prompt, *model_args,
        "--output-format", "json",
        "--allowedTools", READ_ONLY_TOOLS,
    ]
