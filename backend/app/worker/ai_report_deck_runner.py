"""簡報產製 runner：runner 驅動機械步、CLI 只接撰稿與目視迴圈。

系統位置（add-deck-delivery-line design §1）：前端「匯出報告」頁按「產製簡報」
→ `ai:report_deck` job → Companion ai_bridge 領取 → 本 runner。

## 分工（為什麼不讓 CLI 一路跑到底）

機械步（intake／排頁／chip 重排／fit／閘門／組版／稽核／截圖）本來就是
確定性程式——runner 以 subprocess 驅動可拿到**結構化 exit code 當閘門**；
讓 CLI 跑需要 Bash 白名單，權限面等於開發機 agent，與「CLI 只輔助、
不碰執行面」相反。CLI 只做兩件需要判斷的事：**撰稿**（產 content.json）與
**目視迴圈**（看逐頁 PNG、改 content.json）。

## 目視迴圈（design §1）

每輪＝check_content → make_deck → audit → 截圖 → CLI 看圖。
check／make 非零＝內容問題（判讀帶超長、版面溢出、圖內字級不足），
把閘門輸出交給 CLI 修稿，**走同一個迴圈、吃同一個上限**——不另設重試路。
audit 非零＝引擎違約（字級白名單外），不是內容能修的，硬失敗。
上限 `DECK_VISUAL_LOOP_MAX_ROUNDS`（env，預設 4，產線參數不改程式）；
達上限即失敗並附最後一輪目視發現。⚠ CLI 說有問題卻沒改 content.json
＝停滯，立即失敗——不許空轉燒到上限。

## 回存（design §3）

**失敗不落半成品**：全閘門過才把 pptx＋逐頁 PNG 搬進
`DECK_ARTIFACT_ROOT/<version>/`，manifest（based_on_version、相對 key、
SHA-256、閘門摘要、逐輪目視紀錄）隨 job result 落 DB——DB 只存相對 key。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .cli_gateway import (
    DEFAULT_CLI_TIMEOUT_SECONDS,
    RESEARCH_TOOLS,
    CliRunner,
    build_cli_command,
    parse_cli_result,
    run_cli,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: 目視迴圈上限（產線參數；design §1 定案預設 4，調整改 env 不改程式）。
DEFAULT_MAX_VISUAL_ROUNDS = 4

#: 機械步 executor 介面：(step 名, argv) → (exit code, 合併輸出)。
#: 測試注入 fake；正式實作見 `_run_step_subprocess`。
StepRunner = Callable[[str, list[str]], tuple[int, str]]


class DeckRunnerError(RuntimeError):
    """deck 產製失敗（機械步非零、CLI 未產出、目視迴圈未收斂、回存失敗）。"""


def _resolve_skill_scripts() -> Path:
    """deck skill 的 scripts 目錄。

    正式位置＝產品 repo 的 `skills/html-report-to-deck/scripts`（隨 Installer
    佈署）；`DECK_SKILL_ROOT` 供部署搬位置時覆寫，沿 narrative 線
    `REPORT_NARRATIVE_FLOW_PATH` 的同一模式。
    """
    configured = os.environ.get("DECK_SKILL_ROOT", "").strip()
    base = Path(configured).expanduser() if configured \
        else PROJECT_ROOT / "skills" / "html-report-to-deck"
    return (base / "scripts").resolve()


def default_artifact_root() -> Path:
    """`DECK_ARTIFACT_ROOT`（正式＝NAS 掛載點；未設＝本機 data 目錄）。

    沿 `MODEL_ARTIFACT_ROOT` resolver 前例；DB 只存相對於此根的 key。
    """
    configured = os.getenv("DECK_ARTIFACT_ROOT", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()
    return (PROJECT_ROOT / "data" / "deck_artifacts").resolve()


def max_visual_rounds_from_env() -> int:
    """`DECK_VISUAL_LOOP_MAX_ROUNDS`；壞值退預設（產線參數不該讓 job 掛在解析上）。"""
    raw = os.getenv("DECK_VISUAL_LOOP_MAX_ROUNDS", "").strip()
    try:
        value = int(raw)
        return value if value >= 1 else DEFAULT_MAX_VISUAL_ROUNDS
    except ValueError:
        return DEFAULT_MAX_VISUAL_ROUNDS


def _run_step_subprocess(step: str, argv: list[str]) -> tuple[int, str]:
    """機械步的正式實作：專案 interpreter 起子行程（tasks 2.2d 定案）。

    ⚠ `encoding="utf-8"` 不可省——skill 腳本輸出繁中，父行程解碼失敗會讓
    output 回 None（narrative 線 job #132 的同一根因）。
    """
    completed = subprocess.run(
        argv, check=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, output


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cover_tech_name(work: Path) -> str:
    """封面技術名稱（tasks 2.4）：workspace 名，全庫退回報表標題。

    值由 intake 的 `report.json` `report_meta` 供給（`assemble_from_version`
    自 version_meta.json 解出）——runner 只消費，不另查 DB，不第二落點。
    """
    try:
        meta = json.loads((work / "report.json").read_text(encoding="utf-8")) \
            .get("report_meta") or {}
    except (OSError, ValueError):
        return ""
    return str(meta.get("workspace_name") or meta.get("h1") or "").strip()


def _workspace_id(run_dir: Path) -> int | None:
    """版本綁定的 workspace（取證範圍用）。來源＝run_dir 的 version_meta.json
    ——它是 workspace_id 的**起點**（report_meta 也是從它解出的）。
    ⚠ 讀起點而非 report.json：narrative 前置在 assemble 之前跑，
    那時 report.json 還不存在。缺＝不綁（全庫版本）。"""
    try:
        meta = json.loads((run_dir / "version_meta.json").read_text(encoding="utf-8"))
        raw = meta.get("workspace_id")
        return int(raw) if raw is not None else None
    except (OSError, ValueError, TypeError):
        return None


def build_deck_cli_command(cli_kind: str, prompt: str, *,
                           model: str | None = None) -> list[str]:
    """deck 線（撰稿與目視共用）的 CLI argv——權限層級的**唯一定義處**。

    ＝RESEARCH_TOOLS（與 ai:narrative 同級，design §2 定案）：讀素材＋
    唯讀 MCP 取證＋寫檔，無 Bash。權限審視閘門
    （test_cli_gateway.MinimalPrivilegePreservedTests）從這裡取實際 argv。
    """
    return build_cli_command(cli_kind, prompt, model=model, tools=RESEARCH_TOOLS)


def build_writing_prompt(work: Path, version: str, *, scripts: Path,
                         gate_output: str | None = None) -> str:
    """撰稿提示：指向素材與 skill 規範，不複製規格內文（narrative 線同模式）。

    `gate_output` 給定＝修稿輪（check_content／make_deck 的閘門輸出），
    CLI 依輸出修 content.json；否則為首次撰稿。
    """
    skill_root = scripts.parent
    cover = _cover_tech_name(work)
    lines = [
        f"你是簡報撰稿者。工作目錄：{work}（版本 {version}）。",
        f"先完整讀 {skill_root / 'SKILL.md'} 的撰稿規範與"
        f"{skill_root / 'references' / 'narrative.md'} 的寫法範式並遵守。",
        f"素材：{work / 'plan.json'}（頁序與 structure_checklist）、"
        f"{work / 'report.json'}（texts／tables／notes）。",
    ]
    if cover:
        lines.append(f"封面技術名稱一律使用 workspace 名稱：「{cover}」。")
    if gate_output:
        lines.append(
            "以下是上一輪閘門輸出，逐項修正後回寫 content.json"
            "（合法動作只有縮寫、改寫、拆頁、轉純文字頁）：\n" + gate_output.strip())
    else:
        lines.append(
            f"完成撰稿後把結果寫入 {work / 'content.json'}（唯一輸出檔）。"
            "plan.json 的 structure_checklist 要逐項處理完。")
    lines.append(
        "顆粒度不足時可用唯讀 MCP 取證工具查請求項原文；只讀不寫、"
        "只補敘述不補統計、新名詞標來源專利號、斷言範圍＝證據範圍。"
        "取證已綁定本 workspace：查 patents／patent_attributes 時"
        "JOIN workspace_scope（欄位 patent_id）過濾，該 CTE 由系統注入。")
    return "\n".join(lines)


def build_review_prompt(work: Path, shots_dir: Path, round_no: int) -> str:
    """目視迴圈提示：CLI 看逐頁 PNG，發現問題改 content.json，寫 verdict。

    檢查清單沿 SKILL.md 現行目視清單——不另立第二份清單。
    """
    return "\n".join([
        f"逐頁目視第 {round_no} 輪。逐一讀 {shots_dir} 內的每一張 PNG"
        "（**不得抽樣**，每頁都要看），依 SKILL.md 目視清單檢查：",
        "文字沒溢出卡片、沒重疊；圖表沒被裁切；圖內字看得清楚；版面平衡；"
        "放大看行首有沒有中文標點。",
        f"發現問題→只能修改 {work / 'content.json'}（縮寫、改寫、拆頁、"
        "轉純文字頁；圖表與版面引擎不得動）。",
        f"最後把結論寫入 {work / 'visual_verdict.json'}，格式："
        '{"pass": true|false, "findings": ["發現…", …]}——'
        "pass=true 表示全部頁面通過、content.json 未再修改。",
    ])


def run_deck(
    based_on_version: str | None,
    *,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: CliRunner | None = None,
    step_runner: StepRunner | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
    root: Path | None = None,
    work_root: Path | None = None,
    artifact_root: Path | None = None,
    max_visual_rounds: int | None = None,
    resolve_run_dir: Callable[..., Path] | None = None,
    ensure_narrative: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """整條 deck 產製：素材 → 機械步 → CLI 撰稿 → 目視迴圈 → 回存。

    回傳 manifest 摘要（job result 落 workflow_outputs 的內容）。
    所有外部效應（subprocess／CLI）都可注入，單元測試不真跑。
    """
    runner = cli_runner if cli_runner is not None else run_cli
    steps = step_runner if step_runner is not None else _run_step_subprocess
    rounds_cap = max_visual_rounds if max_visual_rounds is not None \
        else max_visual_rounds_from_env()
    scripts = _resolve_skill_scripts()
    py = sys.executable

    def _progress(stage: str, pct: int) -> None:
        if progress is not None:
            progress(stage, pct)

    def _step(step: str, argv: list[str], *, gate: bool = False) -> tuple[int, str]:
        """跑一機械步。gate=False 時非零即硬失敗短路；gate=True 交呼叫端判。"""
        code, output = steps(step, argv)
        if code != 0 and not gate:
            raise DeckRunnerError(
                f"機械步 {step} 失敗（exit={code}）：{output.strip()[:600]}")
        return code, output

    # ── 1. 素材：解析版本目錄（本機優先、DB 落地補位——沿 narrative 同函式）──
    _progress("materialize", 5)
    from .ai_narrative_runner import resolve_run_dir as _default_resolver
    resolver = resolve_run_dir or _default_resolver
    run_dir = resolver(based_on_version, root=root)
    version = run_dir.name
    ws_id = _workspace_id(run_dir)

    # ── 1b. narrative 前置（2026-08-14 使用者裁決「未產解讀要先產解讀」）──
    # deck 的判讀素材（report.json texts）來自 narratives.json；缺了不補會產出
    # 判讀帶空洞的簡報——**靜默品質損失**，比 fail 難發現。已有解讀不重跑
    # （narrative 燒 CLI token，重產由使用者在報表頁主動按）。
    from backend.app.mcp_server.report_research import workspace_scope_env
    narrative_chained = False
    if not (run_dir / "narratives.json").exists():
        _progress("narrative", 8)
        producer = ensure_narrative
        if producer is None:
            from .ai_narrative_runner import run_narrative as producer
        try:
            with workspace_scope_env(ws_id):
                producer(version, cli_kind=cli_kind, model=model,
                         cli_runner=cli_runner, timeout_seconds=timeout_seconds,
                         root=root)
        except Exception as exc:
            raise DeckRunnerError(
                f"前置報表解讀失敗（版本 {version} 尚無 narratives.json，"
                f"deck 不得帶著空判讀繼續）：{type(exc).__name__}: {exc}") from exc
        if not (run_dir / "narratives.json").exists():
            raise DeckRunnerError(
                f"前置解讀正常結束但 {run_dir / 'narratives.json'} 仍不存在")
        narrative_chained = True

    work_base = work_root if work_root is not None \
        else PROJECT_ROOT / "var" / "deck_work"
    work = work_base / version
    # 每次重產從乾淨 work 開始——殘檔會讓 plan／fit 讀到上一輪的東西。
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    # ── 2. 機械步：intake → 排頁 →（標記才 chip）→ fit ──────────────
    _progress("assemble", 10)
    _step("assemble", [py, str(scripts / "assemble_from_version.py"),
                       str(run_dir), str(work)])
    _progress("plan", 14)
    _step("plan", [py, str(scripts / "plan_deck.py"), str(work)])

    plan_path = work / "plan.json"
    if not plan_path.is_file():
        raise DeckRunnerError(f"plan_deck 正常結束但未產出 {plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    chips = [str(n) for n in plan.get("rebuildable_chip_chart") or []]
    if chips:
        _progress("chip", 17)
        _step("chip", [py, str(scripts / "rebuild_chip_chart.py"),
                       str(work / "charts"), *chips])

    # 🔴 換色（§6.2b）：報表側色票 → deck 側色票，讓任一頁只出現一種深藍。
    # ⚠ 位置不能動：
    #   - 必須在 `chip` **之後**——`rebuild_chip_chart` 會寫入報表側的色。
    #   - 必須在 `fit` **之前**——fit 產的 PNG 就是進投影片的畫素，之後再換來不及。
    #   - `marks` 在更後面，但它寫的 `#B0123C` 本來就是 deck 側的色，不需轉換。
    _progress("recolor", 19)
    _step("recolor", [py, str(scripts / "recolor_for_deck.py"), str(work / "charts")])

    _progress("fit", 20)
    _step("fit", [py, str(scripts / "fit_render_charts.py"),
                  str(work / "charts"), str(work / "png")])

    # ── 3. CLI 撰稿：唯一輸出＝content.json ────────────────────────
    # 取證範圍（ws_id 已於 resolve 後取自 version_meta）：CLI（含其 MCP 子行程）
    # 只在 workspace_scope_env 內起——query_database 據此注入成員 CTE＋join 閘門。
    # ⚠ 逐呼叫點包而不是包整段：env 只需在「起 CLI 的瞬間」正確。
    _progress("cli_writing", 30)
    content_path = work / "content.json"
    prompt = build_writing_prompt(work, version, scripts=scripts)
    argv = build_deck_cli_command(cli_kind, prompt, model=model)
    with workspace_scope_env(ws_id):
        parse_cli_result(runner(argv, timeout_seconds))
    if not content_path.is_file():
        raise DeckRunnerError(f"CLI 正常結束但未產出 {content_path}")
    _progress("cli_writing", 55)

    # ── 4. 目視迴圈：check → make → audit → shoot → CLI 看圖 ──────
    pptx_path = work / "deck.pptx"
    svg_dir = work / "svg"
    shots_dir = work / "shots"
    visual_log: list[dict[str, Any]] = []
    passed = False
    last_findings: list[str] = []

    def _fix_round(round_no: int, gate_output: str, source: str) -> None:
        """閘門紅→CLI 修稿（同一迴圈的一輪；content 沒改＝停滯即失敗）。"""
        before = _sha256(content_path)
        fix_prompt = build_writing_prompt(work, version, scripts=scripts,
                                          gate_output=gate_output)
        fix_argv = build_deck_cli_command(cli_kind, fix_prompt, model=model)
        with workspace_scope_env(ws_id):
            parse_cli_result(runner(fix_argv, timeout_seconds))
        if _sha256(content_path) == before:
            raise DeckRunnerError(
                f"{source} 未通過且 CLI 未修改 content.json（停滯）：{gate_output[:300]}")
        visual_log.append({"round": round_no, "source": source,
                           "findings": [gate_output.strip()[:600]],
                           "content_changed": True})

    for round_no in range(1, rounds_cap + 1):
        base_pct = min(60 + (round_no - 1) * 8, 88)
        _progress(f"visual_round_{round_no}", base_pct)

        # 跨度圖宣告式標記（design §7.8b）：CLI 宣告、引擎照畫。宣告接不上資料
        # （名稱不在圖上、年份不在軸上）＝內容問題，走修稿輪；有套用時圖變了，
        # 重跑 fit 讓 PNG 跟上（沒宣告時腳本秒退，不花時間）。
        code, output = _step("marks", [py, str(scripts / "apply_chart_marks.py"),
                                       str(work)], gate=True)
        if code != 0:
            _fix_round(round_no, output, "chart_marks")
            continue
        if "MARKS_APPLIED" in output:
            _step("fit", [py, str(scripts / "fit_render_charts.py"),
                          str(work / "charts"), str(work / "png")])

        # 🔴 換色驗收（§6.2c）：驗**產物**——進 deck 的 SVG 不得再有報表側的色。
        # ⚠ 放在這裡而不是緊接換色之後：`marks` 之後 SVG 還會被動一次，
        #   只在換色當下驗等於驗了一份不是最終產物的東西。
        # ⚠ 這是引擎保證項（不是內容問題），紅了不走修稿輪——CLI 改不動色票。
        # ⚠ 帶 content.json：不帶的話只驗換色、**不驗同頁互斥**（§6.8），
        #   而腳本會把「略過」印出來——但印出來不等於有人看，所以這裡直接給。
        _step("recolor_check", [py, str(scripts / "recolor_for_deck.py"),
                                str(work / "charts"), "--check", str(content_path)])

        code, output = _step("check", [py, str(scripts / "check_content.py"),
                                       str(content_path), str(work / "png")],
                             gate=True)
        if code != 0:
            _fix_round(round_no, output, "check_content")
            continue
        code, output = _step("make", [py, str(scripts / "make_deck.py"),
                                      str(content_path), str(work / "png"),
                                      str(pptx_path), str(svg_dir)],
                             gate=True)
        if code != 0:
            _fix_round(round_no, output, "make_deck")
            continue
        # audit＝引擎保證項（字級白名單、圖去重）；紅了不是內容能修的——硬失敗。
        _step("audit", [py, str(scripts / "audit_deck.py"), str(pptx_path)])
        if shots_dir.exists():
            shutil.rmtree(shots_dir)
        _step("shoot", [py, str(scripts / "shoot_pages.py"),
                        str(svg_dir), str(shots_dir)])

        before = _sha256(content_path)
        verdict_path = work / "visual_verdict.json"
        verdict_path.unlink(missing_ok=True)
        review_argv = build_deck_cli_command(
            cli_kind, build_review_prompt(work, shots_dir, round_no), model=model)
        with workspace_scope_env(ws_id):
            parse_cli_result(runner(review_argv, timeout_seconds))
        if not verdict_path.is_file():
            raise DeckRunnerError(f"目視第 {round_no} 輪未產出 {verdict_path}")
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        last_findings = [str(f) for f in verdict.get("findings") or []]
        changed = _sha256(content_path) != before
        visual_log.append({"round": round_no, "source": "cli_review",
                           "findings": last_findings, "content_changed": changed})
        if verdict.get("pass"):
            passed = True
            break
        if not changed:
            raise DeckRunnerError(
                f"目視第 {round_no} 輪回報問題但未修改 content.json（停滯）："
                + "；".join(last_findings)[:400])

    if not passed:
        raise DeckRunnerError(
            f"目視迴圈達上限 {rounds_cap} 輪仍未通過。最後一輪發現："
            + ("；".join(last_findings) if last_findings else "（無明細）"))

    # ── 5. 回存：全閘門過才搬進 artifact root（失敗不落半成品）────────
    _progress("persist", 92)
    dest_root = (artifact_root if artifact_root is not None
                 else default_artifact_root())
    version_dir = dest_root / version
    if version_dir.exists():
        shutil.rmtree(version_dir)
    (version_dir / "pages").mkdir(parents=True)
    shutil.copy2(pptx_path, version_dir / "deck.pptx")
    # content.json 隨產物保存（tasks 3.1）：這份 deck 的**可重現輸入**——
    # 沒有它，「重產一份一樣的」或「查某句話當時怎麼寫的」都辦不到。
    shutil.copy2(content_path, version_dir / "content.json")
    shots = sorted(shots_dir.glob("*.png"))
    for shot in shots:
        shutil.copy2(shot, version_dir / "pages" / shot.name)

    manifest: dict[str, Any] = {
        "based_on_version": version,
        "pptx_key": f"{version}/deck.pptx",           # DB 只存相對 key
        "sha256": _sha256(version_dir / "deck.pptx"),
        "content_key": f"{version}/content.json",
        "content_sha256": _sha256(version_dir / "content.json"),
        "size_bytes": (version_dir / "deck.pptx").stat().st_size,
        "page_count": len(shots),
        "page_keys": [f"{version}/pages/{s.name}" for s in shots],
        "visual_rounds": len({e["round"] for e in visual_log}),
        "visual_log": visual_log,
        "cli_kind": cli_kind,
        "narrative_chained": narrative_chained,
    }
    (version_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _progress("persist", 97)
    return {**manifest, "work_dir": str(work), "artifact_dir": str(version_dir)}
