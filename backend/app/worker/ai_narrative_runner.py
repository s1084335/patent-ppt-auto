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
import os
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
    """解讀規格來源（prompt 模板、narratives.json 契約、based_on_version 規則）。

    預設只取專案 repo 內 `skills/patent-report-ppt/report-narrative-flow.md`。正式部署若把
    規格掛載到其他位置，可用 `REPORT_NARRATIVE_FLOW_PATH` 覆寫。不得 fallback 到本機
    `.agents`；舊規格檔會掩蓋 Docker／公司伺服器缺檔問題。
    """
    configured = os.environ.get("REPORT_NARRATIVE_FLOW_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return PROJECT_ROOT / "skills" / "patent-report-ppt" / "report-narrative-flow.md"


SKILL_PATH = _resolve_skill_path()
# 解讀規格版本；隨 report-narrative-flow.md 模板升版而變。
# v3（2026-07-27）：prompt 納入使用者 instruction（原本 payload 有存但零消費）。
PROMPT_VERSION = "report_narrative_v3"
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


def materialize_report_version(version: str) -> Path:
    """把 DB 內的報表版本落地到本機暫存目錄，回傳該目錄（跨容器讀那一段）。

    延遲 import：worker 匯入本模組時不必拉進 DB 層；且測試可直接 patch 本函式。
    落點放 var/report_cache，與 ai_payloads 同層（皆為本機暫存，不進版控）。
    """
    from backend.app.db.report_artifact_store import materialize_version

    return materialize_version(version, PROJECT_ROOT / "var" / "report_cache")


def resolve_run_dir(based_on_version: str | None, *, root: Path | None = None) -> Path:
    """解析要解讀的報表版本目錄；本機沒有時從 DB 落地（2026-07-27 待辦 9d）。

    based_on_version 給定時＝full_report_latest 下該版本子目錄（目錄名即版本，對齊 PS1
    Split-Path -Leaf 規則）；未給時取 full_report_latest 下最新的 report_trial_ 目錄。

    ⚠ **本機優先、DB 補位**：報表由容器內 worker 產出、只存在 report_artifacts 表，
    而本函式在使用者本機 Companion 執行——只找本機必然落空（實機 job 95 即此，
    解讀從來沒成功過）。故本機目錄不存在時改從 DB 落地整包再讀。
    本機開發（backend 與報表同一台）時目錄真的存在，走原路徑、不繞 DB。
    """
    base = root if root is not None else FULL_REPORT_LATEST
    if based_on_version:
        run_dir = base / based_on_version
        if not (run_dir / "report_data.json").exists():
            # 本機沒有＝報表在容器裡產的，改從 report_artifacts 落地。
            try:
                return materialize_report_version(based_on_version)
            except Exception as exc:  # noqa: BLE001 - 兩邊都沒有才是真的找不到
                raise NarrativeRunnerError(
                    f"找不到報表版本 {based_on_version}：本機 {run_dir} 無 report_data.json，"
                    f"DB report_artifacts 也取不到（{type(exc).__name__}: {exc}）"
                ) from exc
        return run_dir
    # 未指定版本：本機取最新；本機一份都沒有時（容器產的報表）改問 DB 要最新版本。
    candidates = sorted(
        (p for p in base.glob("report_trial_*") if (p / "report_data.json").exists()),
        key=lambda p: p.name,
    )
    if candidates:
        return candidates[-1]
    try:
        from backend.app.db.report_artifact_store import list_versions

        versions = list_versions()   # 已依 version DESC 排序，且只含有 report_data.json 者
        if versions:
            return materialize_report_version(versions[0]["version"])
    except Exception as exc:  # noqa: BLE001 - DB 取不到就落到下面的統一錯誤
        raise NarrativeRunnerError(
            f"找不到可解讀的報表版本：本機 {base} 無 report_trial_ 目錄，"
            f"DB report_artifacts 也取不到（{type(exc).__name__}: {exc}）"
        ) from exc
    raise NarrativeRunnerError(
        f"找不到可解讀的報表版本：本機 {base} 與 DB report_artifacts 都沒有已產製的報表。"
        "請先在「報表種類」頁按「產製選定報表」。"
    )


def build_prompt(
    run_dir: Path,
    version: str,
    *,
    skill_path: Path | None = None,
    instruction: str | None = None,
    report_keys: list[str] | None = None,
) -> str:
    """組 headless 解讀任務提示：只指示 CLI 讀 skill 全文並遵守，不複製規格內文。

    instruction＝使用者在報表旁「重產解讀」時輸入的附加需求（可為空）。
    2026-07-27 前 payload 有存但這裡零消費，使用者打了完全沒作用——
    比失敗更誤導（看似成功卻沒照要求做），故納入 prompt。
    附加需求**不得凌駕輸出契約**：仍只寫 narratives.json、維持 v2 兩層結構。

    report_keys＝只重產這幾張報表的解讀（2026-07-29 使用者定案「報表要能各自
    獨立重產解釋」）。不給＝整份重跑（原行為）。
    ⚠ 限定範圍時**必須明確要求保留其他報表的既有解讀**——否則 CLI 會寫出只含
    這幾張的 narratives.json，其餘解讀全部消失，且檔案結構合法、驗不出來
    （靜默資料損失）。
    """
    skill = skill_path if skill_path is not None else SKILL_PATH
    narratives_path = run_dir / "narratives.json"
    scope = ""
    if report_keys:
        listed = "、".join(str(k) for k in report_keys)
        scope = (
            f"\n\n**本次只重產這幾張報表的解讀**：{listed}\n"
            f"   ⚠ {narratives_path} 內**其他報表的既有解讀必須原樣保留**：\n"
            "   先讀入現有檔案，只替換上列 report_key 的內容，其餘鍵值不得刪除或改寫。\n"
            "   （寫成只含本次範圍的檔案＝其他解讀全部遺失，且檔案結構仍合法、驗不出來。）"
        )
    extra = ""
    if instruction and instruction.strip():
        extra = (
            "\n\n6. 使用者額外需求（在遵守上述契約的前提下盡量滿足）：\n"
            f"   「{instruction.strip()}」\n"
            "   注意：此需求**不得**牴觸或覆蓋第 4、5 點的輸出契約：仍只寫 narratives.json\n"
            "   這一個檔案、維持 v2 兩層結構、不得改動其他檔案。若需求與契約衝突，\n"
            "   以契約為準，並在對應解讀文字中說明無法滿足的部分。"
        )
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
        + scope
        + extra
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
    # 🔴 stdout／stderr 可能是 None（2026-07-30 實機 job #132 failed）：
    # 子程序輸出未被捕捉時 subprocess 會給 None，直接 `.strip()` 會拋
    # `AttributeError: 'NoneType' object has no attribute 'strip'`——
    # ⚠ 那個例外**逃出 NarrativeRunnerError**，畫面只顯示裸的 AttributeError，
    # 完全看不出是哪個環節、哪個變數，比明確的錯誤更難查。
    # ⚠ `_default_build_ppt` 已於 6f50611 加過同樣防護，但這裡漏了——
    # 同一個坑在相鄰模組各補各的，故兩處都要有。
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if result.exit_code != 0:
        detail = stderr.strip() or stdout.strip() or "stdout/stderr 皆為空"
        raise NarrativeRunnerError(
            f"headless CLI 失敗（exit={result.exit_code}）：{detail}"
        )
    text = stdout.strip()
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
    instruction: str | None = None,
    # 只重產這幾張報表的解讀（2026-07-29）；不給＝整份重跑。
    report_keys: list[str] | None = None,
    resolve_run_dir: Callable[..., Path] | None = None,
    upload_run_dir: Callable[[Path], int] | None = None,
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

    # 參數同名遮蔽了模組層函式，故用 globals() 取預設實作（測試可注入 fake）。
    resolver = resolve_run_dir or globals()["resolve_run_dir"]
    run_dir = resolver(based_on_version, root=root)
    version = run_dir.name
    if progress is not None:
        progress("cli_running", 30)

    prompt = build_prompt(run_dir, version, skill_path=skill_path,
                          instruction=instruction, report_keys=report_keys)
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

    # ⚠ 把 narratives.json 上傳回 report_artifacts（2026-07-27 待辦 9d 的「寫」那一段）。
    # CLI 寫的是**本機檔案系統**，但 backend 從 DB 讀（report_artifact_store.read_file）——
    # 不傳回去就永遠讀不到，解讀區維持空白。upload_run_dir 會整包 upsert（同版本同名
    # 檔覆蓋），故也順帶把 refresh_index 重渲染的 index.html 一起更新。
    # 上傳失敗不可吞：backend 讀的是 DB report_artifacts。若 narratives.json 沒進 DB，
    # 使用者看到的是「job succeeded 但完全沒有解讀」，比 failed 更難判斷。
    uploader = upload_run_dir
    if uploader is None:
        from backend.app.db.report_artifact_store import upload_run_dir as _upload
        uploader = _upload
    uploaded = 0
    try:
        uploaded = uploader(run_dir)
    except Exception as exc:  # noqa: BLE001 - 對使用者必須 fail loud
        raise NarrativeRunnerError(
            f"narratives.json 已產生但上傳 report_artifacts 失敗："
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if uploaded <= 0:
        raise NarrativeRunnerError(
            "narratives.json 已產生但沒有任何 report artifact 被上傳；"
            "backend 將讀不到解讀。"
        )

    return {
        "artifacts_uploaded": uploaded,
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
