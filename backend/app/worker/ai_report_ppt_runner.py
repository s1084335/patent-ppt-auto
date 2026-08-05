"""報告 PPT 產製 headless CLI runner（ai:report_ppt 任務的核心）。

用途：把 SKILL.md 步驟 A-D 的匯出報告流程程式化——
  A/B/C（AI 產文案 slots）→ 寫 approvals.json → D（CLI 順手呼 deterministic 的
  build_ppt.py 組版）→ .pptx 進 report_artifacts（跨容器：本機檔案系統不通，必須進 DB）。

規格唯一來源：專案 repo 內 `skills/patent-report-ppt/`（PPT 產製契約、runtime 文案規則、
組版腳本與 theme）與 `.agents/context/export-report-flow-spec.md`（開發期規格）。

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

⚠ 資料走檔案不走命令列（2026-07-28，沿 topic_label／patent_note／irrelevant_filter／
  company_zh_name 已搬好的同一套）：報表數據寫成 payload JSON，命令列只帶「短指示＋路徑」，
  CLI 以 Read 讀檔。改的兩個理由見 `load_report_data()` 與 `build_report_ppt_payload()`
  的說明——命令列長度上限與**原本 20K 截斷丟掉 9 成報表數據**。
  白名單因此從「空」放寬到**只有 Read**（共用核心 `pf.READ_ONLY_TOOLS`），
  安全性仍由任務設計保證：CLI 只讀我們寫的那一個 JSON、不連網、不寫檔
  （approvals.json 由 runner 自己寫，不交給 CLI）。

設計沿用 ai_market_summary_runner：CLI 呼叫、build_ppt、upload、resolve 皆可注入，測試餵 fake，
不跑二進位、不燒 token、不真碰 DB／檔案系統。
"""

from __future__ import annotations

import importlib.util
import json
import os
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
from . import ai_payload_file as pf
from .ai_payload_file import extract_json_payload


# 報告 PPT 流程版本；隨 prompt 契約／版型升版而變，寫進結果供追溯。
PROMPT_VERSION = "report_ppt_v2"

# 🔴 最小權限（**舊路徑專用**）：報表數據內嵌 prompt 時 CLI 不需任何工具。
# 主路徑自 2026-07-28 起改走資料檔，白名單由共用核心給 Read（見模組頂部說明）；
# 本常數只服務保留下來的 build_cli_command／build_prompt（離線除錯與既有測試）。
_PPT_TAIL_ARGS = ["--output-format", "json", "--allowedTools", ""]


class ReportPptRunnerError(RuntimeError):
    """報告 PPT 流程失敗（CLI 產出不合契約、build_ppt 未產檔等）。"""


