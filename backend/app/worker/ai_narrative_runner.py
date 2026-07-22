"""報表解讀 headless CLI runner（把 scripts/run_narrative_task.ps1 的核心系統化）。

用途：worker 的 'ai:narrative' handler 呼叫此模組，把「組 headless CLI 提示 → 執行
`claude -p --output-format json` → 驗收 narratives.json → 觸發 --refresh-index」整條
確定性流程收進 Python，供 background worker 消費，並保留給 Patent Companion 對接。

設計重點：
- CLI 呼叫抽成可注入的 `cli_runner`（測試環境無 claude CLI，故 handler／單元測試餵 fake runner，
  不真跑二進位）。
- 雙 CLI 可換（2026-07-21 定案）：`cli_kind` 參數選 claude／opencode，預設 claude；不把
  'claude' 寫死到無法替換。指令組裝集中在 build_cli_command，換 CLI 只改此處對照表。
- narratives 落點沿用 run_narrative_task.ps1 現行落點（報表輸出目錄下 narratives.json），
  不新增落點語意；DB 敘述型回存另由 MCP save_analysis_narrative 負責，不在此重複。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

# 專案根目錄（此檔位於 backend/app/worker/ai_narrative_runner.py，往上 3 層）。
PROJECT_ROOT = Path(__file__).resolve().parents[3]
# full_report_latest 為報表引擎慣用輸出根；各版本為其下 report_trial_/analysis_ 子目錄。
FULL_REPORT_LATEST = PROJECT_ROOT / "output" / "full_report_latest"


def _resolve_skill_path() -> Path:
    """解讀規格唯一來源（prompt 模板 v2、narratives.json 契約、based_on_version 規則）。

    優先取專案內 .agents/skills；不存在時 fallback 到 workspace 根（力山工作區 .agents 集中在
    D:\\力山\\.agents，非專案子目錄，對齊 run_narrative_task.ps1 的絕對路徑）。兩者皆無時
    仍回專案內路徑（不存在），由 CLI 讀取階段自然報錯，不在匯入期硬失敗。
    """
    project_local = PROJECT_ROOT / ".agents" / "skills" / "report-narrative-flow.md"
    if project_local.exists():
        return project_local
    # fallback 到 workspace 根（力山 .agents 集中在 D:\力山\.agents，非專案子目錄）。
    # PROJECT_ROOT.parents 在容器 /app 下深度不足（index 越界會炸 import 期），故安全存取：
    # 掃各層祖先找存在的 skill，找不到就回 project_local（不存在），由 CLI 階段自然報錯。
    for ancestor in PROJECT_ROOT.parents:
        candidate = ancestor / ".agents" / "skills" / "report-narrative-flow.md"
        if candidate.exists():
            return candidate
    return project_local


SKILL_PATH = _resolve_skill_path()
# 解讀規格版本；隨 report-narrative-flow.md 模板升版而變。
PROMPT_VERSION = "report_narrative_v2"
# 預設 headless CLI 逾時（秒）；解讀多卡多變體可能久，給足時間但避免無限卡住。
DEFAULT_CLI_TIMEOUT_SECONDS = 1800.0

# 雙 CLI 指令對照（headless 非互動、只讀寫 narratives.json）。換 CLI 只改此表。
# 各值為「除提示字串外」的固定 argv 尾段；提示由 build_cli_command 插在二進位之後。
_CLI_SPECS: dict[str, dict[str, Any]] = {
    "claude": {
        "binary": "claude",
        # -p 進 headless、json 輸出、限制工具僅讀寫（對齊 run_narrative_task.ps1）。
        "prompt_flag": "-p",
        # 指定模型的旗標名（模型值由任務 payload 帶，不寫死；None＝該 CLI 不支援指定）。
        "model_flag": "--model",
        "tail_args": [
            "--output-format", "json",
            "--allowedTools", "Read", "Glob", "Grep", "Write",
        ],
    },
    "opencode": {
        # OpenCode 對接介面（Companion 雙 CLI 定案）；binary 與旗標到位時由此替換。
        "binary": "opencode",
        "prompt_flag": "run",
        "model_flag": "--model",
        "tail_args": ["--format", "json"],
    },
}


class NarrativeRunnerError(RuntimeError):
    """headless 解讀流程失敗（CLI 不存在、非零退出、產物缺失或版本不符）。"""


@dataclass
class CliResult:
    """headless CLI 一次執行的結果（供解析與回報）。"""

    exit_code: int
    stdout: str
    stderr: str


# cli_runner 介面：收 (argv, timeout) 回 CliResult；預設 subprocess 實作，測試可注入 fake。
CliRunner = Callable[[Sequence[str], float], CliResult]


def resolve_run_dir(based_on_version: str | None, *, root: Path | None = None) -> Path:
    """解析要解讀的報表版本目錄。

    based_on_version 給定時＝full_report_latest 下該版本子目錄（目錄名即版本，對齊 PS1
    Split-Path -Leaf 規則）；未給時取 full_report_latest 下最新的 report_trial_ 目錄。
    目錄須含 report_data.json 才算有效，否則 raise（不猜路徑）。
    """
    base = root if root is not None else FULL_REPORT_LATEST
    if based_on_version:
        run_dir = base / based_on_version
    else:
        candidates = sorted(
            (p for p in base.glob("report_trial_*") if (p / "report_data.json").exists()),
            key=lambda p: p.name,
        )
        if not candidates:
            raise NarrativeRunnerError(
                f"找不到可解讀的報表版本：{base} 下無含 report_data.json 的 report_trial_ 目錄"
            )
        run_dir = candidates[-1]
    if not (run_dir / "report_data.json").exists():
        raise NarrativeRunnerError(
            f"報表版本目錄無效：{run_dir} 缺 report_data.json，不是有效的報表輸出目錄"
        )
    return run_dir


def build_prompt(run_dir: Path, version: str, *, skill_path: Path | None = None) -> str:
    """組 headless 解讀任務提示：只指示 CLI 讀 skill 全文並遵守，不複製規格內文。"""
    skill = skill_path if skill_path is not None else SKILL_PATH
    narratives_path = run_dir / "narratives.json"
    return (
        "任務：產製專利報表解讀 narratives.json（系統派工、非互動、一次性，v2）。\n\n"
        f"1. 先完整閱讀 {skill} 全文，逐字遵守其中的解讀 Prompt 模板 v2、各報表解讀重點、\n"
        "   口徑守則、痛點待調查固定文案（含 {x_median} 實際值代入）與輸出契約 v2。\n"
        f"2. 目標報表目錄：{run_dir}\n"
        "3. 讀取該目錄 report_data.json：sections 鍵列出全部卡片與各卡片內的 variants（含\n"
        "   variant_key）。對每張卡片的每個變體成對讀取該變體的數據 rows 與 SVG 圖檔，\n"
        "   每一變體產一段解讀文字。\n"
        f"4. 輸出唯一檔案：{narratives_path}\n"
        f"   形狀（v2 引擎讀取契約）：based_on_version 必須等於 \"{version}\"；reports 以\n"
        "   report_key→variants→variant_key→{text,ai_model,prompt_version,generated_at} 兩層結構。\n"
        "5. 只准寫 narratives.json 這一個檔案；不得改動目錄內其他檔案、不得執行 shell 指令；\n"
        "   寫完即結束，不輸出多餘說明。"
    )


def build_cli_command(cli_kind: str, prompt: str, *, model: str | None = None) -> list[str]:
    """依 cli_kind 組 headless argv（提示插在二進位之後，其餘旗標取自對照表）。

    cli_kind 不在對照表時 raise（不默默 fallback 到 claude）；換 CLI 只改 _CLI_SPECS。
    model 給定時插入該 CLI 的 model 旗標（值由任務 payload 帶下來，不寫死於此）；
    未給則省略、用 CLI 預設模型。
    """
    spec = _CLI_SPECS.get(cli_kind)
    if spec is None:
        raise NarrativeRunnerError(
            f"未知 cli_kind：{cli_kind!r}（可用：{sorted(_CLI_SPECS)}）"
        )
    model_args: list[str] = []
    if model:
        model_flag = spec.get("model_flag")
        if not model_flag:
            raise NarrativeRunnerError(f"{cli_kind!r} 不支援指定 model")
        model_args = [model_flag, model]
    return [spec["binary"], spec["prompt_flag"], prompt, *model_args, *spec["tail_args"]]


def parse_cli_result(result: CliResult) -> dict[str, Any]:
    """解析 headless CLI 的 --output-format json 輸出。

    退出碼非 0 直接 raise（附 stderr）；stdout 應為單一 JSON 物件，解析失敗亦 raise
    並保留原始輸出，避免無聲吞錯。
    """
    if result.exit_code != 0:
        raise NarrativeRunnerError(
            f"headless CLI 失敗（exit={result.exit_code}）：{result.stderr.strip() or result.stdout.strip()}"
        )
    text = result.stdout.strip()
    if not text:
        raise NarrativeRunnerError("headless CLI 正常結束但無 JSON 輸出")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NarrativeRunnerError(f"headless CLI 輸出非合法 JSON：{exc}；原始輸出：{text[:500]}") from exc
    if not isinstance(parsed, dict):
        raise NarrativeRunnerError(f"headless CLI 輸出 JSON 非物件：{type(parsed).__name__}")
    return parsed


def _subprocess_cli_runner(argv: Sequence[str], timeout: float) -> CliResult:
    """預設 CLI 執行：subprocess 呼叫真實二進位（測試環境不會走到，由 handler 注入 fake）。"""
    binary = argv[0]
    if shutil.which(binary) is None:
        raise NarrativeRunnerError(
            f"找不到 CLI 二進位 {binary!r}（headless 解讀需已安裝並登入該 CLI）"
        )
    try:
        completed = subprocess.run(  # noqa: S603 argv 由固定對照表組成，非使用者字串
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except OSError as exc:
        raise NarrativeRunnerError(
            f"無法啟動 CLI 二進位 {binary!r}：{type(exc).__name__}: {exc}"
        ) from exc
    return CliResult(exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def run_narrative(
    based_on_version: str | None,
    *,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: CliRunner | None = None,
    refresh_index: Callable[[Path], dict[str, Any]] | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
    root: Path | None = None,
    skill_path: Path | None = None,
) -> dict[str, Any]:
    """把報表解讀整條系統化：組提示 → 呼叫 headless CLI → 驗收產物 → refresh-index。

    cli_runner 未注入時用預設 subprocess 實作（真跑 CLI，並在二進位缺失時 raise 清楚錯誤）；
    測試／handler 可注入 fake runner 避免真跑。progress(stage, percent) 供 CLI 執行期間緩進。
    model 由任務 payload 帶下來選具體模型（如 claude-opus-4-8），未給則用 CLI 預設。
    回傳：based_on_version、narratives 檔路徑、覆蓋變體數（narrated／variants_total）與缺漏。
    """
    runner = cli_runner if cli_runner is not None else _subprocess_cli_runner
    # 預設 refresh_index 延遲 import：避免 worker 匯入即拉進整個報表引擎相依。
    if refresh_index is None:
        from backend.app.reports.chart_runner import refresh_index as _refresh
        refresh_index = _refresh

    run_dir = resolve_run_dir(based_on_version, root=root)
    version = run_dir.name
    if progress is not None:
        progress("cli_running", 30)

    prompt = build_prompt(run_dir, version, skill_path=skill_path)
    argv = build_cli_command(cli_kind, prompt, model=model)
    cli_result = runner(argv, timeout_seconds)
    parse_cli_result(cli_result)  # 退出碼／JSON 檢查；不硬用其內容，narratives.json 才是產物
    if progress is not None:
        progress("cli_running", 85)

    narratives_path = run_dir / "narratives.json"
    if not narratives_path.exists():
        raise NarrativeRunnerError(f"CLI 正常結束但未產出 {narratives_path}")
    narratives = json.loads(narratives_path.read_text(encoding="utf-8"))
    got_version = narratives.get("based_on_version")
    if got_version != version:
        raise NarrativeRunnerError(
            f"narratives.json based_on_version={got_version!r} 與目錄版本 {version!r} 不符（解讀過期）"
        )

    # 確定性程式重渲染 index（嵌入解讀）；CLI 不碰 index.html。
    refresh = refresh_index(run_dir)
    return {
        "based_on_version": version,
        "run_dir": str(run_dir),
        "narratives_path": str(narratives_path),
        "cli_kind": cli_kind,
        "prompt_version": PROMPT_VERSION,
        "narrated": refresh.get("narrated"),
        "variants_total": refresh.get("variants_total"),
        "pending": refresh.get("pending", []),
        "narratives_expired": refresh.get("narratives_expired", False),
    }
