"""報告 PPT 產製 headless CLI runner（ai:report_ppt 任務的核心）。

用途：把 SKILL.md 步驟 A-D 的匯出報告流程程式化——
  A/B/C（AI 產文案 slots）→ 寫 approvals.json → D（CLI 順手呼 deterministic 的
  build_ppt.py 組版）→ .pptx 進 report_artifacts（跨容器：本機檔案系統不通，必須進 DB）。

規格唯一來源：`.agents/context/export-report-flow-spec.md`（架構、字體、分工、同列並排定案）
與 `.agents/skills/patent-report-ppt/SKILL.md`（步驟 A-D）。

⚠ 接線非重寫（使用者定案）：
- **組版沿用既有 build_ppt.py**（skill 目錄的 `scripts/build_ppt.py`），本 runner 不在
  backend 重寫一份組版邏輯；CLI 順手組沿用它的獨立執行方式（uv run --no-project）。
- **slot 命名取自 build_ppt.py 的 all_slot_keys()**（PAGE_LAYOUT 唯一來源），runner 不另定
  一套槽名，避免產的槽與組版讀的槽對不上。
- **報表版本目錄解析沿用 ai_narrative_runner.resolve_run_dir**（同一套 report_trial_ 命名）。
- **.pptx 存取沿用 report_artifact_store.upload_run_dir**，不自造新表或新檔存取。

⚠ 分工紅線（export-report-flow-spec.md 第二節）：AI 只產文案 slots 草稿、**不碰排版、
  不碰數字**；build_ppt.py deterministic 把「已確認文案 + 引擎 report_data 數據 + 圖」組成
  .pptx。全庫也能產 PPT（build_ppt 對全庫不設限，只市場章節第 7/9/10 頁在全庫空著）。

⚠ 安全來自任務設計（沿市場摘要／文獻備註線）：報表數據由 report_data.json 摘要後內嵌 prompt，
  CLI 不需 Read 檔案、不需連網——CLI 白名單為空（`_PPT_TAIL_ARGS`）。

設計沿用 ai_market_summary_runner：CLI 呼叫、build_ppt、upload、resolve 皆可注入，測試餵 fake，
不跑二進位、不燒 token、不真碰 DB／檔案系統。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from .ai_narrative_runner import (
    DEFAULT_CLI_TIMEOUT_SECONDS,
    PROJECT_ROOT,
    CliRunner,
    _CLI_SPECS,
    _subprocess_cli_runner,
    parse_cli_result,
    resolve_run_dir as _default_resolve_run_dir,
)
from .ai_payload_file import extract_json_payload


# 報告 PPT 流程版本；隨 prompt 契約／版型升版而變，寫進結果供追溯。
PROMPT_VERSION = "report_ppt_v1"

# 🔴 最小權限：報表數據內嵌 prompt，CLI 不需讀檔、不寫檔、不上網。
# 明確不加 WebSearch／WebFetch／Read／Glob／Grep／Write（沿市場摘要線的空白名單設計）。
_PPT_TAIL_ARGS = ["--output-format", "json", "--allowedTools", ""]

# 報表數據內嵌 prompt 的字數上限：避免超長 report_data.json 撐爆 context。
DEFAULT_REPORT_DATA_CHAR_LIMIT = 20_000


class ReportPptRunnerError(RuntimeError):
    """報告 PPT 流程失敗（CLI 產出不合契約、build_ppt 未產檔等）。"""


def _resolve_skill_dir() -> Path:
    """定位 patent-report-ppt skill 目錄（含 scripts/build_ppt.py、theme.json）。

    沿 ai_narrative_runner._resolve_skill_path 的作法：優先專案內 .agents/skills，
    不存在時掃各層祖先（力山 .agents 集中在 D:\\力山\\.agents，非專案子目錄）。
    找不到就回專案內路徑（不存在），由讀取階段自然報錯，不在匯入期硬失敗。
    """
    project_local = PROJECT_ROOT / ".agents" / "skills" / "patent-report-ppt"
    if (project_local / "scripts" / "build_ppt.py").exists():
        return project_local
    for ancestor in PROJECT_ROOT.parents:
        candidate = ancestor / ".agents" / "skills" / "patent-report-ppt"
        if (candidate / "scripts" / "build_ppt.py").exists():
            return candidate
    return project_local


SKILL_DIR = _resolve_skill_dir()
BUILD_PPT_PATH = SKILL_DIR / "scripts" / "build_ppt.py"
THEME_PATH = SKILL_DIR / "theme.json"


def _load_builder():
    """以檔案路徑載入 skill 內的 build_ppt 模組（同 test_ppt_builder 的載入方式）。

    build_ppt.py 為可攜獨立腳本、不在主專案 import 路徑；本函式只為取用其
    all_slot_keys()／write_approval_template()／build_ppt()，不重寫組版邏輯。
    """
    spec = importlib.util.spec_from_file_location("build_ppt", BUILD_PPT_PATH)
    if spec is None or spec.loader is None:
        raise ReportPptRunnerError(f"找不到組版程式 build_ppt.py：{BUILD_PPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_ppt"] = module
    spec.loader.exec_module(module)
    return module


def report_slot_keys() -> list[str]:
    """回傳報告 PPT 的全部確認槽鍵（唯一來源＝build_ppt.py 的 PAGE_LAYOUT）。

    runner 產文案就照這組槽名，不自己另定一套（接線非重寫）。build_ppt 載不到時
    fallback 到 SKILL.md 步驟 D 列出的槽位契約，避免匯入期硬失敗。
    """
    try:
        return list(_load_builder().all_slot_keys())
    except Exception:
        # build_ppt 暫時載不到（部署缺 python-pptx 等）：用 SKILL.md D-2 的槽位契約保底。
        return [
            "cover.title", "direction.body", "trend.narrative", "tech.narrative",
            "competitor.narrative", "opportunity.narrative", "pain_point.narrative",
            "key_players.market", "market.scope", "market.size",
        ]


def build_cli_command(cli_kind: str, prompt: str, *, model: str | None = None) -> list[str]:
    """組 headless argv；沿用 ai_narrative_runner 的 CLI 對照表，覆寫 tail_args 為空白名單。

    覆寫理由：報表數據自帶在 prompt 內，CLI **不需要任何工具**（不讀檔、不連網）。
    opencode 等未提供工具白名單旗標的 CLI 沿用其原 tail_args。
    """
    spec = _CLI_SPECS.get(cli_kind)
    if spec is None:
        raise ReportPptRunnerError(
            f"未知 cli_kind：{cli_kind!r}（可用：{sorted(_CLI_SPECS)}）")
    model_args: list[str] = []
    if model:
        model_flag = spec.get("model_flag")
        if not model_flag:
            raise ReportPptRunnerError(f"{cli_kind!r} 不支援指定 model")
        model_args = [model_flag, model]
    tail = _PPT_TAIL_ARGS if cli_kind == "claude" else list(spec["tail_args"])
    return [spec["binary"], spec["prompt_flag"], prompt, *model_args, *tail]


def build_prompt(report_data_text: str, slot_keys: list[str]) -> str:
    """組報告 PPT 文案任務提示：報表數據內嵌，AI 只產文案 slots。

    ⚠ 分工紅線在此明寫：AI 只產各槽的敘述文案、**不碰排版、不碰數字**；排版由
      deterministic 的 build_ppt.py 組，數字一律取自引擎 report_data，AI 不推算不捏造。
    ⚠ 安全來自任務設計：報表數據直接內嵌下方，CLI 不需讀檔／連網（白名單為空）。
    """
    slots_block = "\n".join(f"- {key}" for key in slot_keys)
    return (
        "任務：為專利分析報告 PPT 產出各頁的敘述文案草稿（系統派工、非互動、一次性）。\n\n"
        "── 分工鐵律（務必遵守）──\n"
        "1. 你**只產文案**（各確認槽的敘述／解讀文字）。**不碰排版、不碰版型、不排版面**——"
        "PPTX 由確定性程式（build_ppt.py）依固定版型組出，你的文案只是填進既有版型的內容。\n"
        "2. **不捏造數字**：所有數字一律以下方 report_data 為準；report_data 沒有的數字不得"
        "自行推算或編造，寧可寫質性描述。**不碰數字的正確性**＝不改寫、不四捨五入到失真、"
        "不無中生有。\n"
        "3. 一律繁體中文；每個槽一段精煉文案，附數字依據（若該槽有對應數據）。\n\n"
        "── 需產出的確認槽（slot key，槽名固定、不可更改）──\n"
        f"{slots_block}\n\n"
        "── 報表結構化數據（report_data 摘要，唯一數字來源）──\n"
        f"{report_data_text}\n\n"
        "── 輸出契約 ──\n"
        "只輸出一個 JSON 物件，形狀為\n"
        '{"slots": {"cover.title": "...", "trend.narrative": "...", ...}}\n'
        "- key 必須是上面列出的 slot key（原字不變）；value 為該槽的繁中文案。\n"
        "- 查無對應數據的槽可留空字串或省略（該頁組版時會標「待確認」浮水印，不擋產出）。\n"
        "不要輸出多餘說明文字。"
    )


def summarize_report_data(report_dir: Path, *, char_limit: int = DEFAULT_REPORT_DATA_CHAR_LIMIT) -> str:
    """把報表版本目錄的 report_data.json 讀成內嵌 prompt 的文字（截斷避免撐爆 context）。

    只讀既有產物、不改寫；缺檔時回明確缺漏說明，讓 AI 知道數據不足而非硬掰。
    """
    path = report_dir / "report_data.json"
    if not path.exists():
        return "（無 report_data.json，報表數據不足）"
    text = path.read_text(encoding="utf-8")
    return text[:char_limit]


def _extract_slots(parsed: dict[str, Any]) -> dict[str, str]:
    """從 headless CLI 的 JSON 輸出取出 {slot_key: text}。

    `claude -p --output-format json` 把模型回覆包在 `result` 字串內，先解外層再解內層；
    CLI 直接回契約形狀者也一併支援（不寫死單一形狀）。
    """
    candidate: Any = parsed
    has_contract = isinstance(candidate, dict) and "slots" in candidate
    if not has_contract and isinstance(candidate.get("result"), str):
        text = candidate["result"].strip()
        # 取 JSON 收口在 ai_payload_file.extract_json_payload（2026-07-27 實機 9g）：
        # 原本只認「開頭就是 ```」，CLI 多一句開場白（「依契約輸出：」「以下為契約
        # 指定的 JSON 物件：」）就整段丟 json.loads 而炸——job 102 跑了 183 秒、
        # 第一批已落庫，仍因此整趟報 failed。共用函式容忍前後贅字，七支 runner 同一份。
        try:
            candidate = extract_json_payload(text)
        except ValueError as exc:
            raise ReportPptRunnerError(str(exc)) from exc
    if not isinstance(candidate, dict):
        raise ReportPptRunnerError(f"CLI 輸出非 JSON 物件：{str(parsed)[:300]}")
    slots = candidate.get("slots")
    if slots is None:
        return {}
    if not isinstance(slots, dict):
        raise ReportPptRunnerError(f"CLI 產出 slots 型別非物件：{type(slots).__name__}")
    # 只保留字串值，過濾非文字（AI 不碰數字型結構，槽一律文案）。
    return {str(k): str(v) for k, v in slots.items() if v is not None}


def _default_build_ppt(*, report_dir, approvals_path, output_dir, theme_path=None):
    """預設組版：以獨立子行程呼 skill 的 build_ppt.py（uv run --no-project，可攜）。

    沿 SKILL.md D-3 的獨立執行方式，不 import build_ppt 進 backend、不重寫組版邏輯。
    子行程失敗時 raise，附 stderr 供追溯。測試會注入 fake build_ppt，不走到這裡。
    """
    argv = [
        "uv", "run", "--no-project",
        "--with", "python-pptx", "--with", "pymupdf", "--python", "3.12",
        "python", str(BUILD_PPT_PATH),
        "--report-dir", str(report_dir),
        "--approvals", str(approvals_path),
        "--output-dir", str(output_dir),
    ]
    completed = subprocess.run(  # noqa: S603 argv 由固定值組成，非使用者字串
        argv, capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        raise ReportPptRunnerError(
            f"build_ppt 子行程失敗（exit={completed.returncode}）："
            f"{completed.stderr.strip() or completed.stdout.strip()}")
    # 解析 build_ppt 印出的 pptx 路徑（"pptx: <path>"）。
    pptx_path = None
    for line in completed.stdout.splitlines():
        if line.startswith("pptx:"):
            pptx_path = line.split(":", 1)[1].strip()
    if not pptx_path:
        raise ReportPptRunnerError(f"build_ppt 未回報 pptx 路徑；輸出：{completed.stdout[:500]}")
    return {"pptx_path": pptx_path, "manifest_path": "", "manifest": {}}


def run_report_ppt(
    based_on_version: str | None,
    *,
    workspace_id: int | None = None,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: CliRunner | None = None,
    resolve_run_dir: Callable[..., Path] | None = None,
    build_ppt: Callable[..., dict[str, Any]] | None = None,
    upload_run_dir: Callable[[Path], int] | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """整條報告 PPT 流程：解析報表目錄 → AI 產文案 slots → 寫 approvals.json →
    呼 build_ppt.py 組 .pptx（進 report_dir）→ upload_run_dir 一起上傳到 report_artifacts。

    ⚠ 全庫也能產（不因 workspace_id 是全庫而 raise，與市場摘要不同）；市場章節缺數據的頁
      由 build_ppt 自動標「待確認」浮水印，不擋整檔產出。
    ⚠ 分工：AI 只產 slots 文案（不碰排版／數字）；組版一律 deterministic build_ppt。

    cli_runner／resolve_run_dir／build_ppt／upload_run_dir 皆可注入，供測試以 fake 取代，
    不跑二進位／不燒 token／不真碰 DB。每階段回報進度（0→100），不留無限 spinner。
    回傳含 pptx_filename（進 artifact 的檔名）供前端下載路由組 URL。
    """
    resolver = resolve_run_dir if resolve_run_dir is not None else _default_resolve_run_dir
    runner = cli_runner if cli_runner is not None else _subprocess_cli_runner
    builder = build_ppt if build_ppt is not None else _default_build_ppt
    uploader = upload_run_dir
    if uploader is None:
        from backend.app.db.report_artifact_store import upload_run_dir as _upload
        uploader = _upload

    if progress is not None:
        progress("解析報表版本目錄", 10)
    run_dir = resolver(based_on_version)
    version = run_dir.name

    if progress is not None:
        progress("AI 產生報告文案草稿", 35)
    slot_keys = report_slot_keys()
    report_data_text = summarize_report_data(run_dir)
    prompt = build_prompt(report_data_text, slot_keys)
    argv = build_cli_command(cli_kind, prompt, model=model)
    parsed = parse_cli_result(runner(argv, timeout_seconds))
    slots = _extract_slots(parsed)

    if progress is not None:
        progress("寫入確認槽定稿文案", 55)
    # approvals.json 落在報表版本目錄內，供 build_ppt 讀（沿 SKILL.md D-2 槽位契約）。
    approvals = {"report_version": version, "slots": slots}
    approvals_path = run_dir / "approvals.json"
    approvals_path.write_text(
        json.dumps(approvals, ensure_ascii=False, indent=2), encoding="utf-8")

    if progress is not None:
        progress("組版產生 PPTX", 75)
    # 組版：deterministic build_ppt.py；輸出直接落在 report_dir，upload_run_dir 一起上傳。
    result = builder(
        report_dir=run_dir,
        approvals_path=approvals_path,
        output_dir=run_dir,
        theme_path=THEME_PATH,
    )
    pptx_path = Path(result["pptx_path"])
    pptx_filename = pptx_path.name

    if progress is not None:
        progress("上傳 PPTX 到報表產物", 90)
    uploaded = uploader(run_dir)

    if progress is not None:
        progress("報告 PPT 已產出", 100)
    return {
        "based_on_version": version,
        "run_dir": str(run_dir),
        "pptx_filename": pptx_filename,
        "uploaded_files": uploaded,
        "slots_filled": sum(1 for v in slots.values() if v),
        "slots_total": len(slot_keys),
        "prompt_version": PROMPT_VERSION,
        "cli_kind": cli_kind,
        "workspace_id": workspace_id,
    }
