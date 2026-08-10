"""模組驗證流程的可執行版（AGENTS.md「模組驗證流程」的工具面）。

🔴 為什麼要有這支：那套流程的價值全在**界定範圍**——全庫 ruff 報 742 個問題、
整檔覆蓋率 22%，兩個數字都判斷不了「這次做的東西好不好」。要看的是
**本次新增行**的問題數與覆蓋率，而那需要把工具輸出與 `git diff` 的行號做交集。
手算會漏、每次重打指令會不一致，故收斂成一支。

用法（在專案根目錄）：

    uv run python scripts/verify_module.py --base HEAD~1 --tests tests/test_a.py tests/test_b.py

參數：
  --base        比較基準（預設 HEAD~1）；要驗一整個分支就給分支點
  --tests       要跑的測試檔（功能測試與覆蓋率都以這些為準）
  --paths       靜態分析範圍（預設 backend/app）
  --source      覆蓋率來源，可多個；套件名或目錄路徑
  --regression  一併跑專案標準範圍回歸
  --skip        跳過某階段：tests／lint／coverage／complexity

⚠ **不要只服務單一模組**（2026-08-05 使用者提醒）：本專案的程式散在
`backend/app/` 與 `skills/patent-report-ppt/scripts/` 兩處，故 --paths／--source
皆為多值、預設值只是常用值。換模組時只換參數，不必改腳本。

範例（組版模組，程式在 skills/ 底下）：

    uv run python scripts/verify_module.py --base HEAD~5         --tests tests/test_ppt_builder.py         --paths skills/patent-report-ppt/scripts         --source skills/patent-report-ppt/scripts

⚠ 本腳本只**量測與回報**，不修改任何程式；門檻未達標時以退出碼 1 表示，
判斷與修正仍由人／agent 決定（見 AGENTS.md 的四項驗收門檻）。
⚠ 工具一律 `uv run --with` 暫時性執行，不裝進專案環境。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from pathlib import Path

# ⚠ Windows 主控台預設 cp950，含 ≥／≤ 這類符號會 UnicodeEncodeError 而中斷輸出
# ——本專案既有的坑（見 work-log 2026-08-04）。輸出一律轉 UTF-8。
if "pytest" not in sys.modules and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SEP = chr(92)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 驗收門檻（AGENTS.md「模組驗證流程」）。改門檻要連同該節一起改，不得只改這裡。
LINT_MAX_NEW_ISSUES = 0
COVERAGE_MIN_PERCENT = 90.0
COMPLEXITY_MAX_RANK = "B"  # A–B（CC ≤ 10）


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """跑外部指令並回傳結果（不 raise；由呼叫端判讀退出碼）。

    ⚠ `check=False` 是刻意的：本腳本靠退出碼判讀各關卡結果，
    拋例外會讓後面的關卡整批跑不到——「測試紅了就看不到覆蓋率」正是要避免的。
    """
    return subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False, **kwargs)


def added_lines(base: str) -> dict[str, set[int]]:
    """本次新增行：{相對路徑: {行號}}。

    ⚠ 用 `-U0`：帶上下文的話未改動的行也會被算進來，範圍就失真了。
    """
    diff = run(["git", "diff", base, "-U0"]).stdout
    out: dict[str, set[int]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            out.setdefault(current, set())
        elif line.startswith("@@") and current:
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2) or 1)
                out[current].update(range(start, start + count))
    return out


def _match_key(path: str, added: dict[str, set[int]]) -> str | None:
    """把工具輸出的絕對路徑對回 diff 的相對路徑。"""
    normalized = path.replace(SEP, "/")
    return next((key for key in added if normalized.endswith(key)), None)


def check_lint(paths: list[str], added: dict[str, set[int]]) -> tuple[bool, str]:
    """靜態分析：只算落在本次新增行的問題（既有問題另計、不混報）。"""
    result = run(["uv", "run", "--no-project", "--python", "3.12", "--with", "ruff",
                  "ruff", "check", "--output-format", "json", *paths])
    try:
        issues = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return False, f"ruff 輸出無法解析：{(result.stderr or '')[:200]}"
    mine = []
    for issue in issues:
        key = _match_key(issue["filename"], added)
        row = (issue.get("location") or {}).get("row")
        if key and row in added[key]:
            mine.append(f"{key}:{row} {issue['code']} {issue['message'][:60]}")
    ok = len(mine) <= LINT_MAX_NEW_ISSUES
    lines = [f"新增行問題 {len(mine)}／全庫 {len(issues)}（門檻 ≤{LINT_MAX_NEW_ISSUES}）"]
    lines.extend(f"    {item}" for item in mine[:20])
    return ok, "\n".join(lines)


def check_tests(tests: list[str]) -> tuple[bool, str]:
    """功能正確性的第一關：指定的測試到底過不過。

    ⚠ 覆蓋率高但測試是紅的，等於「把錯的行為覆蓋得很完整」——必須先看這一項。
    （測試案例設計是否涵蓋等價類／邊界值／決策表，工具驗不了，見 AGENTS.md 人工判讀。）
    """
    result = run(["uv", "run", "python", "-m", "pytest", *tests, "-q",
                  "-p", "no:cacheprovider"])
    tail = [line for line in (result.stdout or "").splitlines() if line.strip()][-1:]
    return result.returncode == 0, (tail[0] if tail else "（無輸出）")


def check_coverage(tests: list[str], sources: list[str],
                   added: dict[str, set[int]]) -> tuple[bool, str]:
    """測試完整性：算**新增行**覆蓋率（整檔覆蓋率含大量既有碼，看不出新功能品質）。

    ⚠ `sources` 收多個值：模組不見得都在同一個套件下——本專案的組版程式在
    `skills/patent-report-ppt/scripts/`，只給 `backend.app` 就整段量不到
    （2026-08-05 使用者提醒「不要搞成只能用這次」時修正）。
    套件名（`backend.app`）與目錄路徑（`skills`）都可以給。
    """
    run(["uv", "run", "--with", "coverage", "python", "-m", "coverage", "run",
         f"--source={','.join(sources)}", "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider"])
    report = PROJECT_ROOT / ".verify_coverage.json"
    run(["uv", "run", "--with", "coverage", "python", "-m", "coverage", "json",
         "-o", str(report)])
    if not report.exists():
        return False, "coverage 沒有產出資料（測試是否真的執行到？）"
    data = json.loads(report.read_text(encoding="utf-8"))
    report.unlink(missing_ok=True)
    total_new = total_hit = 0
    detail: list[str] = []
    for path, info in sorted(data["files"].items()):
        key = _match_key(path, added)
        if not key or key.startswith("tests/"):
            continue
        executable = set(info["executed_lines"]) | set(info["missing_lines"])
        new_exec = executable & added[key]
        if not new_exec:
            continue
        hit = new_exec & set(info["executed_lines"])
        total_new += len(new_exec)
        total_hit += len(hit)
        detail.append(f"    {key}: {len(hit)}/{len(new_exec)}"
                      f" = {len(hit) / len(new_exec) * 100:.0f}%")
        missed = sorted(new_exec - set(info["executed_lines"]))
        if missed:
            detail.append(f"        未覆蓋行：{missed}")
    if not total_new:
        return True, "本次無新增可執行行（純文件／設定變更）"
    percent = total_hit / total_new * 100
    ok = percent >= COVERAGE_MIN_PERCENT
    headline = (f"新增行覆蓋率 {total_hit}/{total_new} = {percent:.0f}%"
                f"（門檻 ≥{COVERAGE_MIN_PERCENT:.0f}%）")
    return ok, "\n".join([headline, *detail])


#: 依賴本機 postgres、沒起 DB 時每支空等 30 秒的測試（AGENTS.md「回歸只跑範圍」）。
#: ⚠ 這份清單與該章節是同一份知識——改一邊要改另一邊。
DB_DEPENDENT_TESTS = (
    "tests/test_clustering_database.py",
    "tests/test_report_artifact_store.py",
    "tests/test_api_report_versions.py",
    "tests/test_api_exclude_patents.py",
    "tests/test_api_exclusion_reviews.py",
)
DB_DEPENDENT_DESELECT = ("tests/test_per_channel_topic_labels.py",)

VERIFY_PRESETS: dict[str, dict[str, list[str] | str]] = {
    "report-professionalism": {
        "groups": ["report", "transform", "renderer", "narrative"],
        "tests": [
            "tests/test_report_catalog_removals.py",
            "tests/test_annual_trend_four_columns.py",
            "tests/test_report_quality_and_ipc_filter.py",
            "tests/test_cluster_reports_and_narrative.py",
            "tests/test_ppt_reader_facing_output.py",
            "tests/test_ppt_layout_contract.py",
            "tests/test_narrative_contract_v4.py",
            "tests/test_narrative_named_subjects.py",
            "tests/test_narrative_capacity_is_honest.py",
        ],
        "paths": ["backend/app", "skills/patent-report-ppt/scripts"],
        "source": ["backend.app", "skills/patent-report-ppt/scripts"],
        "regression_filter": "report or transform or renderer or narrative",
    },
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 preset 測試所需的公開 CLI 參數。"""
    parser = argparse.ArgumentParser(description="verify_module argument parser")
    parser.add_argument("--base", default="HEAD~1")
    parser.add_argument("--preset", choices=sorted(VERIFY_PRESETS))
    parser.add_argument("--tests", nargs="+")
    parser.add_argument("--paths", nargs="+", default=["backend/app"])
    parser.add_argument("--source", nargs="+", default=["backend.app"])
    parser.add_argument("--regression", action="store_true")
    parser.add_argument("-k", dest="regression_filter", default=None)
    parser.add_argument("--skip", nargs="*", default=[])
    args = parser.parse_args(argv)
    if not args.tests and not args.preset:
        parser.error("--tests or --preset is required")
    return args