def _resolve_skill_dir() -> Path:
    """定位 patent-report-ppt skill 目錄（含 scripts/build_ppt.py、theme.json）。

    預設來源＝專案 repo 的 `skills/patent-report-ppt/`。正式部署若把 skill 掛載到
    其他位置，可用 `PATENT_REPORT_PPT_SKILL_DIR` 覆寫。不得 fallback 到 `D:\\力山\\.agents`
    或祖先 `.agents`；本機舊檔會掩蓋 Docker／公司伺服器缺檔問題。
    """
    configured = os.environ.get("PATENT_REPORT_PPT_SKILL_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    repo_local = PROJECT_ROOT / "skills" / "patent-report-ppt"
    return repo_local


SKILL_DIR = _resolve_skill_dir()
BUILD_PPT_PATH = SKILL_DIR / "scripts" / "build_ppt.py"
THEME_PATH = SKILL_DIR / "theme.json"
CONTENT_RULES_PATH = SKILL_DIR / "report_ppt_content_rules.md"


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

    runner 產文案就照這組槽名，不自己另定一套（接線非重寫）。

    ⚠ 載不到就 **raise，不做寫死清單 fallback**（2026-07-29 移除）：原 fallback
    寫死拆欄前的 10 槽，而 PAGE_LAYOUT 已改 17 頁動態——真走到 fallback 時
    AI 只產 10 槽、新頁全掛「待確認」浮水印，**job 卻顯示成功**（靜默劣化）。
    寫死副本＝與唯一來源的第二落點，PAGE_LAYOUT 再改就再脫節一次。
    載不到的環境（缺 skill 檔案／python-pptx）本來就組不了版，早炸早知道。
    """
    try:
        return list(_load_builder().all_slot_keys())
    except Exception as exc:
        raise ReportPptRunnerError(
            f"載入 build_ppt.py 失敗，取不到確認槽清單（部署環境缺 skill 檔案或 "
            f"python-pptx？）：{type(exc).__name__}: {exc}"
        ) from exc


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

    ⚠ 2026-07-28 起**不再是主路徑**：報表數據改走 `build_report_ppt_payload` 落檔
    （命令列長度不隨資料成長、且不必截斷）。本函式保留供既有測試與離線除錯使用。

    ⚠ 分工紅線在此明寫：AI 只產各槽的敘述文案、**不碰排版、不碰數字**；排版由
      deterministic 的 build_ppt.py 組，數字一律取自引擎 report_data，AI 不推算不捏造。
    """
    slots_block = "\n".join(f"- {key}" for key in slot_keys)
    return (
        "任務：為專利分析報告 PPT 產出各頁的敘述文案草稿（系統派工、非互動、一次性）。\n\n"
        "── 文案規則（務必遵守）──\n"
        f"{load_content_rules()}\n\n"
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


def load_report_data(report_dir: Path) -> Any:
    """讀報表版本目錄的 report_data.json，**全量**回傳解析後的結構（不截斷）。

    ⚠ 2026-07-28 取代 summarize_report_data 的截斷版（使用者定案「完整資料進檔案」）。
    原本 `text[:20_000]`，而實測 `report_data.json` 有 279,593 字元——
    **AI 只看得到 7%，且截點落在 JSON 中間是破碎片段**，模型連解析都解不開，
    等於拿殘缺數據寫文案卻毫無錯誤訊號（本專案反覆踩過的靜默失敗）。

    回傳解析後的物件而非原字串，讓資料以結構化形式進 payload：
    落檔時重新序列化會去掉原檔 indent-2 的縮排空白，187,151 字元即全部內容
    （原檔 279,593 字元有三分之一是排版空白），不丟任何一個數字。

    只讀既有產物、不改寫；缺檔或內容非法 JSON 時回明確缺漏說明字串，
    讓 AI 知道數據不足而非硬掰（不 raise，缺市場數據的頁本來就允許空著）。
    """
    path = report_dir / "report_data.json"
    if not path.exists():
        return "（無 report_data.json，報表數據不足）"
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 檔在但內容壞掉：原樣給 CLI（仍不截斷），由它自行判斷可用範圍。
        return text


def load_narratives(report_dir: Path) -> Any:
    """讀 narratives.json；缺檔時回空結構，讓 PPT 產製可自動提示缺漏。"""
    path = report_dir / "narratives.json"
    if not path.exists():
        return {"reports": {}}
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def load_content_rules() -> str:
    """讀取匯出報告 PPT 文案 runtime 規則。

    規則來源放在 skill 目錄，讓產品規格、prompt 與部署檔案同版；runner 只負責載入，
    不在程式內維護第二份逐 slot 文案規則，避免 SKILL.md／runtime prompt 分裂。
    """
    try:
        return CONTENT_RULES_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ReportPptRunnerError(
            f"找不到或無法讀取 PPT 文案規則檔：{CONTENT_RULES_PATH}"
        ) from exc

_PPT_OUTPUT_CONTRACT = {
    "shape": '{"slots": {"cover.title": "...", "trend.narrative": "...", ...}}',
    "rules": [
        "key 必須是 slot_keys 列出的槽名（原字不變），不得新增或改寫槽名",
        "value 為該槽的繁體中文文案",
        "查無對應數據的槽可留空字串或省略；缺漏由平台任務進度與 manifest 顯示，不印進 PPT",
        "只輸出一個 JSON 物件，不要多餘說明文字",
    ],
}


def current_topic_versions(workspace_id: Any) -> dict[str, int]:
    """查該 workspace 現行的主題版本 `{source_field: run_id}`（#3b）。

    ⚠ 查掛一律回空 dict——版本比對是提示性功能，不得讓它擋住 PPT 產製
    （沒有比對結果時 `topic_version_warnings` 自然不提示）。
    """
    if workspace_id is None:
        return {}
    try:
        from backend.app.clustering.sources import SOURCE_SPECS
        from backend.app.repositories.topic_state_repository import (
            PostgresTopicStateRepository,
            TopicStateNotFoundError,
        )

        repo = PostgresTopicStateRepository()
        out: dict[str, int] = {}
        for source_field in SOURCE_SPECS:
            try:
                state = repo.get_latest_topic_state(int(workspace_id), source_field)
            except TopicStateNotFoundError:
                continue
            run_id = state.get("run_id")
            if run_id is not None:
                out[source_field] = int(run_id)
        return out
    except Exception:  # noqa: BLE001 - 提示性功能，查不到就不提示
        return {}


def topic_version_warnings(*, recorded: Any, current: Any) -> list[str]:
    """比對報表記下的主題版本與現行版本，回傳提示訊息（一致或無從比對＝空）。

    🔴 **提示不擋**（2026-08-05 使用者定案）：擋會讓使用者在重新分群後
    再也無法為舊版報表出 PPT；提示已足以避免「拿舊主題當現況解讀」。
    ⚠ 任一邊沒有版本就不提示——沒有依據的警告只會製造雜訊，
    而且舊報表本來就沒有這個欄位（本功能之前產的都沒有）。
    """
    if not isinstance(recorded, dict) or not isinstance(current, dict):
        return []
    messages: list[str] = []
    for source_field, recorded_id in sorted(recorded.items()):
        current_id = current.get(source_field)
        if recorded_id is None or current_id is None:
            continue
        if recorded_id != current_id:
            messages.append(
                f"主題版本已更新（{source_field}：報表產製時 run {recorded_id}，"
                f"目前為 run {current_id}）——本份 PPT 的主題標籤沿用產製當時的分群，"
                f"要反映最新分群請重新產製報表")
    return messages


def load_direction_capacity() -> dict[str, int]:
    """研發方向頁的版面容量（R-1，2026-08-05）——與組版端同一個算法。

    ⚠ 字數上限不得寫死在規範檔：12.5pt 時代算出的「≤20 字」在 K-10 改 16pt 後
    直接失真，AI 照寫、組版照截。改由 build_ppt.direction_capacity() 供給，
    改字級時提示自動跟著變。取不到（載不到 builder）就回空，退化為原行為。
    """
    try:
        builder = _load_builder()
        return builder.direction_capacity(builder.Theme.load())
    except Exception:  # noqa: BLE001 - 容量拿不到不該擋整個 PPT 產製
        return {}


def build_report_ppt_payload(report_data: Any, slot_keys: list[str],
                             narratives: Any | None = None) -> dict[str, Any]:
    """組資料檔內容（取代把報表數據截斷後串進命令列）。

    ⚠ 為什麼改（2026-07-28，規格 export-report-flow-spec.md 5-5）：
    原本 build_prompt 把 report_data 截到 20K 再整段塞進 argv，一次踩兩個坑——
    1. **命令列長度**：實測 report_data 50KB → argv 51,775 字元、200KB → 205,375，
       Windows CreateProcess 上限 32,767，必爆 WinError 206（訊息「檔名或副檔名太長」
       與真因完全對不上，每次都要重查一輪）。改後 argv 固定約 200 字元、不隨資料成長。
    2. **靜默丟資料**：20K 截斷 vs 實際 279,593 字元＝AI 只看到 7%。走檔案後全量給，
       不需要任何截斷。

    ⚠ **全量單批、不分批**（2026-07-28 決定，依據見下）：
    共用核心的 MAX_PAYLOAD_CHARS=150,000 是為 topic_label 那類「逐項目」任務訂的，
    其理由（輸出上限、注意力分散、失敗隔離）在本任務都不成立：
    - **輸出上限**：本任務只回 10 個短槽的文案，離單次 128,000 tokens 上限極遠。
    - **品質**：報表數據落在 187,151 字元（compact），Opus 5 的 1,000,000 token
      context 綽綽有餘；且各頁文案本來就要跨報表交叉判讀（例如技術分布頁同時要
      cluster 與 IPC 資料），**切開反而讓模型看不到全貌**，品質更差。
    - **失敗隔離**：單次呼叫本來就是全有全無，分批只是多幾次往返。
    故本任務給全量單批；這不是回頭截斷（一個數字都沒少），是不強套不適用的分批。
    """
    return {
        "task": "為專利分析報告 PPT 產出各頁的敘述文案草稿（系統派工、非互動、一次性）",
        "instruction": (
            "依 report_data 為每一個 slot_keys 列出的確認槽產一段繁體中文文案；"
            "只產文案，不碰排版、不碰數字。"
        ),
        "rules": [load_content_rules()],
        # R-1：版面容量隨提示給到 CLI（字數上限不寫死在規則檔，見 load_direction_capacity）。
        "layout_capacity": load_direction_capacity(),
        "slot_keys": list(slot_keys),
        "report_data": report_data,
        "narratives": narratives if narratives is not None else {"reports": {}},
        "output_contract": _PPT_OUTPUT_CONTRACT,
    }


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


def _clean_str_map(value: Any) -> dict[str, str]:
    """清理前端覆寫用的一層字串 dict，避免多餘結構進 approvals.json。"""
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if v is not None}


def _filter_slots(slots: dict[str, str], allowed_slots: set[str]) -> tuple[dict[str, str], list[str]]:
    """只保留合法 slot；非法 key 回報給任務進度與 result。"""
    valid: dict[str, str] = {}
    invalid: list[str] = []
    for key, value in slots.items():
        if key in allowed_slots:
            valid[key] = value
        else:
            invalid.append(key)
    return valid, sorted(invalid)


def _clean_position_overrides(value: Any) -> dict[str, dict[str, float]]:
    """只保留 PPT 元件位置覆寫的四個 inch 座標欄位。"""
    if not isinstance(value, dict):
        return {}
    allowed = ("left_in", "top_in", "width_in", "height_in")
    cleaned: dict[str, dict[str, float]] = {}
    for key, raw_box in value.items():
        if not isinstance(raw_box, dict):
            continue
        box: dict[str, float] = {}
        for field in allowed:
            if field not in raw_box:
                continue
            try:
                box[field] = float(raw_box[field])
            except (TypeError, ValueError):
                continue
        if box:
            cleaned[str(key)] = box
    return cleaned


def _build_approvals(version: str, ai_slots: dict[str, str],
                     approval_overrides: dict[str, Any] | None,
                     allowed_slots: set[str] | None = None) -> tuple[dict[str, Any], list[str]]:
    """合併 AI 文案與使用者覆寫；非法 slot 不寫入 approvals.json。"""
    overrides = approval_overrides if isinstance(approval_overrides, dict) else {}
    invalid: list[str] = []
    slots = dict(ai_slots)
    override_slots = _clean_str_map(overrides.get("slots"))
    if allowed_slots is not None:
        slots, invalid_ai = _filter_slots(slots, allowed_slots)
        override_slots, invalid_overrides = _filter_slots(override_slots, allowed_slots)
        invalid.extend(invalid_ai)
        invalid.extend(invalid_overrides)
    slots.update(override_slots)
    return {
        "report_version": version,
        "slots": slots,
        "layout_overrides": _clean_str_map(overrides.get("layout_overrides")),
        "position_overrides": _clean_position_overrides(overrides.get("position_overrides")),
    }, sorted(set(invalid))


def _subprocess_text_env() -> dict[str, str]:
    """回傳跑子行程用的環境變數，強制其輸出為 UTF-8。

    ⚠ 不設這個，子行程 stdout 會走系統 codepage；父行程用 UTF-8 解碼含中文路徑
    （`D:\\力山\\專案\\專利_ppt自動\\...`）的輸出時失敗，`completed.stdout` 直接
    回 **None**——真正的失敗原因整包消失。

    ⚠ 為何 2026-07-30 前一直沒發現：Companion 由 `Start-Process -WindowStyle Hidden`
    啟動，繼承不到 PYTHONIOENCODING；而手動重現時指令前都帶了 `PYTHONIOENCODING=utf-8`
    ——測試方法本身掩蓋了 bug。實測：有該變數 stdout_len=318，沒有則 stdout is None。
    """
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


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
    # ⚠ errors="replace" 是保底：PYTHONIOENCODING 萬一沒生效，也要拿到帶替代字元的
    #   字串（看得出發生什麼事），不要 None（線索全失，錯誤訊息會指向不相干的地方）。
    completed = subprocess.run(  # noqa: S603 argv 由固定值組成，非使用者字串
        argv, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=_subprocess_text_env())
    # 實機曾回 stdout=None（通常代表子程序輸出未被捕捉或外層 runtime 行為異常）。
    # runner 不能讓 None.splitlines()/None.strip() 蓋掉真正問題，先正規化成字串。
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if completed.returncode != 0:
        detail = stderr.strip() or stdout.strip() or "stdout/stderr 皆為空"
        raise ReportPptRunnerError(
            f"build_ppt 子行程失敗（exit={completed.returncode}）："
            f"{detail}")
    # 解析 build_ppt 印出的 pptx 與 manifest 路徑。
    pptx_path = None
    manifest_path = None
    for line in stdout.splitlines():
        if line.startswith("pptx:"):
            pptx_path = line.split(":", 1)[1].strip()
        if line.startswith("manifest:"):
            manifest_path = line.split(":", 1)[1].strip()
    if not pptx_path:
        output = stdout[:500] if stdout else "stdout 為空"
        raise ReportPptRunnerError(f"build_ppt 未回報 pptx 路徑；輸出：{output}")
    manifest: dict[str, Any] = {}
    if manifest_path:
        path = Path(manifest_path)
        if path.exists():
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {}
    return {"pptx_path": pptx_path, "manifest_path": manifest_path or "", "manifest": manifest}


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
    payload_root: Any = None,
    approval_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """整條報告 PPT 流程：解析報表目錄 → AI 產文案 slots → 寫 approvals.json →
    呼 build_ppt.py 組 .pptx（進 report_dir）→ upload_run_dir 一起上傳到 report_artifacts。

    ⚠ 全庫也能產（不因 workspace_id 是全庫而 raise，與市場摘要不同）；市場章節缺數據的頁
      由 build_ppt 自動標「待確認」浮水印，不擋整檔產出。
    ⚠ 分工：AI 只產 slots 文案（不碰排版／數字）；組版一律 deterministic build_ppt。

    cli_runner／resolve_run_dir／build_ppt／upload_run_dir／payload_root 皆可注入，供測試以
    fake 取代，不跑二進位／不燒 token／不真碰 DB。每階段回報進度（0→100），不留無限 spinner。
    回傳含 pptx_filename（進 artifact 的檔名）供前端下載路由組 URL。
    """
    resolver = resolve_run_dir if resolve_run_dir is not None else _default_resolve_run_dir
    runner = cli_runner if cli_runner is not None else _subprocess_cli_runner
    builder = build_ppt if build_ppt is not None else _default_build_ppt
    uploader = upload_run_dir
    if uploader is None:
        from backend.app.db.report_artifact_store import upload_run_dir as _upload
        uploader = _upload
    pf.cleanup_old_payloads(root=payload_root)

    if progress is not None:
        progress("解析報表版本目錄", 10)
    run_dir = resolver(based_on_version)
    version = run_dir.name

    if progress is not None:
        progress("AI 產生報告文案草稿", 35)
    slot_keys = report_slot_keys()
    allowed_slots = set(slot_keys)
    # 資料走檔案、命令列只留 instruction 與路徑（見 build_report_ppt_payload 的說明）：
    # 全量報表數據進 payload，不截斷、不隨資料量撐大 argv。
    payload_path = pf.write_payload_file(
        "report_ppt",
        build_report_ppt_payload(load_report_data(run_dir), slot_keys, load_narratives(run_dir)),
        root=payload_root,
        label=version,
    )
    argv = pf.build_cli_command_with_payload(
        cli_kind,
        instruction="任務：為專利分析報告 PPT 產出各頁敘述文案（系統派工、非互動、一次性）。",
        payload_path=payload_path,
        model=model,
    )
    parsed = parse_cli_result(runner(argv, timeout_seconds))
    slots = _extract_slots(parsed)

    if progress is not None:
        progress("寫入確認槽定稿文案", 55)
    # approvals.json 落在報表版本目錄內，供 build_ppt 讀（沿 SKILL.md D-2 槽位契約）。
    approvals, invalid_slots = _build_approvals(version, slots, approval_overrides, allowed_slots)
    if invalid_slots and progress is not None:
        progress(f"已過濾無效 PPT 文案槽：{', '.join(invalid_slots)}", 60)
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
    # #3b：主題版本不一致＝提示（不擋）。報表產製時記下的版本在 report_data.json，
    # 與現行版本比對；任一邊缺就不提示（舊報表沒有這個欄位）。
    try:
        _rd_path = run_dir / "report_data.json"
        _recorded = (json.loads(_rd_path.read_text(encoding="utf-8")).get("parameters") or {}
                     ).get("topic_run_id") if _rd_path.exists() else None
    except Exception:  # noqa: BLE001 - 讀不到就不提示
        _recorded = None
    _stale = topic_version_warnings(
        recorded=_recorded, current=current_topic_versions(workspace_id))

    return {
        "based_on_version": version,
        "run_dir": str(run_dir),
        "topic_version_warnings": _stale,
        "pptx_filename": pptx_filename,
        "uploaded_files": uploaded,
        "slots_filled": sum(1 for v in approvals["slots"].values() if v),
        "slots_total": len(slot_keys),
        "invalid_slots": invalid_slots,
        "manifest_path": result.get("manifest_path", ""),
        "manifest": result.get("manifest") or {},
        "missing_slots": [
            slot
            for page in (result.get("manifest") or {}).get("pages", [])
            for slot in page.get("missing_slots", [])
        ],
        "missing_reports": [
            report_key
            for page in (result.get("manifest") or {}).get("pages", [])
            for report_key in page.get("missing_reports", [])
        ],
        "prompt_version": PROMPT_VERSION,
        "cli_kind": cli_kind,
        "workspace_id": workspace_id,
    }