def resolve_preset_args(args: argparse.Namespace) -> argparse.Namespace:
    """套用 verify preset，補齊目標測試、掃描路徑與回歸 filter。"""
    if not args.preset:
        return args
    preset = VERIFY_PRESETS[args.preset]
    if not args.tests:
        args.tests = list(preset["tests"])
    if args.paths == ["backend/app"]:
        args.paths = list(preset["paths"])
    if args.source == ["backend.app"]:
        args.source = list(preset["source"])
    if args.regression_filter is None:
        args.regression_filter = str(preset["regression_filter"])
    return args


def check_regression(tests: list[str], keyword: str | None) -> tuple[bool, str]:
    """專案標準範圍回歸（不是完整回歸——見 AGENTS.md「回歸只跑範圍」）。

    ⚠ 不起本機 postgres（使用者明示「不要亂起」），以 --ignore 排除即可。
    keyword 未給時由 --tests 的檔名推：`test_ranking_x.py` → `ranking`，
    讓「改哪塊就回歸哪塊」不必每次自己想關鍵字。
    """
    if not keyword:
        stems = {Path(t).stem.removeprefix("test_").split("_")[0] for t in tests}
        keyword = " or ".join(sorted(stems)) or "test"
    cmd = ["uv", "run", "python", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
           "-k", keyword]
    for path in DB_DEPENDENT_TESTS:
        cmd += ["--ignore", path]
    for path in DB_DEPENDENT_DESELECT:
        cmd += ["--deselect", path]
    result = run(cmd)
    tail = [line for line in (result.stdout or "").splitlines() if line.strip()][-1:]
    return result.returncode == 0, f"-k {keyword!r}：{tail[0] if tail else '（無輸出）'}"


def _radon_blocks(added: dict[str, set[int]]) -> tuple[dict, str]:
    """跑 radon 並回傳 {檔案: 函式區塊清單}；失敗時第二個值帶錯誤說明。

    抽出來的理由：把「選檔＋執行外部工具＋解析輸出」從判定邏輯裡拿掉，
    `check_complexity` 才只剩「逐函式判定」這一件事（本腳本自我驗證時
    它自己是 C(16)，超過本腳本要求的門檻）。
    """
    files = [key for key in added
             if key.endswith(".py") and not key.startswith("tests/")]
    if not files:
        return {}, "無 .py"
    result = run(["uv", "run", "--no-project", "--python", "3.12", "--with", "radon",
                  "radon", "cc", "--json", *files])
    try:
        return json.loads(result.stdout or "{}"), ""
    except json.JSONDecodeError:
        return {}, f"radon 輸出無法解析：{(result.stderr or '')[:200]}"


def _rank_touched_block(block: dict, added_rows: set[int], key: str) -> str | None:
    """本次動到的函式回傳「超標描述或空字串」，沒動到回 None。

    抽出來的理由：`check_complexity` 原本三層巢狀（檔案→函式→判定）達 C(16)，
    自己就超過本腳本要求的門檻。⚠ 規則對工具本身也成立，否則沒有說服力。
    """
    span = set(range(block.get("lineno", 0), block.get("endline", 0) + 1))
    if not added_rows & span:
        return None
    if str(block.get("rank", "A")) > COMPLEXITY_MAX_RANK:
        return (f"{key}:{block['lineno']} {block.get('name')} "
                f"- {block.get('rank')} ({block.get('complexity')})")
    return ""


def check_complexity(added: dict[str, set[int]]) -> tuple[bool, str]:
    """圈複雜度：只看**本次動到的函式**（函式行段與新增行有交集者）。

    🔴 2026-08-05 本腳本自我驗證時抓到的缺陷：原本列出動到的**檔案**裡所有
    C 級以上函式——`chart_runner` 本來就有 D(25)／D(22) 兩支既有函式，
    這道門檻於是永遠不會過，等於形同虛設。與 lint／coverage 同一個原則：
    **範圍要界定到本次新增的行**，否則量到的是專案歷史、不是這次的品質。
    ⚠ 用 radon 的 JSON 輸出（含 endline）才算得出函式行段；`-s` 純文字沒有結束行。
    """
    data, error = _radon_blocks(added)
    if error:
        return error != "無 .py", error
    over: list[str] = []
    touched = 0
    for path, blocks in data.items():
        key = _match_key(path, added)
        if not key or not isinstance(blocks, list):
            continue
        for block in blocks:
            verdict = _rank_touched_block(block, added[key], key)
            if verdict is None:
                continue  # 這支函式本次沒動到
            touched += 1
            if verdict:
                over.append(verdict)
    body = "\n".join(f"    {item}" for item in over[:20]) or "    （無）"
    return not over, (f"本次動到的函式 {touched} 支，超過 {COMPLEXITY_MAX_RANK} 級者 "
                      f"{len(over)} 支：\n{body}")


def main() -> int:
    if "--preset" in sys.argv[1:]:
        preset_args = resolve_preset_args(parse_args(sys.argv[1:]))
        rewritten = [
            sys.argv[0],
            "--base", preset_args.base,
            "--tests", *preset_args.tests,
            "--paths", *preset_args.paths,
            "--source", *preset_args.source,
            "-k", preset_args.regression_filter,
        ]
        if preset_args.regression:
            rewritten.append("--regression")
        if preset_args.skip:
            rewritten.extend(["--skip", *preset_args.skip])
        sys.argv = rewritten
    parser = argparse.ArgumentParser(description="模組驗證流程（AGENTS.md 同名章節）")
    parser.add_argument("--base", default="HEAD~1", help="比較基準（預設 HEAD~1）")
    parser.add_argument("--tests", nargs="+", required=True, help="要跑的測試檔")
    parser.add_argument("--paths", nargs="+", default=["backend/app"], help="靜態分析範圍")
    parser.add_argument("--source", nargs="+", default=["backend.app"],
                        help="覆蓋率來源；套件名或目錄路徑，可多個"
                             "（例：backend.app skills/patent-report-ppt/scripts）")
    parser.add_argument("--regression", action="store_true",
                        help="一併跑專案標準範圍回歸（排除依賴本機 postgres 的 13 支）")
    parser.add_argument("-k", dest="regression_filter", default=None,
                        help="回歸的 -k 關鍵字（未給時以 --tests 的檔名推）")
    parser.add_argument("--skip", nargs="*", default=[],
                        help="跳過 tests／lint／coverage／complexity")
    args = parser.parse_args()

    added = added_lines(args.base)
    if not added:
        print(f"⚠ {args.base} 之後沒有變更，無從驗證")
        return 1

    # 關卡表：跑哪些、順序、要不要跑，都是資料——加關卡不必再多一條 if。
    # 關卡表：跑哪些、順序、要不要跑，都是資料——加關卡不必再多一條 if。
    # 回歸較慢，預設不跑（要跑給 --regression）。
    stages = [
        ("tests", "功能測試", lambda: check_tests(args.tests)),
        *([("regression", "範圍回歸",
            lambda: check_regression(args.tests, args.regression_filter))]
          if args.regression else []),
        ("lint", "靜態分析", lambda: check_lint(args.paths, added)),
        ("complexity", "圈複雜度", lambda: check_complexity(added)),
        ("coverage", "測試覆蓋", lambda: check_coverage(args.tests, args.source, added)),
    ]
    results = [(label, *fn()) for name, label, fn in stages if name not in args.skip]

    print("\n" + "=" * 60)
    for name, ok, detail in results:
        print(f"[{'通過' if ok else '未達標'}] {name}")
        print(detail)
    print("=" * 60)
    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print(f"未達標：{'、'.join(failed)}"
              f"（功能正確性與回歸測試請另依 AGENTS.md 執行並自行判讀）")
        return 1
    print("量測項全數達標（功能正確性與回歸測試請另依 AGENTS.md 執行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
